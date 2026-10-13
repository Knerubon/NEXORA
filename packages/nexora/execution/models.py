"""Broker-agnostic execution request/result contracts (ADR-034 sections 4-5).

Pure contract shapes only. No I/O, no MT5/broker import, no network call, no
broker-specific object, constant or default (no XAUUSD, no symbol suffix, no
digits/lot-step/filling-mode token). Broker-specific resolution happens
downstream, in BrokerCapabilities (packages/nexora/autonomous/broker_capabilities.py,
ADR-033 section 14) and a future BrokerExecutionAdapter — never here.

Nothing in this module is imported by research/pipeline.py, research/runtime.py,
checkpoint_state.py, apps/api, or apps/web. It documents and validates the
contract shapes themselves; it does not implement transmission, matching,
retry, or reconciliation behavior (see reconciliation.py for the reconciliation
contract and idempotency.py for the duplicate-order-safety contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

from nexora.autonomous.authority import ProtectionChange
from nexora.autonomous_contracts import TradeIntentKind

Side = Literal["long", "short"]


class ExecutionContractError(ValueError):
    """Sanitized execution-contract validation error (code only, no free text)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, kw_only=True)
class PriceConstraint:
    """Optional, broker-agnostic price constraint on an ``ExecutionRequest``.

    No filling-mode token, no broker-specific order-type constant — those are
    ``BrokerCapabilities``/adapter concerns (ADR-033 section 14), never a field
    here.
    """

    limit_price: Decimal | None = None
    max_slippage: Decimal | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("limit_price", self.limit_price),
            ("max_slippage", self.max_slippage),
        ):
            if value is not None and (not value.is_finite() or value < 0):
                raise ExecutionContractError(f"invalid_{name}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectionRequest:
    """Broker-agnostic stop-loss/take-profit change payload for an
    ``ExecutionRequest`` whose ``action`` is ``MODIFY_PROTECTION`` (or whose
    OPEN/REDUCE also sets initial/updated protection).
    """

    stop_price: Decimal | None = None
    target_prices: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        if self.stop_price is not None and (
            not self.stop_price.is_finite() or self.stop_price <= 0
        ):
            raise ExecutionContractError("invalid_stop_price")
        for target in self.target_prices:
            if not target.is_finite() or target <= 0:
                raise ExecutionContractError("invalid_target_price")
        if self.stop_price is None and not self.target_prices:
            raise ExecutionContractError("protection_request_requires_a_value")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionRequest:
    """Broker-agnostic execution request (ADR-034 section 4).

    No MT5-specific object. Instrument identity is the ADR-025 canonical
    ``instrument_id`` — never a broker symbol, suffix, digits, lot step,
    filling mode or broker name; those live only in ``BrokerCapabilities``
    and are resolved by a future adapter, not carried on this type.

    Prefer ``idempotency.build_execution_request()`` over constructing this
    directly from application code — the factory derives ``idempotency_key``
    and ``origin_ref`` deterministically from the originating ``TradeIntent``
    so they can never drift from it (ADR-034 section 6). Direct construction
    remains available for tests that need to exercise invalid combinations.
    """

    request_id: str
    idempotency_key: str
    intent_proposal_id: str
    origin_ref: str
    instrument_id: str
    side: Side
    action: TradeIntentKind
    quantity: Decimal | None = None
    price_constraint: PriceConstraint | None = None
    protection: ProtectionRequest | None = None
    position_ref: str | None = None
    new_position_ref: str | None = None
    created_at: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("request_id", self.request_id),
            ("idempotency_key", self.idempotency_key),
            ("intent_proposal_id", self.intent_proposal_id),
            ("origin_ref", self.origin_ref),
            ("instrument_id", self.instrument_id),
        ):
            if not value.strip():
                raise ExecutionContractError(f"missing_{name}")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ExecutionContractError("created_at_requires_timezone")
        if self.quantity is not None and not self.quantity.is_finite():
            raise ExecutionContractError("invalid_quantity")
        if self.position_ref is not None and not self.position_ref.strip():
            raise ExecutionContractError("blank_position_ref")
        if self.new_position_ref is not None:
            # ADR-035 s4.1 declares `new_position_ref: str | None`; bytes (which also
            # has .strip()) and every other non-str type is a contract violation.
            if type(self.new_position_ref) is not str:
                raise ExecutionContractError("invalid_new_position_ref_type")
            if not self.new_position_ref.strip():
                raise ExecutionContractError("blank_new_position_ref")
        # ADR-035 s4.1: new_position_ref is meaningful for OPEN only. For OPEN it is
        # required for transmission but type-level optional during migration.
        if self.action is not TradeIntentKind.OPEN and self.new_position_ref is not None:
            raise ExecutionContractError("new_position_ref_only_allowed_for_open")

        if self.action is TradeIntentKind.OPEN:
            if self.position_ref is not None:
                raise ExecutionContractError("open_must_not_reference_position")
            if self.quantity is None or self.quantity <= 0:
                raise ExecutionContractError("open_requires_positive_quantity")
        elif self.action is TradeIntentKind.REDUCE:
            if not self.position_ref:
                raise ExecutionContractError("reduce_requires_position_ref")
            if self.quantity is None or self.quantity <= 0:
                raise ExecutionContractError("reduce_requires_positive_quantity")
        elif self.action is TradeIntentKind.CLOSE:
            if not self.position_ref:
                raise ExecutionContractError("close_requires_position_ref")
            if self.quantity is not None and self.quantity <= 0:
                raise ExecutionContractError("close_quantity_must_be_positive_or_absent")
        elif self.action is TradeIntentKind.MODIFY_PROTECTION:
            if not self.position_ref:
                raise ExecutionContractError("modify_protection_requires_position_ref")
            if self.quantity is not None:
                raise ExecutionContractError("modify_protection_must_not_carry_quantity")
            if self.protection is None:
                raise ExecutionContractError("modify_protection_requires_protection_payload")
        else:  # pragma: no cover - TradeIntentKind is exhaustively handled above
            raise ExecutionContractError("unknown_execution_action")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedExecution:
    """ADR-035 s4.2: output of pipeline step 2 (resolution). Pure contract shape.

    Carries NO signal fields (INV-05): no ``signal_id``, ``signal_decision_ref``
    or ``entry_readiness_ref``. Type-level invariants only. The following need
    runtime evidence and are enforced by the pipeline (a later PR), NOT here:
    CLOSE ``resolved_quantity`` equals local AND broker-observed quantity;
    REDUCE ``0 < requested < resolved position quantity``; the position is
    ``OPEN``/``MANAGING`` and its instrument/side equal the intent's;
    ``protection_change`` is derived by step 2, never caller-asserted.
    """

    intent_proposal_id: str
    action: TradeIntentKind
    instrument_id: str
    side: Side
    nexora_position_ref: str
    resolved_quantity: Decimal | None
    protection_change: ProtectionChange | None
    reconciliation_evidence_ref: str
    reconciliation_observed_at: datetime
    resolved_at: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("intent_proposal_id", self.intent_proposal_id),
            ("instrument_id", self.instrument_id),
            ("nexora_position_ref", self.nexora_position_ref),
            ("reconciliation_evidence_ref", self.reconciliation_evidence_ref),
        ):
            if not value.strip():
                raise ExecutionContractError(f"missing_{name}")
        if self.side not in ("long", "short"):
            raise ExecutionContractError("invalid_side")
        for name, moment in (
            ("reconciliation_observed_at", self.reconciliation_observed_at),
            ("resolved_at", self.resolved_at),
        ):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ExecutionContractError(f"{name}_requires_timezone")

        if self.action is TradeIntentKind.MODIFY_PROTECTION:
            if self.resolved_quantity is not None:
                raise ExecutionContractError("modify_protection_must_not_carry_quantity")
            if self.protection_change not in ("TIGHTEN", "WIDEN"):
                raise ExecutionContractError("modify_protection_requires_protection_change")
            return
        if self.protection_change is not None:
            raise ExecutionContractError("protection_change_only_for_modify_protection")
        quantity = self.resolved_quantity
        if quantity is None or not quantity.is_finite() or quantity <= 0:
            raise ExecutionContractError("resolved_quantity_must_be_positive")


