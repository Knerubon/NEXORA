"""Tests for the pure reconciliation classifier (RECON-1, ADR-034 section 7)."""

from __future__ import annotations

import ast
import itertools
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.autonomous_contracts import TradeState
from nexora.execution import ExecutionResult, ExecutionStatus, reconciliation_blocks_new_trade
from nexora.execution.reconciler import (
    BrokerPositionSnapshot,
    BrokerSnapshot,
    ReconcilerInputError,
    aggregate_reconciliation_status,
    classify_reconciliation,
)
from nexora.execution.reconciliation import (
    ReconciliationFinding as F,
)
from nexora.execution.reconciliation import (
    ReconciliationRecord,
    ReconciliationStatus,
)
from nexora.position.models import PositionRecord, ProtectionLevels

from tests.execution_resolver_fixtures import identity_resolver

NOW = datetime(2026, 10, 3, tzinfo=UTC)
INSTR = "instrument:test"
RESOLVER = identity_resolver(INSTR)


def _local(
    pid: str = "pos:1",
    *,
    state: TradeState = TradeState.OPEN,
    quantity: str = "1",
    side: str = "long",
    stop: str = "90",
    targets: tuple[str, ...] = ("110",),
    symbol: str = INSTR,
) -> PositionRecord:
    qty = Decimal(quantity)
    if state in (TradeState.EXIT_PENDING, TradeState.CLOSED):
        qty = Decimal(0)
    return PositionRecord(
        position_id=pid,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        state=state,
        quantity=qty,
        entry_price=Decimal("100"),
        protection=ProtectionLevels(
            stop_price=Decimal(stop), target_prices=tuple(Decimal(t) for t in targets)
        ),
        opened_at=NOW,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def _broker(
    ref: str = "b:1",
    *,
    nexora: str | None = "pos:1",
    quantity: str = "1",
    side: str = "long",
    stop: str | None = "90",
    targets: tuple[str, ...] = ("110",),
    instrument: str = INSTR,
) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        broker_position_ref=ref,
        instrument_id=instrument,
        side=side,  # type: ignore[arg-type]
        quantity=Decimal(quantity),
        nexora_position_ref=nexora,
        stop_price=None if stop is None else Decimal(stop),
        target_prices=tuple(Decimal(t) for t in targets),
    )


def _classify(
    local: list[PositionRecord],
    broker: list[BrokerPositionSnapshot] | None,
    **kwargs: object,
) -> tuple[ReconciliationRecord, ...]:
    snap = None if broker is None else BrokerSnapshot(positions=tuple(broker))
    return classify_reconciliation(
        local_positions=local,
        broker_snapshot=snap,
        observed_at=NOW,
        instrument_resolver=RESOLVER,
        **kwargs,  # type: ignore[arg-type]
    )


def _findings(records: tuple[ReconciliationRecord, ...]) -> list[F]:
    return [r.finding for r in records]


def _unknown_result(rid: str = "res:1") -> ExecutionResult:
    return ExecutionResult(
        result_id=rid,
        request_ref="req:1",
        status=ExecutionStatus.UNKNOWN,
        requested_quantity=Decimal(1),
        observed_at=NOW,
    )


# --- each finding reachable -------------------------------------------------


def test_match() -> None:
    records = _classify([_local()], [_broker()])
    assert _findings(records) == [F.MATCH]
    assert records[0].status is ReconciliationStatus.SYNCHRONIZED
    assert records[0].position_ref == "pos:1"
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.SYNCHRONIZED
    assert not reconciliation_blocks_new_trade(aggregate_reconciliation_status(records))


def test_match_is_decimal_exact_not_textual() -> None:
    records = _classify(
        [_local(quantity="1", stop="90")],
        [_broker(quantity="1.00", stop="90.0", targets=("110.000",))],
    )
    assert _findings(records) == [F.MATCH]


def test_match_target_order_insensitive() -> None:
    records = _classify([_local(targets=("110", "120"))], [_broker(targets=("120", "110"))])
    assert _findings(records) == [F.MATCH]


def test_both_flat_is_explicit_match() -> None:
    records = _classify([], [])
    assert _findings(records) == [F.MATCH]
    assert records[0].position_ref is None
    records = _classify([_local(state=TradeState.CLOSED)], [])
    assert _findings(records) == [F.MATCH]


def test_local_open_broker_missing() -> None:
    for state in (
        TradeState.OPEN,
        TradeState.MANAGING,
        TradeState.EXIT_PENDING,
        TradeState.EMERGENCY,
    ):
        records = _classify([_local(state=state)], [])
        assert _findings(records) == [F.LOCAL_OPEN_BROKER_MISSING], state
        assert records[0].status is ReconciliationStatus.UNSYNCHRONIZED
        assert records[0].position_ref == "pos:1"


