"""CLOSE-ALL-OWNERSHIP-1: pure CLOSE ALL plan layer (ADR-034 section 8)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexora.autonomous import (
    AuthorityPolicyStatus,
    ExecutionTransmissibility,
    Health,
    SystemHealthSnapshot,
)
from nexora.autonomous_contracts import TradeIntentKind, TradeState
from nexora.execution.close_all_plan import (
    CloseAllPlan,
    ObservedExternalPositionRef,
    build_close_all_plan,
)
from nexora.execution.models import ExecutionContractError
from nexora.position.models import PositionRecord, ProtectionLevels

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
AXES = ("market_data", "broker", "account", "capabilities", "reconciliation", "journal")


def _health(**overrides: Health) -> SystemHealthSnapshot:
    axes = dict.fromkeys(AXES, Health.HEALTHY)
    axes.update(overrides)
    return SystemHealthSnapshot(observed_at=NOW, **axes)


def _pos(pid: str, symbol: str = "EURUSD", state: TradeState = TradeState.OPEN) -> PositionRecord:
    qty = Decimal("0") if state in (TradeState.CLOSED, TradeState.EXIT_PENDING) else Decimal("1")
    return PositionRecord(
        position_id=pid,
        symbol=symbol,
        side="long",
        state=state,
        quantity=qty,
        entry_price=Decimal("1.1"),
        protection=ProtectionLevels(stop_price=Decimal("1.0"), target_prices=(Decimal("1.2"),)),
        opened_at=NOW,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def _plan(positions, **kw) -> CloseAllPlan:  # type: ignore[no-untyped-def]
    kw.setdefault("health", _health())
    return build_close_all_plan(
        positions,
        operator_ref="operator:1",
        manual_request_id_prefix="closeall:1",
        requested_at=NOW,
        **kw,
    )


def test_plan_has_entry_per_owned_open_and_managing_position() -> None:
    plan = _plan([_pos("p1"), _pos("p2", state=TradeState.MANAGING)])
    assert [e.position_id for e in plan.entries] == ["p1", "p2"]
    assert all(e.intent.kind is TradeIntentKind.CLOSE for e in plan.entries)
    assert all(e.transmittable for e in plan.entries)
    assert plan.counts["owned_open"] == 2 and plan.counts["transmittable"] == 2
    assert plan.excluded_not_owned == ()


def test_external_positions_reported_never_intents() -> None:
    ext = (ObservedExternalPositionRef("broker:999", "EURUSD"), ObservedExternalPositionRef("b:2"))
    plan = _plan([_pos("p1")], external_positions=ext)
    assert plan.excluded_not_owned == ext
    assert len(plan.intents) == 1
    assert plan.counts["excluded_not_owned"] == 2
    assert all("broker:999" not in i.proposal_id for i in plan.intents)


def test_only_external_positions_yields_empty_plan() -> None:
    plan = _plan([], external_positions=[ObservedExternalPositionRef("broker:1")])
    assert plan.entries == () and plan.intents == ()
    assert len(plan.excluded_not_owned) == 1


def test_empty_owned_set_yields_empty_plan() -> None:
    plan = _plan([])
    assert plan.entries == () and plan.counts["owned_open"] == 0


def test_non_open_states_are_excluded_and_reported() -> None:
    plan = _plan(
        [
            _pos("p1"),
            _pos("p2", state=TradeState.CLOSED),
            _pos("p3", state=TradeState.EXIT_PENDING),
            _pos("p4", state=TradeState.EMERGENCY),
        ]
    )
    assert [e.position_id for e in plan.entries] == ["p1"]
    assert plan.excluded_not_open == ("p2", "p3", "p4")


def test_symbol_scope_never_widens_ownership() -> None:
    plan = _plan(
        [_pos("p1", "EURUSD"), _pos("p2", "GBPUSD"), _pos("p3", "EURUSD", TradeState.CLOSED)],
        symbol="EURUSD",
    )
    assert [e.position_id for e in plan.entries] == ["p1"]
    assert plan.excluded_out_of_scope == ("p2",)
    assert plan.excluded_not_open == ("p3",)
    # scope on a symbol with no NEXORA position must not pull in anything
    assert _plan([_pos("p1", "EURUSD")], symbol="XAUUSD").entries == ()


def test_retry_is_byte_identical() -> None:
    positions = [_pos("p1"), _pos("p2")]
    a, b = _plan(positions), _plan(positions)
    assert a == b
    assert [e.idempotency_key for e in a.entries] == [e.idempotency_key for e in b.entries]
    assert [e.idempotency_key for e in a.entries] == [
        "exec:CLOSE:closeall:1:p1",
        "exec:CLOSE:closeall:1:p2",
    ]
    assert _plan(positions) != build_close_all_plan(
        positions,
        health=_health(),
        operator_ref="operator:1",
        manual_request_id_prefix="closeall:2",
        requested_at=NOW,
    )


def test_duplicate_position_ids_fail_closed() -> None:
    with pytest.raises(ExecutionContractError, match="duplicate_position_id"):
        _plan([_pos("p1"), _pos("p1", "GBPUSD")])
    with pytest.raises(ExecutionContractError, match="duplicate_position_id"):
        _plan([_pos("p1"), _pos("p1", state=TradeState.CLOSED)])


def test_duplicate_external_refs_fail_closed() -> None:
    ext = [ObservedExternalPositionRef("b:1"), ObservedExternalPositionRef("b:1")]
    with pytest.raises(ExecutionContractError, match="duplicate_external"):
        _plan([], external_positions=ext)


def test_blank_external_ref_rejected() -> None:
    with pytest.raises(ExecutionContractError):
        ObservedExternalPositionRef("  ")


def test_broker_unhealthy_is_authorized_but_not_transmittable_and_kept() -> None:
    plan = _plan([_pos("p1")], health=_health(broker=Health.UNHEALTHY))
    assert len(plan.entries) == 1
    entry = plan.entries[0]
    assert entry.authority.policy_status is AuthorityPolicyStatus.AUTHORIZED
    assert entry.authority.transmissibility is ExecutionTransmissibility.NOT_TRANSMITTABLE
    assert not entry.transmittable
    assert entry not in plan.transmittable_entries
    assert plan.authorized_not_transmittable_entries == (entry,)
    assert plan.denied_entries == ()
    assert plan.counts["authorized_not_transmittable"] == 1
    assert plan.counts["denied"] == 0
    assert plan.counts["transmittable"] == 0


# A policy-DENIED CLOSE cannot be produced: ExistingPositionAuthority denies CLOSE only via
# broker_unhealthy_fail_closed, which ADR-034 section 3 classifies as AUTHORIZED +
# NOT_TRANSMITTABLE. Hence denied_entries stays empty for CLOSE plans (asserted above).


def test_degraded_non_broker_still_authorizes_close() -> None:
    plan = _plan([_pos("p1")], health=_health(market_data=Health.STALE))
    assert plan.entries[0].transmittable


def test_health_override_per_position() -> None:
    plan = _plan(
        [_pos("p1"), _pos("p2")], health_by_position={"p2": _health(broker=Health.UNHEALTHY)}
    )
    assert [e.transmittable for e in plan.entries] == [True, False]
    assert plan.counts["denied"] == 0
    assert plan.counts["authorized_not_transmittable"] == 1
    with pytest.raises(ExecutionContractError, match="unknown_position"):
        _plan([_pos("p1")], health_by_position={"zzz": _health()})


def test_plan_is_frozen_and_makes_no_execution_request() -> None:
    plan = _plan([_pos("p1")])
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.entries = ()  # type: ignore[misc]
    assert not any("request" in f.name for f in dataclasses.fields(plan))


def test_module_imports_no_broker_network_db() -> None:
    import ast
    from pathlib import Path

    import nexora.execution.close_all_plan as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "collections", "dataclasses", "datetime", "nexora"}
    calls = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "open" not in calls
