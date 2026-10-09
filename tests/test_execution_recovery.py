"""PR-3: EMERGENCY recovery planning - authorization DISABLED / fail closed."""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import nexora.execution.recovery as module
import pytest
from nexora.autonomous_contracts import TRADE_STATE_TRANSITIONS, TradeState
from nexora.execution.reconciler import (
    BrokerPositionSnapshot,
    BrokerSnapshot,
    classify_reconciliation,
)
from nexora.execution.reconciliation import ReconciliationFinding as F
from nexora.execution.reconciliation import ReconciliationRecord
from nexora.execution.recovery import (
    RECOVERY_AUTHORIZATION_NOT_RESOLVED,
    EmergencyRecoveryAudit,
    EmergencyRecoveryEvidence,
    RecoveryPlan,
    RecoveryPlanOutcome,
    plan_emergency_recovery,
)
from nexora.position.models import PositionRecord, ProtectionLevels

from tests.execution_resolver_fixtures import identity_resolver

NOW = datetime(2026, 10, 3, tzinfo=UTC)
INSTR = "instrument:test"
RESOLVER = identity_resolver(INSTR)
D = Decimal


def _pos(
    state: TradeState = TradeState.EMERGENCY, pid: str = "pos:1", qty: str = "1"
) -> PositionRecord:
    q = D(0) if state in (TradeState.EXIT_PENDING, TradeState.CLOSED) else D(qty)
    return PositionRecord(
        position_id=pid,
        symbol=INSTR,
        side="long",
        state=state,
        quantity=q,
        entry_price=D("100"),
        protection=ProtectionLevels(stop_price=D("90"), target_prices=(D("110"),)),
        opened_at=NOW,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def _rec(
    finding: F,
    ref: str | None = "pos:1",
    lq: str | None = "1",
    bq: str | None = "1",
    at: datetime = NOW,
) -> ReconciliationRecord:
    return ReconciliationRecord(
        position_ref=ref,
        finding=finding,
        local_quantity=None if lq is None else D(lq),
        broker_quantity=None if bq is None else D(bq),
        observed_at=at,
    )


def _ev(
    records: tuple[ReconciliationRecord, ...],
    *,
    complete: bool = True,
    ref: str = "pos:1",
    at: datetime = NOW,
) -> EmergencyRecoveryEvidence:
    return EmergencyRecoveryEvidence(
        position_ref=ref,
        records=records,
        snapshot_complete=complete,
        evidence_ref="evidence:1",
        observed_at=at,
    )


MATCH = (_rec(F.MATCH),)
PENDING = (_rec(F.CLOSE_PENDING_CONFIRMED, bq="1"),)
FLAT = (_rec(F.BROKER_FLAT_CONFIRMED, bq=None),)


def _plan(
    ev: EmergencyRecoveryEvidence, pos: PositionRecord | None = None, op: str = "op:alice"
) -> RecoveryPlan:
    return plan_emergency_recovery(
        pos or _pos(), ev, operator_ref=op, planned_at=NOW + timedelta(days=30)
    )


# ------------------------------------------------------------ evidence -> target


@pytest.mark.parametrize(
    ("records", "target"),
    [
        (MATCH, TradeState.MANAGING),
        (PENDING, TradeState.EXIT_PENDING),
        (FLAT, TradeState.CLOSED),
    ],
)
def test_verified_evidence_derives_target_but_is_denied(
    records: tuple[ReconciliationRecord, ...], target: TradeState
) -> None:
    plan = _plan(_ev(records))
    assert plan.derived_target is target
    assert plan.outcome is RecoveryPlanOutcome.DENIED
    assert plan.reason == RECOVERY_AUTHORIZATION_NOT_RESOLVED
    assert plan.authorized is False
    assert plan.audit.authorized is False
    assert plan.from_state is TradeState.EMERGENCY
    assert "OPEN-20" in plan.unresolved_gates


_UNATTRIBUTED_UNKNOWN = _rec(F.EXECUTION_RESULT_UNKNOWN, ref=None, lq=None, bq=None)
_RESTART = _rec(F.RESTART_RECOVERY_PENDING, ref=None, lq=None, bq=None)
_FOREIGN = _rec(F.BROKER_POSITION_LOCAL_MISSING, ref="b:1", lq=None)
_OTHER_AT = _rec(F.MATCH, ref="pos:2", at=NOW + timedelta(seconds=1))

AMBIGUOUS: list[tuple[str, tuple[ReconciliationRecord, ...], bool]] = [
    ("match_incomplete_snapshot", MATCH, False),
    ("pending_incomplete_snapshot", PENDING, False),
    ("flat_incomplete_snapshot", FLAT, False),
    ("empty", (), True),
    ("quantity_mismatch", (_rec(F.QUANTITY_MISMATCH, bq="2"),), True),
    ("protection_mismatch", (_rec(F.PROTECTION_MISMATCH),), True),
    ("local_open_broker_missing", (_rec(F.LOCAL_OPEN_BROKER_MISSING, bq=None),), True),
    ("match_plus_mismatch", MATCH + (_rec(F.PROTECTION_MISMATCH),), True),
    ("two_confirming", MATCH + FLAT, True),
    ("match_zero_qty", (_rec(F.MATCH, lq="0", bq="0"),), True),
    ("foreign_broker_only", MATCH + (_FOREIGN,), True),
    ("unattributed_unknown", MATCH + (_UNATTRIBUTED_UNKNOWN,), True),
    ("restart_pending", MATCH + (_RESTART,), True),
    ("other_ref_only", (_rec(F.MATCH, ref="pos:2"),), True),
    ("mixed_observed_at", MATCH + (_OTHER_AT,), True),
]


@pytest.mark.parametrize(("name", "records", "complete"), AMBIGUOUS, ids=[a[0] for a in AMBIGUOUS])
def test_non_confirming_or_ambiguous_remains_emergency(
    name: str, records: tuple[ReconciliationRecord, ...], complete: bool
) -> None:
    pos = _pos()
    plan = _plan(_ev(records, complete=complete), pos)
    assert plan.derived_target is None
    assert plan.outcome is RecoveryPlanOutcome.REFUSED
    assert plan.authorized is False
    assert pos.state is TradeState.EMERGENCY


def test_stale_snapshot_quantity_differs_from_position_remains_emergency() -> None:
    plan = _plan(_ev((_rec(F.MATCH, lq="2", bq="2"),)), _pos(qty="1"))
    assert plan.derived_target is None
    assert plan.reason == "evidence_stale_vs_position"


def test_evidence_observed_at_mismatch_with_records_refused() -> None:
    plan = _plan(_ev(MATCH, at=NOW + timedelta(hours=1)))
    assert plan.derived_target is None


def test_evidence_for_other_position_refused() -> None:
    plan = _plan(_ev(MATCH, ref="pos:2"))
    assert plan.derived_target is None
    assert plan.reason == "evidence_position_mismatch"


@pytest.mark.parametrize(
    "state", [TradeState.OPEN, TradeState.MANAGING, TradeState.EXIT_PENDING, TradeState.CLOSED]
)
def test_non_emergency_position_refused(state: TradeState) -> None:
    plan = _plan(_ev(MATCH), _pos(state))
    assert plan.derived_target is None
    assert plan.outcome is RecoveryPlanOutcome.REFUSED
    assert plan.reason == "position_not_emergency"
    assert plan.authorized is False


def test_end_to_end_with_classifier_flat_and_pending() -> None:
    local = _pos()
    pending_snapshot = BrokerSnapshot(
        positions=(
            BrokerPositionSnapshot(
                broker_position_ref="b:1",
                instrument_id=INSTR,
                side="long",
                quantity=D("1"),
                nexora_position_ref="pos:1",
                close_pending=True,
            ),
        ),
        complete=True,
    )
    for snap, target in (
        (BrokerSnapshot(positions=(), complete=True), TradeState.CLOSED),
        (pending_snapshot, TradeState.EXIT_PENDING),
    ):
        recs = classify_reconciliation(
            local_positions=[local],
            broker_snapshot=snap,
            observed_at=NOW,
            instrument_resolver=RESOLVER,
        )
        plan = _plan(_ev(recs), local)
        assert plan.derived_target is target
        assert plan.authorized is False


# ------------------------------------------------------- operator_ref / timeout


@pytest.mark.parametrize("op", ["op:alice", "", "   ", "admin", "force", "authorized"])
@pytest.mark.parametrize("records", [MATCH, PENDING, FLAT, ()])
def test_operator_ref_never_changes_outcome_or_authorizes(
    op: str, records: tuple[ReconciliationRecord, ...]
) -> None:
    ev = _ev(records)
    base = _plan(ev, op="op:base")
    plan = _plan(ev, op=op)
    assert plan.authorized is False
    assert (plan.derived_target, plan.outcome, plan.reason) == (
        base.derived_target,
        base.outcome,
        base.reason,
    )
    assert plan.audit.operator_ref == op


def test_planned_at_age_has_no_effect() -> None:
    ev = _ev(MATCH)
    outcomes = {
        (p.derived_target, p.outcome, p.reason)
        for p in (
            plan_emergency_recovery(
                _pos(), ev, operator_ref="o", planned_at=NOW + timedelta(days=d)
            )
            for d in (0, 1, 365, 3650)
        )
    }
    assert len(outcomes) == 1


# --------------------------------------------------- authorization disabled


def test_plan_authorized_cannot_be_set_or_constructed() -> None:
    plan = _plan(_ev(MATCH))
    with pytest.raises((AttributeError, TypeError)):
        plan.authorized = True  # type: ignore[misc]
    assert "authorized" not in {f.name for f in fields(RecoveryPlan)}
    assert "authorized" not in {f.name for f in fields(EmergencyRecoveryAudit)}
    with pytest.raises(TypeError):
        EmergencyRecoveryAudit(authorized=True)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        RecoveryPlan(authorized=True, position_ref="pos:1")  # type: ignore[call-arg]
    forged = replace(plan, outcome=RecoveryPlanOutcome.DENIED)
    assert forged.authorized is False


def test_outcome_vocabulary_has_no_applied_or_authorized() -> None:
    assert {o.value for o in RecoveryPlanOutcome} == {"REFUSED", "DENIED"}


def test_public_api_has_no_authorization_override_parameter() -> None:
    banned = ("auth", "force", "override", "bypass", "enable", "allow", "target", "skip", "unsafe")
    checked = 0
    for name, obj in vars(module).items():
        if name.startswith("_") or not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        # Input side only: functions and the evidence input contract.
        if not (inspect.isfunction(obj) or name == "EmergencyRecoveryEvidence"):
            continue
        checked += 1
        for p in inspect.signature(obj).parameters:
            assert not any(b in p.lower() for b in banned), (name, p)
    assert checked >= 1


def test_module_exposes_no_function_returning_position_record() -> None:
    for name, obj in vars(module).items():
        if inspect.isfunction(obj) and obj.__module__ == module.__name__:
            ann = str(inspect.signature(obj).return_annotation)
            assert "PositionRecord" not in ann, name
    public = {n for n in vars(module) if not n.startswith("_")}
    assert not {n for n in public if "apply" in n or "recover_from" in n}
    assert not hasattr(module, "recover_from_emergency")


def test_no_state_changing_result_for_any_input_combination() -> None:
    pos = _pos()
    for records in (MATCH, PENDING, FLAT, ()):
        for complete in (True, False):
            for op in ("op", "", "force"):
                plan: Any = _plan(_ev(records, complete=complete), pos, op)
                assert plan.authorized is False
                assert not isinstance(plan, PositionRecord)
                assert plan.outcome in (RecoveryPlanOutcome.DENIED, RecoveryPlanOutcome.REFUSED)
    assert pos.state is TradeState.EMERGENCY


def test_emergency_transition_table_unchanged() -> None:
    assert TRADE_STATE_TRANSITIONS[TradeState.EMERGENCY] == frozenset()


# ------------------------------------------------------------------ purity


def test_inputs_not_mutated_and_deterministic() -> None:
    pos = _pos()
    ev = _ev(MATCH)
    pos_before, ev_before = replace(pos), replace(ev)
    a, b = _plan(ev, pos), _plan(ev, pos)
    assert a == b
    assert pos == pos_before
    assert ev == ev_before


def test_purity_import_boundary() -> None:
    tree = ast.parse(inspect.getsource(module))
    forbidden = {
        "MetaTrader5",
        "socket",
        "http",
        "urllib",
        "requests",
        "subprocess",
        "sqlite3",
        "os",
        "time",
        "random",
        "asyncio",
        "threading",
        "pathlib",
        "logging",
    }
    forbidden_nexora = (
        "nexora.storage",
        "nexora.autonomous.mt5",
        "nexora.execution.dedup_store",
        "nexora.execution.guard",
        "nexora.execution.preflight",
        "nexora.execution.broker_capabilities",
        "nexora.position.supervisor",
        "nexora.position.result_lifecycle",
    )
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for n in names:
            assert n.split(".")[0] not in forbidden, n
            assert not any(n.startswith(f) for f in forbidden_nexora), n
    src = inspect.getsource(module)
    assert "now(" not in src
    assert "utcnow" not in src
