"""Small execution hardening (Track 5): new_position_ref type, zero helpers, grid oracle,
and a characterization of the record_result late-writer race (no semantics added)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from nexora.autonomous import broker_capabilities as bc
from nexora.autonomous_contracts import TradeIntentKind
from nexora.execution.dedup_store import (
    DedupKeyState,
    JournalExecutionDedupStore,
)
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from nexora.storage import SQLiteJournal

NOW = datetime(2026, 1, 1, tzinfo=UTC)
D = Decimal


def _open_request(new_position_ref: Any) -> ExecutionRequest:
    return ExecutionRequest(
        request_id="req-1",
        idempotency_key="exec:OPEN:p1",
        intent_proposal_id="p1",
        origin_ref="origin-1",
        instrument_id="EURUSD",
        side="long",
        action=TradeIntentKind.OPEN,
        quantity=D("1"),
        new_position_ref=new_position_ref,
        created_at=NOW,
    )


# -- item 1: new_position_ref must be text (ADR-035 s4.1: `str | None`) -----------------


def test_new_position_ref_text_and_none_accepted() -> None:
    assert _open_request("pos:p1").new_position_ref == "pos:p1"
    assert _open_request(None).new_position_ref is None


@pytest.mark.parametrize("bad", [b"pos:p1", bytearray(b"pos:p1"), 7, ("pos:p1",), D("1")])
def test_new_position_ref_non_str_rejected(bad: object) -> None:
    with pytest.raises(ExecutionContractError, match="invalid_new_position_ref_type"):
        _open_request(bad)


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_new_position_ref_blank_still_rejected(blank: str) -> None:
    with pytest.raises(ExecutionContractError, match="blank_new_position_ref"):
        _open_request(blank)


# -- item 4: zero-input helpers are total and mathematically correct -------------------


@pytest.mark.parametrize("prime", [2, 5])
@pytest.mark.parametrize("need", [-3, 0, 1, 7, 10**6])
def test_divisible_by_prime_power_zero_is_divisible(prime: int, need: int) -> None:
    assert bc._divisible_by_prime_power(0, prime, need) is True


@pytest.mark.parametrize("prime", [2, 5])
def test_strip_prime_zero_terminates(prime: int) -> None:
    assert bc._strip_prime(0, prime) == (0, 0)


# -- item 3: seeded exact-Fraction oracle for the step-grid helper itself -------------


def _shape(rng: random.Random) -> Decimal:
    kind = rng.randrange(7)
    exp = rng.randint(-25, 25)
    if kind == 0:
        coeff = 0
    elif kind == 1:
        coeff = 2 ** rng.randint(0, 60)
    elif kind == 2:
        coeff = 5 ** rng.randint(0, 40)
    elif kind == 3:
        coeff = 10 ** rng.randint(0, 40)
    elif kind == 4:
        coeff = 2 ** rng.randint(0, 20) * 5 ** rng.randint(0, 20) * rng.choice([1, 3, 7])
    else:
        coeff = rng.randint(1, 10 ** rng.randint(1, 15))
    return D(f"{rng.choice([-1, 1]) * coeff}E{exp}")


def test_on_step_grid_seeded_exact_oracle() -> None:
    rng = random.Random(20261006)
    disagreements: list[tuple[Decimal, Decimal, Decimal]] = []
    for i in range(6000):
        step = abs(_shape(rng))
        if step == 0:
            step = D("0.01")
        anchor = _shape(rng)
        if i % 2:  # steer toward the integral side: quantity = anchor + k * step
            q = anchor + step * rng.randint(-40, 40)
            if rng.random() < 0.25:  # equivalent representation of the same value
                sign, digits, exp = q.as_tuple()
                q = D((sign, (*digits, 0, 0), int(exp) - 2))
        else:
            q = _shape(rng)
        expected = ((Fraction(q) - Fraction(anchor)) / Fraction(step)).denominator == 1
        if bc._on_step_grid(q, anchor, step) is not expected:
            disagreements.append((q, anchor, step))
    assert disagreements == []


# -- item 2: CHARACTERIZATION (not a fix) of record_result's missing expected_count ----


class _RacingJournal:
    """Delegating journal that runs a hook once, right before the first result append."""

    backend = "racing"

    def __init__(self, inner: SQLiteJournal) -> None:
        self.inner = inner
        self.hook: Any = None

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        if self.hook is not None and key.startswith("result#"):
            hook, self.hook = self.hook, None
            hook()
        return self.inner.append(stream, key, payload, expected_count=expected_count)

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        return self.inner.read(stream)

    def iter_read(self, stream: str) -> Any:
        yield from self.read(stream)

    def close(self) -> None:
        self.inner.close()


def _result(status: ExecutionStatus, rid: str, fill: str = "0") -> ExecutionResult:
    qty, filled = D("1.0"), D(fill)
    return ExecutionResult(
        result_id=rid,
        request_ref="req-1",
        status=status,
        requested_quantity=qty,
        filled_quantity=filled,
        remaining_quantity=qty - filled,
        reason_code="x" if status is ExecutionStatus.REJECTED else None,
        observed_at=NOW,
    )


def test_characterization_record_result_late_writer_quarantines_on_next_read(
    tmp_path: Path,
) -> None:
    key = "exec:CLOSE:prop-race"
    inner = SQLiteJournal(tmp_path / "dedup.sqlite")
    racing = _RacingJournal(inner)
    writer = JournalExecutionDedupStore(racing)
    releaser = JournalExecutionDedupStore(inner)
    attempt: dict[str, Any] = {
        "request_digest": "sha256:abc",
        "resolved_quantity": D("1"),
        "reconciliation_evidence_ref": "e",
        "preflight_decision_ref": "p",
        "written_at": NOW,
    }
    writer.claim(key)
    writer.record_attempt(key, **attempt)
    writer.record_result(key, _result(ExecutionStatus.REJECTED, "r1"))

    # Writer A has read (claimed, gen 0, clean) but not yet appended an UNSAFE result;
    # a second store instance releases the generation in that window.
    racing.hook = lambda: releaser.release_for_retry(key)
    writer.record_result(key, _result(ExecutionStatus.FILLED, "late", fill="1.0"))  # no error

    # Current behaviour: the late unsafe result lands AFTER the release and is only
    # detected on the next read; the key is quarantined (fail closed), never re-claimable.
    status = releaser.inspect(key)
    assert status.state is DedupKeyState.QUARANTINED
    assert status.violation_code == "dedup_released_generation_not_safe"
    inner.close()
