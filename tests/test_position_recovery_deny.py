"""Recovery ownership freeze: supervisor.recover_from_emergency is HARD-DENIED (ADR-035 INV-30)."""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import nexora.position.supervisor as module
import pytest
from nexora.autonomous_contracts import TRADE_STATE_TRANSITIONS, TradeState
from nexora.execution.reconciliation import ReconciliationFinding as F
from nexora.execution.reconciliation import ReconciliationRecord
from nexora.execution.recovery import (
    EmergencyRecoveryEvidence,
    RecoveryPlan,
    RecoveryPlanOutcome,
    plan_emergency_recovery,
)
from nexora.position.models import PositionInputError, PositionRecord, ProtectionLevels
from nexora.position.supervisor import (
    RECOVERY_AUTHORIZATION_NOT_RESOLVED,
    RecoveryAuthorizationNotResolvedError,
    recover_from_emergency,
)

NOW = datetime(2026, 10, 5, tzinfo=UTC)
D = Decimal


def _pos(state: TradeState = TradeState.EMERGENCY) -> PositionRecord:
    q = D(0) if state in (TradeState.EXIT_PENDING, TradeState.CLOSED) else D(1)
    return PositionRecord(
        position_id="pos:1",
        symbol="instrument:test",
        side="long",
        state=state,
        quantity=q,
        entry_price=D("100"),
        protection=ProtectionLevels(stop_price=D("90"), target_prices=(D("110"),)),
        opened_at=NOW,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def _evidence(records: tuple[ReconciliationRecord, ...] = ()) -> EmergencyRecoveryEvidence:
    return EmergencyRecoveryEvidence(
        position_ref="pos:1",
        records=records,
        snapshot_complete=True,
        evidence_ref="evidence:1",
        observed_at=NOW,
    )


OPERATOR_REFS = ("", "   ", "AUTHORIZED", "op:alice", "x" * 100_000)


def _deny(position: Any, evidence: Any, op: Any) -> RecoveryAuthorizationNotResolvedError:
    with pytest.raises(RecoveryAuthorizationNotResolvedError) as info:
        recover_from_emergency(position, evidence, operator_ref=op, recovered_at=NOW)
    return info.value


def test_recovery_denied_at_authorization_boundary_until_open20() -> None:
    for op in OPERATOR_REFS:
        err = _deny(_pos(), _evidence(), op)
        assert err.code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
        assert isinstance(err, PositionInputError)
    assert RECOVERY_AUTHORIZATION_NOT_RESOLVED == "recovery_authorization_not_resolved"


def test_nonblank_operator_ref_alone_does_not_authorize_recovery() -> None:
    blank = _deny(_pos(), _evidence(), "")
    for op in OPERATOR_REFS:
        err = _deny(_pos(), _evidence(), op)
        assert (type(err), err.code, str(err)) == (type(blank), blank.code, str(blank))


def test_recovery_has_no_timeout_reset() -> None:
    for at in (datetime(2099, 1, 1, tzinfo=UTC), datetime(1970, 1, 1, tzinfo=UTC)):
        with pytest.raises(RecoveryAuthorizationNotResolvedError):
            recover_from_emergency(_pos(), _evidence(), operator_ref="op:a", recovered_at=at)


def _confirming_records() -> dict[str, tuple[ReconciliationRecord, ...]]:
    def rec(f: F, bq: str | None) -> ReconciliationRecord:
        return ReconciliationRecord(
            position_ref="pos:1",
            finding=f,
            local_quantity=D("1"),
            broker_quantity=None if bq is None else D(bq),
            observed_at=NOW,
        )

    return {
        "MANAGING": (rec(F.MATCH, "1"),),
        "EXIT_PENDING": (rec(F.CLOSE_PENDING_CONFIRMED, "1"),),
        "CLOSED": (rec(F.BROKER_FLAT_CONFIRMED, None),),
        "REFUSED": (),
    }


def test_always_denied_for_every_position_plan_outcome_and_operator_ref() -> None:
    seen_outcomes = set()
    for records in _confirming_records().values():
        ev = _evidence(records)
        for op in OPERATOR_REFS:
            pos = _pos()
            plan = plan_emergency_recovery(pos, ev, operator_ref=op, planned_at=NOW)
            seen_outcomes.add(plan.outcome)
            snapshot = replace(pos)
            assert _deny(pos, ev, op).code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
            # a plan is no authorization either
            assert _deny(pos, plan, op).code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
            assert pos == snapshot and pos.state is TradeState.EMERGENCY
    assert seen_outcomes == {RecoveryPlanOutcome.DENIED, RecoveryPlanOutcome.REFUSED}


def test_hostile_plan_authorized_true_does_not_change_denial() -> None:
    class Hostile(RecoveryPlan):
        @property
        def authorized(self) -> bool:
            return True

    pos = _pos()
    base = plan_emergency_recovery(
        pos,
        _evidence(_confirming_records()["MANAGING"]),
        operator_ref="AUTHORIZED",
        planned_at=NOW,
    )
    hostile = Hostile(
        position_ref=base.position_ref,
        from_state=base.from_state,
        derived_target=base.derived_target,
        outcome=base.outcome,
        reason=base.reason,
        evidence_ref=base.evidence_ref,
        operator_ref=base.operator_ref,
        planned_at=base.planned_at,
    )
    assert hostile.authorized is True
    assert _deny(pos, hostile, "AUTHORIZED").code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
    assert pos.state is TradeState.EMERGENCY


def test_never_returns_a_position_record() -> None:
    for state in (
        TradeState.OPEN,
        TradeState.MANAGING,
        TradeState.EXIT_PENDING,
        TradeState.CLOSED,
        TradeState.EMERGENCY,
    ):
        assert _deny(_pos(state), _evidence(), "op").code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
    ann = inspect.signature(recover_from_emergency).return_annotation
    assert "PositionRecord" not in str(ann)
    assert "NoReturn" in str(ann)


def test_non_emergency_and_garbage_inputs_also_denied() -> None:
    for state in (TradeState.OPEN, TradeState.MANAGING, TradeState.EXIT_PENDING, TradeState.CLOSED):
        assert _deny(_pos(state), _evidence(), "op").code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
    assert _deny(None, None, "op").code == RECOVERY_AUTHORIZATION_NOT_RESOLVED
    assert _deny(object(), object(), None).code == RECOVERY_AUTHORIZATION_NOT_RESOLVED


def test_emergency_transition_table_unchanged() -> None:
    assert TRADE_STATE_TRANSITIONS[TradeState.EMERGENCY] == frozenset()


def test_no_override_parameter_names() -> None:
    banned = ("force", "override", "authoriz", "bypass", "skip", "enable", "target", "state")
    params = inspect.signature(recover_from_emergency).parameters
    assert set(params) == {"position", "evidence", "operator_ref", "recovered_at"}
    for name in params:
        assert not any(b in name.lower() for b in banned), name
    assert all(p.default is inspect.Parameter.empty for p in params.values())


def test_import_boundary_no_io_clock_broker_dedup() -> None:
    src = inspect.getsource(module)
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    banned = ("time", "os", "sys", "socket", "asyncio", "pathlib", "subprocess", "sqlite3", "io")
    for name in imported:
        assert name.split(".")[0] not in banned, name
        assert not name.startswith("nexora.execution"), name
        assert "broker" not in name and "dedup" not in name, name
    assert "now(" not in src and "utcnow" not in src
