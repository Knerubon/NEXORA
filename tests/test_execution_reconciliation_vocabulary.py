"""Tests for ADR-035 PR-1b reconciliation vocabulary/evidence (section 4.6)."""

from __future__ import annotations

import ast
import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.autonomous_contracts import TradeState
from nexora.execution import reconciler as reconciler_mod
from nexora.execution.reconciler import (
    BrokerPositionSnapshot,
    BrokerSnapshot,
    aggregate_reconciliation_status,
    classify_reconciliation,
    derive_recovery_target,
)
from nexora.execution.reconciliation import (
    ReconciliationFinding as F,
)
from nexora.execution.reconciliation import (
    ReconciliationRecord,
    ReconciliationStatus,
    blocks_new_trade,
)
from nexora.position.models import PositionRecord, ProtectionLevels

from tests.execution_resolver_fixtures import identity_resolver

NOW = datetime(2026, 10, 5, tzinfo=UTC)
INSTR = "instrument:test"
RESOLVER = identity_resolver(INSTR)


def _local(
    pid: str = "pos:1",
    *,
    state: TradeState = TradeState.EMERGENCY,
    symbol: str = INSTR,
    side: str = "long",
) -> PositionRecord:
    qty = Decimal(0) if state in (TradeState.EXIT_PENDING, TradeState.CLOSED) else Decimal(1)
    return PositionRecord(
        position_id=pid,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        state=state,
        quantity=qty,
        entry_price=Decimal("100"),
        protection=ProtectionLevels(stop_price=Decimal("90"), target_prices=(Decimal("110"),)),
        opened_at=NOW,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def _broker(
    ref: str = "b:1",
    *,
    nexora: str | None = "pos:1",
    instrument: str = INSTR,
    side: str = "long",
    close_pending: bool = False,
) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        broker_position_ref=ref,
        instrument_id=instrument,
        side=side,  # type: ignore[arg-type]
        quantity=Decimal(1),
        nexora_position_ref=nexora,
        stop_price=Decimal("90"),
        target_prices=(Decimal("110"),),
        close_pending=close_pending,
    )


def _run(
    local: list[PositionRecord],
    broker: list[BrokerPositionSnapshot],
    *,
    complete: bool = True,
) -> tuple[ReconciliationRecord, ...]:
    return classify_reconciliation(
        local_positions=local,
        broker_snapshot=BrokerSnapshot(positions=tuple(broker), complete=complete),
        observed_at=NOW,
        instrument_resolver=RESOLVER,
    )


def _f(records: tuple[ReconciliationRecord, ...]) -> list[F]:
    return [r.finding for r in records]


# --- defaults fail closed ---------------------------------------------------


def test_defaults_fail_closed() -> None:
    assert BrokerSnapshot(positions=()).complete is False
    assert _broker().close_pending is False
    bare = BrokerPositionSnapshot(
        broker_position_ref="b",
        instrument_id="i",
        side="long",
        quantity=Decimal(1),
    )
    assert bare.close_pending is False


def test_status_derivation_and_position_ref_required() -> None:
    close_pending = ReconciliationRecord(
        position_ref="p", finding=F.CLOSE_PENDING_CONFIRMED, observed_at=NOW
    )
    flat = ReconciliationRecord(position_ref="p", finding=F.BROKER_FLAT_CONFIRMED, observed_at=NOW)
    assert close_pending.status is ReconciliationStatus.UNKNOWN
    assert flat.status is ReconciliationStatus.UNSYNCHRONIZED
    assert blocks_new_trade(close_pending.status)
    assert blocks_new_trade(flat.status)
    for finding in (F.CLOSE_PENDING_CONFIRMED, F.BROKER_FLAT_CONFIRMED):
        with pytest.raises(ValueError):
            ReconciliationRecord(position_ref=None, finding=finding, observed_at=NOW)


# --- BROKER_FLAT_CONFIRMED --------------------------------------------------


@pytest.mark.parametrize("state", [TradeState.EMERGENCY, TradeState.EXIT_PENDING, TradeState.OPEN])
def test_flat_confirmed_complete_empty_snapshot(state: TradeState) -> None:
    records = _run([_local(state=state)], [])
    assert _f(records) == [F.BROKER_FLAT_CONFIRMED]
    assert records[0].position_ref == "pos:1"
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.UNSYNCHRONIZED