class ExecutionStatus(StrEnum):
    """ADR-034 section 5. Normalized, broker-independent outcome."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    UNKNOWN = "UNKNOWN"


# Statuses for which the *same logical* TradeIntent must never be blindly
# retried without reconciliation first (ADR-034 sections 5/6/9). A timeout or
# connection loss after transmission does not mean the broker never received
# the order, so it is UNKNOWN, never auto-REJECTED.
_UNSAFE_TO_RETRY_WITHOUT_RECONCILIATION = frozenset(
    {ExecutionStatus.ACCEPTED, ExecutionStatus.PARTIALLY_FILLED, ExecutionStatus.UNKNOWN}
)


# ADR-035 s4.3: a MODIFY_PROTECTION result is never a fill.
_MODIFY_PROTECTION_STATUSES = frozenset(
    {ExecutionStatus.ACCEPTED, ExecutionStatus.REJECTED, ExecutionStatus.UNKNOWN}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionResult:
    """ADR-034 section 5. Normalized, broker-independent execution outcome.

    ``status is UNKNOWN`` means the outcome is genuinely unresolved (e.g. a
    timeout/connection loss after transmission) — ``remaining_quantity`` is
    deliberately left unset in that case rather than guessed, because asserting
    a concrete value would silently claim knowledge reconciliation has not yet
    established (ADR-034 section 5/7).
    """

    result_id: str
    request_ref: str
    status: ExecutionStatus
    requested_quantity: Decimal | None
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal | None = None
    broker_order_ref: str | None = None
    broker_deal_ref: str | None = None
    execution_price: Decimal | None = None
    reason_code: str | None = None
    observed_at: datetime
    action: TradeIntentKind | None = None
    nexora_position_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.result_id.strip() or not self.request_ref.strip():
            raise ExecutionContractError("missing_execution_result_identity")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ExecutionContractError("observed_at_requires_timezone")
        if self.nexora_position_ref is not None and not self.nexora_position_ref.strip():
            raise ExecutionContractError("blank_nexora_position_ref")
        if not self.filled_quantity.is_finite() or self.filled_quantity < 0:
            raise ExecutionContractError("invalid_filled_quantity")

        if self.action is TradeIntentKind.MODIFY_PROTECTION:
            # ADR-035 s4.3: no quantity, never filled; ACCEPTED means acknowledged only.
            if self.requested_quantity is not None:
                raise ExecutionContractError("modify_protection_result_must_not_carry_quantity")
            if self.filled_quantity != 0:
                raise ExecutionContractError("modify_protection_result_must_have_zero_fill")
            if self.remaining_quantity is not None:
                raise ExecutionContractError("modify_protection_result_must_not_carry_remaining")
            if self.status not in _MODIFY_PROTECTION_STATUSES:
                raise ExecutionContractError("modify_protection_result_status_invalid")
            return

        # OPEN / REDUCE / CLOSE and legacy (action None) keep the quantity invariants.
        requested = self.requested_quantity
        if requested is None or not requested.is_finite() or requested <= 0:
            raise ExecutionContractError("invalid_requested_quantity")
        if self.filled_quantity > requested:
            raise ExecutionContractError("filled_quantity_exceeds_requested")

        if self.status is ExecutionStatus.UNKNOWN:
            if self.remaining_quantity is not None:
                raise ExecutionContractError("unknown_result_must_not_assert_remaining_quantity")
            return

        expected_remaining = requested - self.filled_quantity
        if self.remaining_quantity is None:
            raise ExecutionContractError("remaining_quantity_required_for_determinate_result")
        if self.remaining_quantity != expected_remaining:
            raise ExecutionContractError("remaining_quantity_inconsistent_with_fill")

        if self.status is ExecutionStatus.REJECTED and self.filled_quantity != 0:
            raise ExecutionContractError("rejected_result_must_have_zero_fill")
        if self.status is ExecutionStatus.FILLED and self.remaining_quantity != 0:
            raise ExecutionContractError("filled_result_requires_zero_remaining")
        if self.status is ExecutionStatus.PARTIALLY_FILLED and (
            self.filled_quantity <= 0 or self.remaining_quantity <= 0
        ):
            raise ExecutionContractError("partially_filled_requires_positive_fill_and_remaining")
        if self.status is ExecutionStatus.ACCEPTED and self.filled_quantity != 0:
            raise ExecutionContractError("accepted_result_must_not_assert_fill_yet")


def is_safe_to_retry_without_reconciliation(result: ExecutionResult) -> bool:
    """ADR-034 sections 5/6/9: True only for a clean, zero-fill ``REJECTED``.

    ``UNKNOWN``, ``ACCEPTED`` and ``PARTIALLY_FILLED`` must never be treated as
    a safe blind retry. ``FILLED`` is also False — "retrying" an already-filled
    result would create a duplicate execution, not a retry.
    """

    if result.status in _UNSAFE_TO_RETRY_WITHOUT_RECONCILIATION:
        return False
    if result.status is ExecutionStatus.FILLED:
        return False
    return result.status is ExecutionStatus.REJECTED and result.filled_quantity == 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionInstrumentBinding:
    """ADR-035 s5.2: the single ``execution_symbol -> instrument_id`` conversion
    record. Type only; no resolver behavior, legacy declaration source or format
    exists here (OPEN-9) and nothing is wired (PR-7).
    """

    mode: Literal["binding", "legacy"]
    execution_symbol: str
    instrument_id: str
    feed_id: str | None
    binding_ref: str

    def __post_init__(self) -> None:
        if self.mode not in ("binding", "legacy"):
            raise ExecutionContractError("invalid_binding_mode")
        for name, value in (
            ("execution_symbol", self.execution_symbol),
            ("instrument_id", self.instrument_id),
            ("binding_ref", self.binding_ref),
        ):
            if not value.strip():
                raise ExecutionContractError(f"missing_{name}")
        if self.mode == "legacy" and self.feed_id is not None:
            raise ExecutionContractError("legacy_binding_must_not_carry_feed_id")
        if self.feed_id is not None and not self.feed_id.strip():
            raise ExecutionContractError("blank_feed_id")


class ExecutionInstrumentResolver(Protocol):
    """ADR-035 s5.2 signature only. Exact, case-sensitive match on
    ``execution_symbol``; an implementation MUST fail closed (raise, reason
    ``execution_binding_missing``) when no declaration exists. No implementation
    ships in PR-1.
    """

    def resolve_execution_instrument(self, execution_symbol: str) -> ExecutionInstrumentBinding: ...
