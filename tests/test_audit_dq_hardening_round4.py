"""Decision Audit hardening round 4 (ADR-036): credential prefixes in every string field kind.

Covers R3-1 from the narrow re-review of ``239f987``: the known-credential check covered only
``token`` / ``reference`` values, so caller references (``ref``) and free text (``text``) could
still persist AWS- or Stripe-style keys. Credential-shaped strings are synthetic.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from nexora.data_quality import MarketDataSnapshot
from nexora.decision_audit import AuditGateOutcome, DecisionAuditGate, DecisionAuditStore
from nexora.storage import SQLiteJournal

from tests.test_audit_dq_hardening_round2 import (
    ENV,
    _guard,
    _step,
    journal,  # noqa: F401  (pytest fixture)
)
from tests.test_audit_dq_hardening_round3 import _KNOWN_SHAPES

_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_STRIPE = "sk_" + "live_" + "a1B2c3D4e5F6g7H8"


def _record(store: SQLiteJournal, output: dict[str, Any], **refs: str) -> Any:
    event, _ = _step()
    gate = DecisionAuditGate(DecisionAuditStore(store, environment=ENV), _guard())
    return gate.record_decision(
        output=output,
        event=event,
        snapshot=MarketDataSnapshot((event,)),
        evaluated_at=event.received_at,
        **refs,
    )


def _assert_denied(result: Any, value: str) -> None:
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"
    assert result.new_trade_eligible is False
    assert value not in repr(result)


@pytest.mark.parametrize("field", ["lifecycle_ref", "instrument_id", "risk_authority_outcome"])
@pytest.mark.parametrize("value", [_AWS, _STRIPE])
def test_caller_references_reject_known_credentials(
    journal: SQLiteJournal,  # noqa: F811
    field: str,
    value: str,
) -> None:
    _, output = _step()
    _assert_denied(_record(journal, output, **{field: value}), value)


@pytest.mark.parametrize("value", _KNOWN_SHAPES)
def test_free_text_rejects_known_credentials(
    journal: SQLiteJournal,  # noqa: F811
    value: str,
) -> None:
    _, output = _step()
    broken = copy.deepcopy(output)
    broken["signals"]["decision"]["future_conditions"] = [f"wait for confirmation {value}"]
    _assert_denied(_record(journal, broken), value)


def test_clean_caller_references_and_text_still_record(
    journal: SQLiteJournal,  # noqa: F811
) -> None:
    _, output = _step()
    broken = copy.deepcopy(output)
    broken["signals"]["decision"]["future_conditions"] = ["ask live test of breakout"]
    result = _record(journal, broken, lifecycle_ref="lifecycle:42", instrument_id="xauusd-spot")
    assert result.outcome is AuditGateOutcome.RECORDED_ELIGIBLE