def test_flat_confirmed_with_foreign_other_position() -> None:
    records = _run([_local()], [_broker("b:9", nexora="pos:other", instrument="instrument:x")])
    assert F.BROKER_FLAT_CONFIRMED in _f(records)


def test_incomplete_snapshot_never_flat() -> None:
    assert _f(_run([_local()], [], complete=False)) == [F.LOCAL_OPEN_BROKER_MISSING]


def test_closed_local_not_flat_confirmed() -> None:
    assert _f(_run([_local(state=TradeState.CLOSED)], [])) == [F.MATCH]


def test_symbol_only_unattributed_blocks_flat() -> None:
    records = _run([_local()], [_broker("b:1", nexora=None)])
    assert F.BROKER_FLAT_CONFIRMED not in _f(records)
    assert F.LOCAL_OPEN_BROKER_MISSING in _f(records)


def test_identity_conflict_not_flat() -> None:
    records = _run([_local()], [_broker(instrument="instrument:other")])
    assert F.BROKER_FLAT_CONFIRMED not in _f(records)
    assert set(_f(records)) == {F.LOCAL_OPEN_BROKER_MISSING, F.BROKER_POSITION_LOCAL_MISSING}
    assert F.BROKER_FLAT_CONFIRMED not in _f(_run([_local()], [_broker(side="short")]))


def test_duplicate_claim_never_flat() -> None:
    records = _run([_local()], [_broker("b:1"), _broker("b:2")])
    assert F.BROKER_FLAT_CONFIRMED not in _f(records)


# --- CLOSE_PENDING_CONFIRMED ------------------------------------------------


def test_close_pending_confirmed() -> None:
    records = _run([_local()], [_broker(close_pending=True)])
    assert _f(records) == [F.CLOSE_PENDING_CONFIRMED]
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.UNKNOWN


def test_close_pending_requires_complete() -> None:
    records = _run([_local()], [_broker(close_pending=True)], complete=False)
    assert _f(records) == [F.MATCH]


def test_close_pending_false_is_plain_match() -> None:
    assert _f(_run([_local()], [_broker()])) == [F.MATCH]


def test_close_pending_without_identity() -> None:
    records = _run([_local()], [_broker(nexora=None, close_pending=True)])
    assert F.CLOSE_PENDING_CONFIRMED not in _f(records)
    assert F.BROKER_POSITION_LOCAL_MISSING in _f(records)


def test_close_pending_identity_mismatch() -> None:
    for kwargs in ({"instrument": "instrument:other"}, {"side": "short"}):
        records = _run([_local()], [_broker(close_pending=True, **kwargs)])
        assert F.CLOSE_PENDING_CONFIRMED not in _f(records)
        assert F.BROKER_FLAT_CONFIRMED not in _f(records)


def test_close_pending_duplicate_claim_not_confirmed() -> None:
    records = _run(
        [_local()], [_broker("b:1", close_pending=True), _broker("b:2", close_pending=True)]
    )
    assert F.CLOSE_PENDING_CONFIRMED not in _f(records)
    assert F.BROKER_FLAT_CONFIRMED not in _f(records)


def test_naive_observed_at_rejected() -> None:
    with pytest.raises(ValueError):
        classify_reconciliation(
            local_positions=[_local()],
            broker_snapshot=BrokerSnapshot(positions=(), complete=True),
            observed_at=datetime(2026, 10, 5),  # noqa: DTZ001
            instrument_resolver=RESOLVER,
        )


# --- determinism / purity ---------------------------------------------------


def test_deterministic_and_order_independent() -> None:
    local = [_local("pos:1"), _local("pos:2")]
    broker = [_broker("b:1", nexora="pos:1", close_pending=True)]
    first = _run(local, broker)
    assert first == _run(local, broker)
    assert first == _run(list(reversed(local)), broker)
    assert _f(first) == [F.CLOSE_PENDING_CONFIRMED, F.BROKER_FLAT_CONFIRMED]


def test_inputs_not_mutated() -> None:
    local = [_local()]
    snap = BrokerSnapshot(positions=(_broker(close_pending=True),), complete=True)
    before = (dataclasses.asdict(local[0]), dataclasses.asdict(snap))
    classify_reconciliation(
        local_positions=local, broker_snapshot=snap, observed_at=NOW, instrument_resolver=RESOLVER
    )
    assert (dataclasses.asdict(local[0]), dataclasses.asdict(snap)) == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.complete = False  # type: ignore[misc]


# --- target derivation ------------------------------------------------------


