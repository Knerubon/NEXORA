"""ADR-035 PR-1 contract-foundation tests (sections 4.1-4.3, 4.5, 5.2).

Pure contract shapes only. No I/O beyond an in-memory/SQLite dedup round trip,
no broker, no network. Runtime-evidence invariants (CLOSE quantity equals
local and broker quantity, REDUCE strictly below the resolved position
quantity, position OPEN/MANAGING) are pipeline responsibilities and are
deliberately NOT tested here as type-level behavior.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.autonomous.broker_capabilities import (
    REASON_VOLUME_ABOVE_MAX,
    REASON_VOLUME_BELOW_MIN,
    REASON_VOLUME_STEP_MISMATCH,
    BrokerCapabilities,
    validate_volume,
)
from nexora.autonomous_contracts import ManualOrigin, TradeIntent, TradeIntentKind
from nexora.execution import broker_adapter
from nexora.execution.dedup_store import (
    InMemoryExecutionDedupStore,
    JournalExecutionDedupStore,
    deserialize_result,
    serialize_result,
)
from nexora.execution.idempotency import (
    build_execution_request,
    derive_new_position_ref,
    execution_request_idempotency_key,
)
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionInstrumentBinding,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ProtectionRequest,
    ResolvedExecution,
    is_safe_to_retry_without_reconciliation,
)
from nexora.market_data.instruments import FeedBinding, InstrumentDefinition, PriceGrid
from nexora.storage import SQLiteJournal

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
K = TradeIntentKind


# ------------------------------------------------------------------ helpers


def _request(action: TradeIntentKind, **overrides: object) -> ExecutionRequest:
    fields: dict[str, object] = {
        "request_id": "req-1",
        "idempotency_key": f"exec:{action.value}:p-1",
        "intent_proposal_id": "p-1",
        "origin_ref": "origin-1",
        "instrument_id": "inst-1",
        "side": "long",
        "action": action,
        "created_at": NOW,
    }
    if action is K.OPEN:
        fields["quantity"] = Decimal("1")
    elif action in (K.REDUCE, K.CLOSE):
        fields["position_ref"] = "pos-1"
        fields["quantity"] = Decimal("1")
    else:
        fields["position_ref"] = "pos-1"
        fields["protection"] = ProtectionRequest(stop_price=Decimal("1.0"))
    fields.update(overrides)
    return ExecutionRequest(**fields)  # type: ignore[arg-type]


def _intent(kind: TradeIntentKind = K.OPEN, proposal_id: str = "p-1") -> TradeIntent:
    origin = ManualOrigin(
        operator_ref="op:1",
        manual_request_id="m-1",
        requested_at=NOW,
        position_id=None if kind is K.OPEN else "pos-1",
    )
    return TradeIntent(
        kind=kind,
        symbol="EURUSD",
        side="long",
        origin=origin,
        proposal_id=proposal_id,
    )


def _result(**overrides: object) -> ExecutionResult:
    fields: dict[str, object] = {
        "result_id": "res-1",
        "request_ref": "req-1",
        "status": ExecutionStatus.FILLED,
        "requested_quantity": Decimal("2"),
        "filled_quantity": Decimal("2"),
        "remaining_quantity": Decimal("0"),
        "observed_at": NOW,
    }
    fields.update(overrides)
    return ExecutionResult(**fields)  # type: ignore[arg-type]


def _resolved(**overrides: object) -> ResolvedExecution:
    fields: dict[str, object] = {
        "intent_proposal_id": "p-1",
        "action": K.CLOSE,
        "instrument_id": "inst-1",
        "side": "long",
        "nexora_position_ref": "pos-1",
        "resolved_quantity": Decimal("1"),
        "protection_change": None,
        "reconciliation_evidence_ref": "ev-1",
        "reconciliation_observed_at": NOW,
        "resolved_at": NOW,
    }
    fields.update(overrides)
    return ResolvedExecution(**fields)  # type: ignore[arg-type]


def _caps(**overrides: object) -> BrokerCapabilities:
    fields: dict[str, object] = {
        "binding": FeedBinding(
            instrument_id="inst-1",
            broker_id="broker-a",
            symbol="SYM",
            price_grid=PriceGrid(digits=2, point=Decimal("0.01"), trade_tick_size=None),
            time_offset_seconds=0,
        ),
        "instrument": InstrumentDefinition(
            instrument_id="inst-1",
            currency_base="USD",
            currency_profit="USD",
            trade_calc_mode=0,
            trade_contract_size=Decimal("100"),
            chart_mode=0,
        ),
        "volume_min": Decimal("0.10"),
        "volume_max": Decimal("10"),
        "volume_step": Decimal("0.05"),
        "stops_level": Decimal("0"),
        "freeze_level": Decimal("0"),
        "filling_modes": ("FOK",),
        "execution_mode": "market",
        "session_policy_ref": "p:s",
        "spread_policy_ref": "p:sp",
        "margin_policy_ref": "p:m",
        "observed_at": NOW,
    }
    fields.update(overrides)
    return BrokerCapabilities(**fields)  # type: ignore[arg-type]


def _code(exc: pytest.ExceptionInfo[ExecutionContractError]) -> str:
    return exc.value.code


# ------------------------------------------------- 4.1 ExecutionRequest


def test_new_position_ref_defaults_to_none_and_open_accepts_it() -> None:
    assert _request(K.OPEN).new_position_ref is None  # type-level optional (migration)
    assert _request(K.OPEN, new_position_ref="pos:p-1").new_position_ref == "pos:p-1"


@pytest.mark.parametrize("action", [K.REDUCE, K.CLOSE, K.MODIFY_PROTECTION])
def test_new_position_ref_forbidden_for_non_open(action: TradeIntentKind) -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _request(action, new_position_ref="pos:x")
    assert _code(exc) == "new_position_ref_only_allowed_for_open"


def test_blank_new_position_ref_rejected() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _request(K.OPEN, new_position_ref="  ")
    assert _code(exc) == "blank_new_position_ref"


def test_open_still_forbids_position_ref_alongside_new_position_ref() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _request(K.OPEN, position_ref="pos-1", new_position_ref="pos:p-1")
    assert _code(exc) == "open_must_not_reference_position"


def test_existing_quantity_invariants_unchanged() -> None:
    with pytest.raises(ExecutionContractError):
        _request(K.OPEN, quantity=None)
    with pytest.raises(ExecutionContractError):
        _request(K.REDUCE, quantity=Decimal("0"))
    assert _request(K.CLOSE, quantity=None).quantity is None
    with pytest.raises(ExecutionContractError):
        _request(K.MODIFY_PROTECTION, quantity=Decimal("1"))


def test_build_execution_request_passes_new_position_ref_and_key_unchanged() -> None:
    intent = _intent(K.OPEN)
    ref = derive_new_position_ref(intent)
    request = build_execution_request(
        intent,
        request_id="r",
        instrument_id="inst-1",
        created_at=NOW,
        quantity=Decimal("1"),
        new_position_ref=ref,
    )
    assert request.new_position_ref == ref
    assert request.idempotency_key == "exec:OPEN:p-1"
    assert request.idempotency_key == execution_request_idempotency_key(intent)
    plain = build_execution_request(
        intent, request_id="r", instrument_id="inst-1", created_at=NOW, quantity=Decimal("1")
    )
    assert plain.new_position_ref is None
    assert plain.idempotency_key == request.idempotency_key


def test_derive_new_position_ref_is_deterministic_and_intent_scoped() -> None:
    a = derive_new_position_ref(_intent(K.OPEN, "p-1"))
    assert a == derive_new_position_ref(_intent(K.OPEN, "p-1"))
    assert a != derive_new_position_ref(_intent(K.OPEN, "p-2"))
    with pytest.raises(ExecutionContractError):
        derive_new_position_ref(_intent(K.CLOSE))


# ------------------------------------------------- 4.2 ResolvedExecution


def test_resolved_execution_has_no_signal_fields() -> None:
    names = {f.name for f in dataclasses.fields(ResolvedExecution)}
    assert names == {
        "intent_proposal_id",
        "action",
        "instrument_id",
        "side",
        "nexora_position_ref",
        "resolved_quantity",
        "protection_change",
        "reconciliation_evidence_ref",
        "reconciliation_observed_at",
        "resolved_at",
    }
    for forbidden in ("signal_id", "signal_decision_ref", "entry_readiness_ref"):
        assert forbidden not in names


@pytest.mark.parametrize("action", [K.OPEN, K.REDUCE, K.CLOSE])
def test_resolved_execution_quantity_actions(action: TradeIntentKind) -> None:
    assert _resolved(action=action).resolved_quantity == Decimal("1")
    for bad in (None, Decimal("0"), Decimal("-1"), Decimal("NaN")):
        with pytest.raises(ExecutionContractError) as exc:
            _resolved(action=action, resolved_quantity=bad)
        assert _code(exc) == "resolved_quantity_must_be_positive"


@pytest.mark.parametrize("change", ["TIGHTEN", "WIDEN"])
def test_resolved_execution_modify_protection(change: str) -> None:
    resolved = _resolved(
        action=K.MODIFY_PROTECTION, resolved_quantity=None, protection_change=change
    )
    assert resolved.resolved_quantity is None
    assert resolved.protection_change == change


def test_resolved_execution_modify_protection_invariants() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _resolved(
            action=K.MODIFY_PROTECTION, resolved_quantity=Decimal("1"), protection_change="TIGHTEN"
        )
    assert _code(exc) == "modify_protection_must_not_carry_quantity"
    for bad in (None, "SIDEWAYS"):
        with pytest.raises(ExecutionContractError) as exc:
            _resolved(action=K.MODIFY_PROTECTION, resolved_quantity=None, protection_change=bad)
        assert _code(exc) == "modify_protection_requires_protection_change"


@pytest.mark.parametrize("action", [K.OPEN, K.REDUCE, K.CLOSE])
def test_resolved_execution_protection_change_only_for_modify(action: TradeIntentKind) -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _resolved(action=action, protection_change="TIGHTEN")
    assert _code(exc) == "protection_change_only_for_modify_protection"


def test_resolved_execution_refs_timezones_and_side() -> None:
    for name in (
        "intent_proposal_id",
        "instrument_id",
        "nexora_position_ref",
        "reconciliation_evidence_ref",
    ):
        with pytest.raises(ExecutionContractError) as exc:
            _resolved(**{name: " "})
        assert _code(exc) == f"missing_{name}"
    naive = datetime(2026, 10, 4, 12, 0)
    for name in ("reconciliation_observed_at", "resolved_at"):
        with pytest.raises(ExecutionContractError) as exc:
            _resolved(**{name: naive})
        assert _code(exc) == f"{name}_requires_timezone"
    with pytest.raises(ExecutionContractError):
        _resolved(side="flat")


# ------------------------------------------------- 4.3 ExecutionResult


@pytest.mark.parametrize("action", [K.OPEN, K.REDUCE, K.CLOSE])
def test_execution_result_preserves_resolved_quantity(action: TradeIntentKind) -> None:
    result = _result(action=action, nexora_position_ref="pos-1")
    assert result.requested_quantity == Decimal("2")
    assert result.action is action
    assert result.nexora_position_ref == "pos-1"
    with pytest.raises(ExecutionContractError):
        _result(action=action, requested_quantity=None)
    with pytest.raises(ExecutionContractError):
        _result(action=action, requested_quantity=Decimal("0"))


@pytest.mark.parametrize("action", [K.OPEN, K.REDUCE, K.CLOSE, None])
def test_result_status_set_per_action_quantity_actions_allow_all_five(
    action: TradeIntentKind | None,
) -> None:
    for status, filled, remaining in (
        (ExecutionStatus.ACCEPTED, "0", "2"),
        (ExecutionStatus.REJECTED, "0", "2"),
        (ExecutionStatus.PARTIALLY_FILLED, "1", "1"),
        (ExecutionStatus.FILLED, "2", "0"),
        (ExecutionStatus.UNKNOWN, "0", None),
    ):
        result = _result(
            action=action,
            status=status,
            filled_quantity=Decimal(filled),
            remaining_quantity=None if remaining is None else Decimal(remaining),
        )
        assert result.status is status


def test_modify_protection_result_has_no_quantity() -> None:
    result = _result(
        action=K.MODIFY_PROTECTION,
        status=ExecutionStatus.ACCEPTED,
        requested_quantity=None,
        filled_quantity=Decimal("0"),
        remaining_quantity=None,
    )
    assert result.requested_quantity is None
    assert result.remaining_quantity is None
    base = {
        "action": K.MODIFY_PROTECTION,
        "status": ExecutionStatus.ACCEPTED,
        "requested_quantity": None,
        "filled_quantity": Decimal("0"),
        "remaining_quantity": None,
    }
    for override, code in (
        ({"requested_quantity": Decimal("1")}, "modify_protection_result_must_not_carry_quantity"),
        ({"filled_quantity": Decimal("1")}, "modify_protection_result_must_have_zero_fill"),
        (
            {"remaining_quantity": Decimal("0")},
            "modify_protection_result_must_not_carry_remaining",
        ),
    ):
        with pytest.raises(ExecutionContractError) as exc:
            _result(**{**base, **override})
        assert _code(exc) == code


def test_result_status_set_per_action() -> None:
    for status in (ExecutionStatus.ACCEPTED, ExecutionStatus.REJECTED, ExecutionStatus.UNKNOWN):
        result = _result(
            action=K.MODIFY_PROTECTION,
            status=status,
            requested_quantity=None,
            filled_quantity=Decimal("0"),
            remaining_quantity=None,
        )
        assert result.status is status
    for status in (ExecutionStatus.FILLED, ExecutionStatus.PARTIALLY_FILLED):
        with pytest.raises(ExecutionContractError) as exc:
            _result(
                action=K.MODIFY_PROTECTION,
                status=status,
                requested_quantity=None,
                filled_quantity=Decimal("0"),
                remaining_quantity=None,
            )
        assert _code(exc) == "modify_protection_result_status_invalid"


def test_legacy_result_behavior_unchanged() -> None:
    legacy = _result()
    assert legacy.action is None and legacy.nexora_position_ref is None
    with pytest.raises(ExecutionContractError) as exc:
        _result(requested_quantity=None)
    assert _code(exc) == "invalid_requested_quantity"
    with pytest.raises(ExecutionContractError) as exc:
        _result(filled_quantity=Decimal("3"), remaining_quantity=Decimal("0"))
    assert _code(exc) == "filled_quantity_exceeds_requested"
    with pytest.raises(ExecutionContractError):
        _result(remaining_quantity=Decimal("1"))


def test_blank_nexora_position_ref_rejected() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _result(action=K.CLOSE, nexora_position_ref=" ")
    assert _code(exc) == "blank_nexora_position_ref"


def test_retry_helper_unchanged_for_modify_protection_results() -> None:
    rejected = _result(
        action=K.MODIFY_PROTECTION,
        status=ExecutionStatus.REJECTED,
        requested_quantity=None,
        filled_quantity=Decimal("0"),
        remaining_quantity=None,
    )
    assert is_safe_to_retry_without_reconciliation(rejected) is True
    unknown = dataclasses.replace(rejected, status=ExecutionStatus.UNKNOWN)
    assert is_safe_to_retry_without_reconciliation(unknown) is False
    accepted = dataclasses.replace(rejected, status=ExecutionStatus.ACCEPTED)
    assert is_safe_to_retry_without_reconciliation(accepted) is False


# ------------------------------------------------- dedup serialization


def _roundtrip(result: ExecutionResult) -> ExecutionResult:
    return deserialize_result(serialize_result(result))


def test_dedup_roundtrip_modify_protection_result_without_quantity() -> None:
    result = _result(
        action=K.MODIFY_PROTECTION,
        status=ExecutionStatus.ACCEPTED,
        requested_quantity=None,
        filled_quantity=Decimal("0"),
        remaining_quantity=None,
        nexora_position_ref="pos-1",
    )
    assert _roundtrip(result) == result


@pytest.mark.parametrize("action", [K.OPEN, K.REDUCE, K.CLOSE, None])
def test_dedup_roundtrip_quantity_results_with_action_and_ref(
    action: TradeIntentKind | None,
) -> None:
    result = _result(action=action, nexora_position_ref=None if action is None else "pos-1")
    assert _roundtrip(result) == result


def test_dedup_deserializes_legacy_payload_without_new_keys() -> None:
    raw = serialize_result(_result())
    raw.pop("action")
    raw.pop("nexora_position_ref")
    assert deserialize_result(raw) == _result()


def test_dedup_rejects_legacy_payload_with_missing_requested_quantity() -> None:
    from nexora.execution.dedup_store import DedupStoreCorruptError

    raw = serialize_result(_result())
    raw["requested_quantity"] = None
    with pytest.raises(DedupStoreCorruptError):
        deserialize_result(raw)
    raw = serialize_result(_result(action=K.CLOSE))
    raw["action"] = "NOT_AN_ACTION"
    with pytest.raises(DedupStoreCorruptError):
        deserialize_result(raw)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_dedup_store_persists_widened_result(backend: str, tmp_path: Path) -> None:
    store: JournalExecutionDedupStore = (
        InMemoryExecutionDedupStore()
        if backend == "memory"
        else JournalExecutionDedupStore(SQLiteJournal(tmp_path / "d.sqlite"))
    )
    key = "exec:MODIFY_PROTECTION:p-9"
    store.claim(key)
    result = _result(
        action=K.MODIFY_PROTECTION,
        status=ExecutionStatus.UNKNOWN,
        requested_quantity=None,
        filled_quantity=Decimal("0"),
        remaining_quantity=None,
        nexora_position_ref="pos-1",
    )
    store.record_result(key, result)
    record = store.lookup(key)
    assert record is not None and record.latest_result == result


# ------------------------------------------------- 5.2 binding type


def _binding(**overrides: object) -> ExecutionInstrumentBinding:
    fields: dict[str, object] = {
        "mode": "binding",
        "execution_symbol": "inst-1",
        "instrument_id": "inst-1",
        "feed_id": "feed-1",
        "binding_ref": "decl:1",
    }
    fields.update(overrides)
    return ExecutionInstrumentBinding(**fields)  # type: ignore[arg-type]


def test_execution_instrument_binding_valid_modes() -> None:
    assert _binding().feed_id == "feed-1"
    assert _binding(mode="binding", feed_id=None).feed_id is None
    legacy = _binding(mode="legacy", execution_symbol="SYM.x", feed_id=None)
    assert legacy.mode == "legacy" and legacy.execution_symbol == "SYM.x"


def test_execution_instrument_binding_validation() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        _binding(mode="legacy", feed_id="feed-1")
    assert _code(exc) == "legacy_binding_must_not_carry_feed_id"
    with pytest.raises(ExecutionContractError) as exc:
        _binding(mode="other")
    assert _code(exc) == "invalid_binding_mode"
    for name in ("execution_symbol", "instrument_id", "binding_ref"):
        with pytest.raises(ExecutionContractError) as exc:
            _binding(**{name: "  "})
        assert _code(exc) == f"missing_{name}"
    with pytest.raises(ExecutionContractError) as exc:
        _binding(feed_id=" ")
    assert _code(exc) == "blank_feed_id"


def test_execution_symbol_is_not_trimmed_or_normalized() -> None:
    assert _binding(execution_symbol=" Sym ").execution_symbol == " Sym "


# ------------------------------------------------- 4.5 volume


def test_validate_volume_anchor_defaults_to_volume_min() -> None:
    caps = _caps()  # min 0.10, step 0.05, no anchor
    assert caps.volume_step_anchor is None
    assert validate_volume(caps, Decimal("0.10")) is None
    assert validate_volume(caps, Decimal("0.15")) is None
    assert validate_volume(caps, Decimal("10")) is None
    assert validate_volume(caps, Decimal("0.12")) == REASON_VOLUME_STEP_MISMATCH


def test_validate_volume_honors_declared_anchor() -> None:
    caps = _caps(volume_step_anchor=Decimal("0.02"))
    assert validate_volume(caps, Decimal("0.12")) is None  # (0.12 - 0.02) % 0.05 == 0
    assert validate_volume(caps, Decimal("0.15")) == REASON_VOLUME_STEP_MISMATCH
    assert validate_volume(caps, Decimal("0.10")) == REASON_VOLUME_STEP_MISMATCH


def test_validate_volume_bounds_and_non_finite() -> None:
    caps = _caps()
    assert validate_volume(caps, Decimal("0.05")) == REASON_VOLUME_BELOW_MIN
    assert validate_volume(caps, Decimal("0")) == REASON_VOLUME_BELOW_MIN
    assert validate_volume(caps, Decimal("-1")) == REASON_VOLUME_BELOW_MIN
    assert validate_volume(caps, Decimal("10.05")) == REASON_VOLUME_ABOVE_MAX
    assert validate_volume(caps, Decimal("NaN")) is not None
    assert validate_volume(caps, Decimal("Infinity")) is not None


def test_volume_step_anchor_must_be_finite() -> None:
    for bad in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="invalid_volume_step_anchor"):
            _caps(volume_step_anchor=bad)


def test_volume_reason_codes_equal_broker_adapter_constants() -> None:
    assert REASON_VOLUME_BELOW_MIN == broker_adapter.REASON_VOLUME_BELOW_MIN
    assert REASON_VOLUME_ABOVE_MAX == broker_adapter.REASON_VOLUME_ABOVE_MAX
    assert REASON_VOLUME_STEP_MISMATCH == broker_adapter.REASON_VOLUME_STEP_MISMATCH


def test_validate_volume_matches_adapter_check_when_anchor_defaulted() -> None:
    caps = _caps(volume_min=Decimal("0.01"), volume_step=Decimal("0.01"))
    adapter = broker_adapter.SimulatedBrokerAdapter(
        mode=broker_adapter.SIMULATION_MODE, capabilities=caps
    )
    for q in ("0.005", "0.01", "0.015", "1", "10", "11"):
        assert validate_volume(caps, Decimal(q)) == adapter._volume_violation(Decimal(q))
