"""BrokerExecutionAdapter interface and a simulated, non-transmitting adapter.

BROKER-EXEC-1 scope (ADR-034 sections 4, 5, 9). This module defines only:

* ``BrokerExecutionAdapter`` -- a broker-agnostic ``typing.Protocol``;
* ``SimulatedBrokerAdapter`` -- a deterministic in-process test double.

Nothing here can reach a real broker: no broker SDK import, no network client,
no wiring to apps/api, the Execution Guard, Risk or any UI. Binding a real
adapter to the Execution Guard remains blocked by governance until a dedicated
governance ADR narrowly lifts it (ADR-034 section 11; AGENTS.md section 0).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from nexora.autonomous.broker_capabilities import BrokerCapabilities
from nexora.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus

SIMULATION_MODE = "simulation"

# Normalized, broker-agnostic reason codes produced by the simulated adapter.
REASON_INSTRUMENT_NOT_SUPPORTED = "instrument_not_supported"
REASON_VOLUME_BELOW_MIN = "volume_below_min"
REASON_VOLUME_ABOVE_MAX = "volume_above_max"
REASON_VOLUME_STEP_MISMATCH = "volume_not_multiple_of_step"
REASON_TIMEOUT_AFTER_TRANSMISSION = "timeout_after_transmission"
REASON_SIMULATED_REJECTION = "simulated_rejection"


class BrokerAdapterError(RuntimeError):
    """Sanitized adapter error (code only, no free text)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class BrokerExecutionAdapter(Protocol):
    """Broker-agnostic execution adapter contract (ADR-034 section 9).

    Implementations MUST:

    * be idempotent per ``request.idempotency_key``: re-submitting a request
      with a key already seen returns the original result and never causes a
      second transmission or fill;
    * return ``ExecutionStatus.UNKNOWN`` -- never ``REJECTED`` -- when a
      timeout or connection loss happens AFTER transmission, because the broker
      may have received the order (see ``is_safe_to_retry_without_reconciliation``);
    * never accept broker symbols: ``request.instrument_id`` is the ADR-025
      canonical id and the adapter resolves it itself through its
      ``BrokerCapabilities``/binding;
    * enforce ``volume_min``/``volume_max``/``volume_step`` from
      ``BrokerCapabilities`` and reject violations with a normalized,
      broker-agnostic ``reason_code`` (never a broker retcode).
    """

    def capabilities(self) -> BrokerCapabilities:
        """Return the capabilities the adapter enforces."""
        ...

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        """Submit one request and return a normalized result."""
        ...


class SimulatedOutcome(StrEnum):
    """Scripted outcomes for ``SimulatedBrokerAdapter``."""

    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"  # simulated timeout after transmission -> UNKNOWN


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptedOutcome:
    """One scripted adapter outcome. ``fill_fraction`` applies to partial fills."""

    outcome: SimulatedOutcome
    fill_fraction: Decimal = Decimal("0.5")
    execution_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.fill_fraction < Decimal("1")):
            raise BrokerAdapterError("invalid_fill_fraction")
        if self.execution_price is not None and (
            not self.execution_price.is_finite() or self.execution_price <= 0
        ):
            raise BrokerAdapterError("invalid_execution_price")