def _rec(
    finding: F,
    ref: str | None = "pos:1",
    *,
    lq: str | None = "1",
    bq: str | None = "1",
    at: datetime = NOW,
) -> ReconciliationRecord:
    return ReconciliationRecord(
        position_ref=ref,
        finding=finding,
        local_quantity=None if lq is None else Decimal(lq),
        broker_quantity=None if bq is None else Decimal(bq),
        observed_at=at,
    )


def _target(records: list[ReconciliationRecord], *, complete: bool = True) -> TradeState | None:
    return derive_recovery_target(records, "pos:1", snapshot_complete=complete)


def test_target_table_confirming() -> None:
    assert _target([_rec(F.MATCH)]) is TradeState.MANAGING
    assert _target([_rec(F.CLOSE_PENDING_CONFIRMED)]) is TradeState.EXIT_PENDING
    assert _target([_rec(F.BROKER_FLAT_CONFIRMED, lq="0", bq=None)]) is TradeState.CLOSED


def test_target_end_to_end_from_classifier() -> None:
    assert _target(list(_run([_local()], [_broker()]))) is TradeState.MANAGING
    assert _target(list(_run([_local()], [_broker(close_pending=True)]))) is TradeState.EXIT_PENDING
    assert _target(list(_run([_local()], []))) is TradeState.CLOSED


@pytest.mark.parametrize(
    "records",
    [
        [],
        [_rec(F.LOCAL_OPEN_BROKER_MISSING, bq=None)],
        [_rec(F.BROKER_POSITION_LOCAL_MISSING, lq=None)],
        [_rec(F.QUANTITY_MISMATCH, bq="2")],
        [_rec(F.PROTECTION_MISMATCH)],
        [_rec(F.EXECUTION_RESULT_UNKNOWN, lq=None, bq=None)],
        [_rec(F.RESTART_RECOVERY_PENDING, lq=None, bq=None)],
        [_rec(F.MATCH), _rec(F.CLOSE_PENDING_CONFIRMED)],
        [_rec(F.CLOSE_PENDING_CONFIRMED), _rec(F.BROKER_FLAT_CONFIRMED)],
        [_rec(F.MATCH), _rec(F.QUANTITY_MISMATCH, bq="2")],
        [_rec(F.CLOSE_PENDING_CONFIRMED), _rec(F.PROTECTION_MISMATCH)],
        [_rec(F.MATCH, ref=None, lq="0", bq="0")],
        [_rec(F.MATCH), _rec(F.RESTART_RECOVERY_PENDING, ref=None, lq=None, bq=None)],
        [_rec(F.MATCH), _rec(F.BROKER_POSITION_LOCAL_MISSING, ref="b:9", lq=None)],
        [_rec(F.MATCH), _rec(F.MATCH, ref="pos:2", at=datetime(2026, 10, 6, tzinfo=UTC))],
        [_rec(F.MATCH, lq="1", bq=None)],
        [_rec(F.MATCH, lq="0", bq="0")],
    ],
)
def test_target_non_confirming_yields_none(records: list[ReconciliationRecord]) -> None:
    assert _target(records) is None


def test_target_incomplete_never_targets() -> None:
    for finding in (F.MATCH, F.CLOSE_PENDING_CONFIRMED, F.BROKER_FLAT_CONFIRMED):
        assert _target([_rec(finding)], complete=False) is None


def test_target_other_position_ref_is_not_identity() -> None:
    assert derive_recovery_target([_rec(F.MATCH)], "pos:2", snapshot_complete=True) is None


def test_target_non_record_items_fail_closed() -> None:
    assert _target([_rec(F.MATCH), "x"]) is None  # type: ignore[list-item]


# --- no authorization / no mutation -----------------------------------------


def test_no_authorization_or_mutation_surface() -> None:
    path = Path(reconciler_mod.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "nexora.position.state"
            assert "transition" not in node.module and "operator" not in node.module
        if isinstance(node, ast.FunctionDef):
            assert "authoriz" not in node.name and "recover_from" not in node.name
            args = {a.arg for a in node.args.args + node.args.kwonlyargs}
            assert "operator_ref" not in args
    names = {n.lower() for n in dir(reconciler_mod) if not n.startswith("_")}
    assert not {n for n in names if "authoriz" in n or "transition" in n}
    # the helper returns a plain target (TradeState | None), never a transition object
    assert derive_recovery_target([], "pos:1", snapshot_complete=True) is None