def test_broker_position_local_missing_unattributed() -> None:
    records = _classify([], [_broker(nexora=None)])
    assert _findings(records) == [F.BROKER_POSITION_LOCAL_MISSING]
    assert records[0].position_ref == "b:1"
    assert records[0].details_ref == "broker_only_not_nexora_owned"
    assert records[0].status is ReconciliationStatus.UNSYNCHRONIZED


def test_broker_claims_unknown_local_ref_is_still_broker_only() -> None:
    records = _classify([], [_broker(nexora="pos:ghost")])
    assert _findings(records) == [F.BROKER_POSITION_LOCAL_MISSING]


def test_broker_position_for_closed_local_is_reported() -> None:
    records = _classify([_local(state=TradeState.CLOSED)], [_broker()])
    assert _findings(records) == [F.BROKER_POSITION_LOCAL_MISSING]


def test_quantity_mismatch() -> None:
    records = _classify([_local(quantity="1")], [_broker(quantity="1.5")])
    assert _findings(records) == [F.QUANTITY_MISMATCH]
    assert records[0].local_quantity == Decimal(1)
    assert records[0].broker_quantity == Decimal("1.5")


def test_quantity_mismatch_tiny_difference_is_exact() -> None:
    records = _classify([_local(quantity="1")], [_broker(quantity="1.0000000000000000001")])
    assert _findings(records) == [F.QUANTITY_MISMATCH]


def test_protection_mismatch_stop_target_and_missing() -> None:
    assert _findings(_classify([_local()], [_broker(stop="91")])) == [F.PROTECTION_MISMATCH]
    assert _findings(_classify([_local()], [_broker(targets=("111",))])) == [F.PROTECTION_MISMATCH]
    assert _findings(_classify([_local()], [_broker(stop=None, targets=())])) == [
        F.PROTECTION_MISMATCH
    ]
    assert _findings(_classify([_local()], [_broker(targets=("110", "120"))])) == [
        F.PROTECTION_MISMATCH
    ]


def test_quantity_and_protection_mismatch_both_reported_no_match() -> None:
    records = _classify([_local()], [_broker(quantity="2", stop="80")])
    assert _findings(records) == [F.PROTECTION_MISMATCH, F.QUANTITY_MISMATCH]


def test_execution_result_unknown() -> None:
    records = _classify([_local()], [_broker()], execution_results=[_unknown_result()])
    assert F.EXECUTION_RESULT_UNKNOWN in _findings(records)
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.UNKNOWN


def test_determinate_execution_results_ignored() -> None:
    filled = ExecutionResult(
        result_id="res:2",
        request_ref="req:2",
        status=ExecutionStatus.FILLED,
        requested_quantity=Decimal(1),
        filled_quantity=Decimal(1),
        remaining_quantity=Decimal(0),
        observed_at=NOW,
    )
    records = _classify([_local()], [_broker()], execution_results=[filled])
    assert _findings(records) == [F.MATCH]


def test_restart_recovery_pending() -> None:
    records = _classify([_local()], [_broker()], restart_recovery_pending=True)
    assert F.RESTART_RECOVERY_PENDING in _findings(records)
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.UNKNOWN


# --- identity conflicts / fail closed ---------------------------------------


@pytest.mark.parametrize(
    "broker",
    [_broker(side="short"), _broker(instrument="instrument:other")],
)
def test_identity_conflict_reports_both_sides(broker: BrokerPositionSnapshot) -> None:
    records = _classify([_local()], [broker])
    assert sorted(f.value for f in _findings(records)) == [
        F.BROKER_POSITION_LOCAL_MISSING.value,
        F.LOCAL_OPEN_BROKER_MISSING.value,
    ]
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.UNSYNCHRONIZED


def test_two_broker_positions_claim_same_local_second_is_broker_only() -> None:
    records = _classify([_local()], [_broker("b:2"), _broker("b:1")])
    by_ref = {r.position_ref: r.finding for r in records}
    assert by_ref == {"pos:1": F.MATCH, "b:2": F.BROKER_POSITION_LOCAL_MISSING}


def test_no_snapshot_yields_no_comparison_and_aggregates_unknown() -> None:
    records = _classify([_local()], None)
    assert records == ()
    assert aggregate_reconciliation_status(records) is ReconciliationStatus.UNKNOWN
    assert reconciliation_blocks_new_trade(aggregate_reconciliation_status(records))


