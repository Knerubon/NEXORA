"""Tests for the pure Execution Guard (ADR-034). No I/O, no broker, no runtime wiring."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import nexora.execution.guard as guard_module
import pytest
from nexora.autonomous.authority import AuthorityDecision, TradingConfig
from nexora.autonomous_contracts import (
    EntryOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradingMode,
)
from nexora.execution.guard import GuardDecision, evaluate_execution_guard
from nexora.execution.idempotency import build_execution_request
from nexora.execution.models import ProtectionRequest
from nexora.execution.reconciliation import ReconciliationStatus

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
ALLOW = AuthorityDecision(allowed=True, reason_codes=())
DENY = AuthorityDecision(allowed=False, reason_codes=("risk_decision_not_allow",))
NOT_TRANSMITTABLE = AuthorityDecision(allowed=False, reason_codes=("broker_unhealthy_fail_closed",))
ASSISTED = TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True)


def _open_intent() -> TradeIntent:
    return TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="SYM",
        side="long",
        origin=EntryOrigin(signal_decision_ref="sig-1", entry_readiness_ref="er-1"),
        proposal_id="p-open",
    )


def _close_intent() -> TradeIntent:
    return TradeIntent(
        kind=TradeIntentKind.CLOSE,
        symbol="SYM",
        side="long",
        origin=PositionOrigin(position_id="pos-1", exit_decision_ref="exit-1"),
        proposal_id="p-close",
    )


def _run(**overrides: Any) -> GuardDecision:
    intent = overrides.get("intent", _open_intent())
    params: dict[str, Any] = {
        "authority": ALLOW,
        "intent": intent,
        "reconciliation": ReconciliationStatus.SYNCHRONIZED,
        "config": ASSISTED,
        "kill_switch": False,
        "request_id": "req-1",
        "instrument_id": "INSTR",
        "created_at": NOW,
    }
    if intent is not None and intent.kind is TradeIntentKind.OPEN:
        params["quantity"] = Decimal("1")
    elif intent is not None:
        params["position_ref"] = "pos-1"
    params.update(overrides)
    return evaluate_execution_guard(**params)


def test_allowed_open_builds_request_via_factory() -> None:
    decision = _run()
    assert decision.allowed is True
    assert decision.reason_codes == ()
    expected = build_execution_request(
        _open_intent(),
        request_id="req-1",
        instrument_id="INSTR",
        created_at=NOW,
        quantity=Decimal("1"),
    )
    assert decision.request == expected


def test_allowed_close_builds_request() -> None:
    decision = _run(intent=_close_intent())
    assert decision.allowed is True
    assert decision.request is not None
    assert decision.request.action is TradeIntentKind.CLOSE


def test_deterministic_and_repeatable() -> None:
    assert _run() == _run()
    assert _run(authority=DENY) == _run(authority=DENY)


@pytest.mark.parametrize("intent_factory", [_open_intent, _close_intent])
def test_denied_authority_never_yields_request(intent_factory: Any) -> None:
    decision = _run(intent=intent_factory(), authority=DENY)
    assert decision.allowed is False
    assert decision.request is None
    assert "authority_denied" in decision.reason_codes


@pytest.mark.parametrize("intent_factory", [_open_intent, _close_intent])
def test_not_transmittable_blocks_all_intents(intent_factory: Any) -> None:
    decision = _run(intent=intent_factory(), authority=NOT_TRANSMITTABLE)
    assert decision.allowed is False
    assert decision.request is None
    assert decision.reason_codes == ("authority_not_transmittable",)


def test_guard_reads_typed_fields_not_reason_codes() -> None:
    # Denied authority whose reason text mimics the transmission code is still typed
    # DENIED/NOT_APPLICABLE only if the code is absent; here we assert the typed path.
    assert NOT_TRANSMITTABLE.reason_codes == ("broker_unhealthy_fail_closed",)
    assert _run(authority=NOT_TRANSMITTABLE).reason_codes == ("authority_not_transmittable",)
    assert _run(authority=DENY).reason_codes == ("authority_denied",)


@pytest.mark.parametrize("intent_factory", [_open_intent, _close_intent])
def test_auto_mode_never_yields_request(intent_factory: Any) -> None:
    for confirmed in (True, False):
        config = TradingConfig(mode=TradingMode.AUTO, assisted_confirmation=confirmed)
        decision = _run(intent=intent_factory(), config=config)
        assert decision.allowed is False
        assert decision.request is None
        assert "auto_mode_not_governed" in decision.reason_codes


def test_shadow_mode_never_yields_request() -> None:
    decision = _run(config=TradingConfig(mode=TradingMode.SHADOW))
    assert decision.allowed is False
    assert "trading_mode_shadow_observes_only" in decision.reason_codes


def test_assisted_open_requires_confirmation_but_close_does_not() -> None:
    unconfirmed = TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=False)
    open_decision = _run(config=unconfirmed)
    assert open_decision.allowed is False
    assert open_decision.reason_codes == ("assisted_confirmation_missing",)
    assert _run(intent=_close_intent(), config=unconfirmed).allowed is True


@pytest.mark.parametrize(
    "status", [ReconciliationStatus.UNSYNCHRONIZED, ReconciliationStatus.UNKNOWN]
)
def test_open_blocked_unless_synchronized(status: ReconciliationStatus) -> None:
    decision = _run(reconciliation=status)
    assert decision.allowed is False
    assert decision.request is None
    assert decision.reason_codes == (f"reconciliation_blocks_new_trade:{status.value}",)


NON_SYNC = [ReconciliationStatus.UNSYNCHRONIZED, ReconciliationStatus.UNKNOWN]


def _reduce_intent() -> TradeIntent:
    return TradeIntent(
        kind=TradeIntentKind.REDUCE,
        symbol="SYM",
        side="long",
        origin=PositionOrigin(position_id="pos-1", exit_decision_ref="exit-1"),
        proposal_id="p-reduce",
    )


def _modify_intent() -> TradeIntent:
    return TradeIntent(
        kind=TradeIntentKind.MODIFY_PROTECTION,
        symbol="SYM",
        side="long",
        origin=PositionOrigin(position_id="pos-1", exit_decision_ref="exit-1"),
        proposal_id="p-modify",
    )


def _position_kwargs(intent: TradeIntent) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"intent": intent, "position_ref": "pos-1"}
    if intent.kind is TradeIntentKind.REDUCE:
        kwargs["quantity"] = Decimal("1")
    if intent.kind is TradeIntentKind.MODIFY_PROTECTION:
        kwargs["protection"] = ProtectionRequest(stop_price=Decimal("9"))
        kwargs["protection_change"] = "TIGHTEN"
    return kwargs


def _position_run(factory: Any, **overrides: Any) -> GuardDecision:
    kwargs = _position_kwargs(factory())
    kwargs.update(overrides)
    return _run(**kwargs)


@pytest.mark.parametrize("status", NON_SYNC)
@pytest.mark.parametrize("factory", [_reduce_intent, _close_intent, _modify_intent])
def test_risk_reducing_requires_synchronized(factory: Any, status: ReconciliationStatus) -> None:
    decision = _position_run(factory, reconciliation=status)
    assert decision.allowed is False
    assert decision.request is None
    assert decision.reason_codes == (f"reconciliation_blocks_position_action:{status.value}",)


@pytest.mark.parametrize("factory", [_reduce_intent, _close_intent, _modify_intent])
def test_risk_reducing_allowed_when_synchronized(factory: Any) -> None:
    decision = _position_run(factory)
    assert decision.allowed is True
    assert decision.request is not None


def test_open_reason_code_unchanged_not_position_action() -> None:
    codes = _run(reconciliation=ReconciliationStatus.UNKNOWN).reason_codes
    assert codes == ("reconciliation_blocks_new_trade:UNKNOWN",)


@pytest.mark.parametrize("factory", [_reduce_intent, _close_intent, _modify_intent])
def test_risk_reducing_blocked_by_not_transmittable_and_reconciliation(factory: Any) -> None:
    decision = _position_run(
        factory, authority=NOT_TRANSMITTABLE, reconciliation=ReconciliationStatus.UNKNOWN
    )
    assert decision.allowed is False
    assert decision.reason_codes == (
        "authority_not_transmittable",
        "reconciliation_blocks_position_action:UNKNOWN",
    )


def test_guard_tighten_passes_kill_switch() -> None:
    decision = _position_run(_modify_intent, kill_switch=True, protection_change="TIGHTEN")
    assert decision.allowed is True
    assert decision.request is not None


def test_guard_widen_blocked_by_kill_switch() -> None:
    decision = _position_run(_modify_intent, kill_switch=True, protection_change="WIDEN")
    assert decision.allowed is False
    assert decision.request is None
    assert decision.reason_codes == ("kill_switch_blocks_protection_widen",)


def test_widen_without_kill_switch_not_blocked_by_g7() -> None:
    # ADR-035 s3.4 note 4 / OPEN-10: guard rule G7 only; no policy asserted beyond it.
    decision = _position_run(_modify_intent, kill_switch=False, protection_change="WIDEN")
    assert "kill_switch_blocks_protection_widen" not in decision.reason_codes
    assert decision.allowed is True


@pytest.mark.parametrize("kill_switch", [True, False])
@pytest.mark.parametrize("value", [None, "tighten", "widen", "UNKNOWN", "", 1, True, b"TIGHTEN"])
def test_guard_unclassified_protection_denied(kill_switch: bool, value: Any) -> None:
    decision = _position_run(_modify_intent, kill_switch=kill_switch, protection_change=value)
    assert decision.allowed is False
    assert decision.request is None
    assert decision.reason_codes == ("protection_change_unclassified",)


def test_modify_protection_requires_protection_change_by_default() -> None:
    kwargs = _position_kwargs(_modify_intent())
    del kwargs["protection_change"]
    assert _run(**kwargs).reason_codes == ("protection_change_unclassified",)


def test_kill_switch_does_not_block_reduce_or_close_g8() -> None:
    for factory in (_reduce_intent, _close_intent):
        assert _position_run(factory, kill_switch=True).allowed is True


def test_protection_change_not_consulted_for_other_kinds() -> None:
    # ADR-035 is silent on protection_change for non-MODIFY_PROTECTION kinds: no rule.
    for value in (None, "WIDEN", "garbage"):
        assert _run(protection_change=value).allowed is True
        assert _position_run(_close_intent, protection_change=value, kill_switch=True).allowed


def test_modify_protection_reasons_in_fixed_order() -> None:
    decision = _position_run(
        _modify_intent,
        authority=DENY,
        reconciliation=ReconciliationStatus.UNKNOWN,
        kill_switch=True,
        config=TradingConfig(mode=TradingMode.AUTO),
        protection_change=None,
    )
    assert decision.reason_codes == (
        "auto_mode_not_governed",
        "authority_denied",
        "reconciliation_blocks_position_action:UNKNOWN",
        "protection_change_unclassified",
    )
    widen = _position_run(
        _modify_intent,
        authority=DENY,
        reconciliation=ReconciliationStatus.UNSYNCHRONIZED,
        kill_switch=True,
        protection_change="WIDEN",
    )
    assert widen.reason_codes == (
        "authority_denied",
        "reconciliation_blocks_position_action:UNSYNCHRONIZED",
        "kill_switch_blocks_protection_widen",
    )


def test_invalid_reconciliation_for_position_action_fails_closed() -> None:
    decision = _position_run(_close_intent, reconciliation="SYNCHRONIZED")
    assert decision.reason_codes == ("invalid_reconciliation",)


def test_kill_switch_blocks_open_only() -> None:
    open_decision = _run(kill_switch=True)
    assert open_decision.allowed is False
    assert open_decision.reason_codes == ("kill_switch_armed",)
    assert _run(intent=_close_intent(), kill_switch=True).allowed is True


def test_multiple_reasons_reported_in_fixed_order() -> None:
    decision = _run(
        authority=DENY,
        reconciliation=ReconciliationStatus.UNKNOWN,
        kill_switch=True,
        config=TradingConfig(mode=TradingMode.AUTO),
    )
    assert decision.reason_codes == (
        "auto_mode_not_governed",
        "authority_denied",
        "reconciliation_blocks_new_trade:UNKNOWN",
        "kill_switch_armed",
    )


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("authority", "invalid_authority"),
        ("intent", "invalid_intent"),
        ("reconciliation", "invalid_reconciliation"),
        ("config", "invalid_trading_config"),
        ("kill_switch", "invalid_kill_switch"),
    ],
)
def test_missing_input_fails_closed(field: str, code: str) -> None:
    decision = _run(**{field: None})
    assert decision.allowed is False
    assert decision.request is None
    assert decision.reason_codes == (code,)


def test_wrongly_typed_inputs_fail_closed() -> None:
    assert _run(reconciliation="SYNCHRONIZED").reason_codes == ("invalid_reconciliation",)
    assert _run(kill_switch=0).reason_codes == ("invalid_kill_switch",)
    assert _run(authority=True).reason_codes == ("invalid_authority",)
    bad_mode = TradingConfig(mode="ASSISTED", assisted_confirmation=True)  # type: ignore[arg-type]
    assert _run(config=bad_mode).reason_codes == ("invalid_trading_config",)


def test_invalid_request_parameters_fail_closed() -> None:
    assert _run(quantity=Decimal("0")).reason_codes == ("execution_request_invalid",)
    assert _run(quantity=None).reason_codes == ("execution_request_invalid",)
    assert _run(instrument_id="  ").reason_codes == ("execution_request_invalid",)
    naive = datetime(2026, 10, 3, 12, 0)
    assert _run(created_at=naive).reason_codes == ("execution_request_invalid",)
    assert _run(intent=_close_intent(), position_ref=None).reason_codes == (
        "execution_request_invalid",
    )


def test_guard_decision_invariants() -> None:
    with pytest.raises(ValueError):
        GuardDecision(allowed=True, reason_codes=())
    with pytest.raises(ValueError):
        GuardDecision(allowed=False, reason_codes=())
    request = _run().request
    with pytest.raises(ValueError):
        GuardDecision(allowed=False, reason_codes=("x",), request=request)
    with pytest.raises(ValueError):
        GuardDecision(allowed=True, reason_codes=("x",), request=request)


def test_guard_module_has_no_forbidden_imports() -> None:
    tree = ast.parse(Path(guard_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = (
        "MetaTrader5",
        "socket",
        "requests",
        "httpx",
        "sqlite3",
        "psycopg",
        "nexora.patterns",
        "nexora.signals",
        "nexora.features",
        "nexora.research",
        "nexora.mt5",
        "nexora.broker",
    )
    for name in imported:
        assert not name.startswith(forbidden), name
    assert all(
        name.startswith(("nexora.autonomous", "nexora.execution", "dataclasses", "datetime"))
        or name in {"__future__", "decimal"}
        for name in imported
    )