class SimulatedBrokerAdapter:
    """Deterministic in-process SIMULATED adapter. Never transmits anything.

    Outcomes are consumed from an explicit script (default ``FILLED`` once the
    script is exhausted). Limitation: ``ExecutionResult`` requires a positive
    ``requested_quantity``, so requests without a quantity (CLOSE without
    quantity, MODIFY_PROTECTION) are refused with ``BrokerAdapterError``.
    """

    def __init__(
        self,
        *,
        mode: str,
        capabilities: BrokerCapabilities,
        script: Iterable[ScriptedOutcome] = (),
    ) -> None:
        if mode != SIMULATION_MODE:
            raise BrokerAdapterError("simulated_adapter_requires_simulation_mode")
        self._mode = mode
        self._capabilities = capabilities
        self._script: deque[ScriptedOutcome] = deque(script)
        self._results: dict[str, ExecutionResult] = {}
        self.simulated_fill_count = 0

    def capabilities(self) -> BrokerCapabilities:
        return self._capabilities

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        if self._mode != SIMULATION_MODE:
            raise BrokerAdapterError("simulated_adapter_requires_simulation_mode")
        cached = self._results.get(request.idempotency_key)
        if cached is not None:
            return cached
        if request.quantity is None:
            raise BrokerAdapterError("simulated_adapter_requires_quantity")
        result = self._decide(request, request.quantity)
        self._results[request.idempotency_key] = result
        return result

    def _result(
        self,
        request: ExecutionRequest,
        quantity: Decimal,
        status: ExecutionStatus,
        *,
        filled: Decimal = Decimal("0"),
        reason_code: str | None = None,
        price: Decimal | None = None,
        order_ref: str | None = None,
    ) -> ExecutionResult:
        unknown = status is ExecutionStatus.UNKNOWN
        return ExecutionResult(
            result_id=f"sim-result:{request.idempotency_key}",
            request_ref=request.request_id,
            status=status,
            requested_quantity=quantity,
            filled_quantity=filled,
            remaining_quantity=None if unknown else quantity - filled,
            broker_order_ref=order_ref,
            broker_deal_ref=f"sim-deal:{request.idempotency_key}" if filled > 0 else None,
            execution_price=price if filled > 0 else None,
            reason_code=reason_code,
            observed_at=request.created_at,
        )

    def _volume_violation(self, quantity: Decimal) -> str | None:
        caps = self._capabilities
        if quantity < caps.volume_min:
            return REASON_VOLUME_BELOW_MIN
        if quantity > caps.volume_max:
            return REASON_VOLUME_ABOVE_MAX
        if (quantity - caps.volume_min) % caps.volume_step != 0:
            return REASON_VOLUME_STEP_MISMATCH
        return None

    def _decide(self, request: ExecutionRequest, quantity: Decimal) -> ExecutionResult:
        caps = self._capabilities
        if request.instrument_id != caps.instrument.instrument_id:
            return self._result(
                request,
                quantity,
                ExecutionStatus.REJECTED,
                reason_code=REASON_INSTRUMENT_NOT_SUPPORTED,
            )
        volume_reason = self._volume_violation(quantity)
        if volume_reason is not None:
            return self._result(
                request, quantity, ExecutionStatus.REJECTED, reason_code=volume_reason
            )

        step = self._script.popleft() if self._script else None
        outcome = step.outcome if step else SimulatedOutcome.FILLED
        price = step.execution_price if step else None
        order_ref = f"sim-order:{request.idempotency_key}"

        if outcome is SimulatedOutcome.REJECTED:
            return self._result(
                request,
                quantity,
                ExecutionStatus.REJECTED,
                reason_code=REASON_SIMULATED_REJECTION,
            )
        if outcome is SimulatedOutcome.TIMEOUT:
            return self._result(
                request,
                quantity,
                ExecutionStatus.UNKNOWN,
                reason_code=REASON_TIMEOUT_AFTER_TRANSMISSION,
                order_ref=order_ref,
            )
        if outcome is SimulatedOutcome.ACCEPTED:
            return self._result(request, quantity, ExecutionStatus.ACCEPTED, order_ref=order_ref)
        if outcome is SimulatedOutcome.PARTIALLY_FILLED:
            assert step is not None
            raw = quantity * step.fill_fraction
            steps = ((raw - caps.volume_min) / caps.volume_step).to_integral_value(ROUND_DOWN)
            filled = caps.volume_min + steps * caps.volume_step
            if filled <= 0 or filled >= quantity:
                raise BrokerAdapterError("scripted_partial_fill_not_representable")
            self.simulated_fill_count += 1
            return self._result(
                request,
                quantity,
                ExecutionStatus.PARTIALLY_FILLED,
                filled=filled,
                price=price,
                order_ref=order_ref,
            )
        self.simulated_fill_count += 1
        return self._result(
            request,
            quantity,
            ExecutionStatus.FILLED,
            filled=quantity,
            price=price,
            order_ref=order_ref,
        )