def test_aggregate_empty_and_foreign_items_fail_closed() -> None:
    assert aggregate_reconciliation_status([]) is ReconciliationStatus.UNKNOWN
    assert (
        aggregate_reconciliation_status(iter(()))  # empty iterator
        is ReconciliationStatus.UNKNOWN
    )
    assert (
        aggregate_reconciliation_status([object()])  # type: ignore[list-item]
        is ReconciliationStatus.UNKNOWN
    )


def test_aggregate_precedence() -> None:
    sync = _classify([_local()], [_broker()])
    unsync = _classify([_local()], [_broker(quantity="2")])
    unknown = _classify([], [], restart_recovery_pending=True)
    assert aggregate_reconciliation_status(sync + unsync) is ReconciliationStatus.UNSYNCHRONIZED
    assert aggregate_reconciliation_status(sync + unsync + unknown) is ReconciliationStatus.UNKNOWN
    assert aggregate_reconciliation_status(unknown + sync) is ReconciliationStatus.UNKNOWN


def test_one_mismatch_among_matches_blocks() -> None:
    local = [_local("pos:1"), _local("pos:2")]
    broker = [_broker("b:1", nexora="pos:1"), _broker("b:2", nexora="pos:2", quantity="3")]
    status = aggregate_reconciliation_status(_classify(local, broker))
    assert status is ReconciliationStatus.UNSYNCHRONIZED
    assert reconciliation_blocks_new_trade(status)


def test_duplicate_inputs_raise() -> None:
    with pytest.raises(ReconcilerInputError, match="duplicate_local_position_id"):
        _classify([_local(), _local()], [])
    with pytest.raises(ReconcilerInputError, match="duplicate_broker_position_ref"):
        _classify([], [_broker(), _broker()])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"broker_position_ref": " "},
        {"instrument_id": ""},
        {"quantity": Decimal(0)},
        {"quantity": Decimal("NaN")},
        {"stop_price": Decimal(0)},
        {"target_prices": (Decimal(-1),)},
        {"nexora_position_ref": " "},
    ],
)
def test_broker_snapshot_position_validation(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "broker_position_ref": "b:1",
        "instrument_id": INSTR,
        "side": "long",
        "quantity": Decimal(1),
    }
    base.update(kwargs)
    with pytest.raises(ReconcilerInputError):
        BrokerPositionSnapshot(**base)  # type: ignore[arg-type]


# --- determinism / purity ---------------------------------------------------


def test_shuffled_input_order_gives_identical_output() -> None:
    local = [
        _local("pos:1"),
        _local("pos:2", quantity="2"),
        _local("pos:3"),
        _local("pos:4", state=TradeState.CLOSED),
    ]
    broker = [
        _broker("b:1", nexora="pos:1"),
        _broker("b:2", nexora="pos:2", quantity="5"),
        _broker("b:9", nexora=None),
        _broker("b:8", nexora="pos:4"),
    ]
    results = [_unknown_result("res:b"), _unknown_result("res:a")]
    expected = _classify(local, broker, execution_results=results, restart_recovery_pending=True)
    for lp, bp, rp in itertools.product(
        itertools.permutations(local), itertools.permutations(broker), [results, results[::-1]]
    ):
        assert (
            _classify(list(lp), list(bp), execution_results=rp, restart_recovery_pending=True)
            == expected
        )


def test_inputs_not_mutated_and_repeatable() -> None:
    local = [_local()]
    broker = [_broker(quantity="2")]
    first = _classify(local, broker)
    assert _classify(local, broker) == first
    assert local == [_local()]
    assert broker == [_broker(quantity="2")]


def test_status_always_follows_frozen_table() -> None:
    local = [_local("pos:1"), _local("pos:2")]
    broker = [_broker("b:1", nexora="pos:1", quantity="9"), _broker("b:3", nexora=None)]
    records = _classify(
        local, broker, execution_results=[_unknown_result()], restart_recovery_pending=True
    )
    assert {r.finding for r in records} == {
        F.QUANTITY_MISMATCH,
        F.BROKER_POSITION_LOCAL_MISSING,
        F.LOCAL_OPEN_BROKER_MISSING,
        F.EXECUTION_RESULT_UNKNOWN,
        F.RESTART_RECOVERY_PENDING,
    }
    for r in records:
        expected = (
            ReconciliationStatus.UNKNOWN
            if r.finding in (F.EXECUTION_RESULT_UNKNOWN, F.RESTART_RECOVERY_PENDING)
            else ReconciliationStatus.UNSYNCHRONIZED
        )
        assert r.status is expected


def test_module_has_no_broker_network_or_db_imports() -> None:
    path = Path(__file__).resolve().parents[1] / "packages/nexora/execution/reconciler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = ("MetaTrader5", "socket", "requests", "httpx", "psycopg", "sqlalchemy", "asyncio")
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith(banned), name
