"""Decision Audit / Data Quality Guard hardening round 3 (ADR-036): regression tests.

Covers the independent re-review of round 2 (``0318818``):

* N-1 - ``Decimal`` subclasses bypassed the price trust boundary (``isinstance`` vs ``type is``)
* N-2 - well-known credential shapes shorter than the opaque-run check were persisted
* N-I1 - the Guard did not type-check ``source_sequence``

Credential-shaped strings below are synthetic; none is a real credential.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from nexora.data_quality import models as m
from nexora.decision_audit import AuditGateOutcome
from nexora.storage import SQLiteJournal

from tests.test_audit_dq_hardening_round2 import (
    _bar,
    _codes,
    _decide,
    _evaluate,
    _step,
    _with_source_ref,
    journal,  # noqa: F401  (pytest fixture)
)
from tests.validation_fixtures import tick


class _HostileDecimal(D):
    """Claims every comparison it is asked, so a negative price looks positive and in range."""

    def __le__(self, other: object) -> bool:
        return False

    def __lt__(self, other: object) -> bool:
        return False

    def __ge__(self, other: object) -> bool:
        return True

    def __gt__(self, other: object) -> bool:
        return True


class _PlainDecimal(D):
    pass


# =============================================================================================
# N-1 - exactly Decimal, never a subclass
# =============================================================================================


@pytest.mark.parametrize("cls", [_HostileDecimal, _PlainDecimal])
@pytest.mark.parametrize(
    "field", ["price", "bid", "ask", "last", "open_price", "high", "low", "close"]
)
def test_decimal_subclass_price_fields_are_blocked(cls: type[D], field: str) -> None:
    event = _bar()
    changes: dict[str, Any] = {field: cls(str(getattr(event, field) or "100"))}
    verdict = _evaluate(replace(event, **changes))
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)


def test_hostile_decimal_price_never_reaches_recorded_eligible(tmp_path: Path) -> None:
    event, output = _step()
    forged = replace(
        event,
        price=_HostileDecimal("-5"),
        last=_HostileDecimal("-5"),
        price_source="last",
    )
    store = SQLiteJournal(tmp_path / "subclass.sqlite")
    try:
        result = _decide(store, forged, output)
    finally:
        store.close()
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY
    assert result.new_trade_eligible is False


def test_mid_price_requires_exact_decimal_bid_and_ask() -> None:
    event = replace(tick(1, "100"), price_source="mid", price=D("100.15"), bid=_PlainDecimal("100"))
    assert _evaluate(event).state == "blocked"


# =============================================================================================
# N-I1 - source_sequence must be an int
# =============================================================================================


@pytest.mark.parametrize("bad", [None, "7", 8.0, True, D("1")])
def test_non_int_source_sequence_is_blocked(bad: object) -> None:
    event = replace(tick(1, "100"), source_sequence=bad)  # type: ignore[arg-type]
    verdict = _evaluate(event)
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)
    assert m.GUARD_INTERNAL_ERROR not in _codes(verdict)


def test_float_sequences_cannot_hide_a_gap_or_raise() -> None:
    first = replace(tick(1, "100"), source_sequence=1.0)  # type: ignore[arg-type]
    second = replace(tick(2, "100.1"), source_sequence=1.5)  # type: ignore[arg-type]
    verdict = _evaluate(first, second)
    assert verdict.state == "blocked"
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)
    assert m.GUARD_INTERNAL_ERROR not in _codes(verdict)


# =============================================================================================
# N-2 - well-known credential shapes are rejected at any length
# =============================================================================================

_KNOWN_SHAPES = [
    "AKIA" + "ABCDEFGHIJKLMNOP",  # AWS access key id, 20 chars
    "ref:" + "ASIA" + "QRSTUVWXYZ234567",
    "sk_" + "live_" + "a1B2c3D4e5F6g7H8i9J0k1L2",  # Stripe-style, 32 chars
    "rk_" + "test_" + "a1B2c3D4e5F6",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0",
    "github_pat_" + "a1B2c3D4e5F6g7H8i9J0",
    "xoxb-" + "1234567890-abcdef",
    "glpat-" + "a1B2c3D4e5F6g7H8i9J0",
    "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r",
]


@pytest.mark.parametrize("value", _KNOWN_SHAPES)
def test_known_credential_shapes_are_rejected_and_not_echoed(
    journal: SQLiteJournal,  # noqa: F811
    value: str,
) -> None:
    event, output = _with_source_ref(value)
    result = _decide(journal, event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"
    assert result.new_trade_eligible is False
    assert value not in repr(result)


@pytest.mark.parametrize(
    "value",
    [
        "aligned_trendline_retest_pending:" + "ab" * 32,  # real engine reference shape
        "skeleton_live_test",  # prefix-like words inside an identifier are not credentials
        "task_akia_flag",
    ],
)
def test_ordinary_references_are_still_accepted(
    journal: SQLiteJournal,  # noqa: F811
    value: str,
) -> None:
    event, output = _with_source_ref(value)
    assert _decide(journal, event, output).outcome is AuditGateOutcome.RECORDED_ELIGIBLE
