"""BrokerCapabilities: broker-agnostic capability model (ADR-033 section 14).

A sibling contract to ADR-025's InstrumentDefinition/FeedBinding, not a merge into
them. No broker name, symbol, lot, digits, stops or filling-mode assumption is
hard-coded anywhere in this module — every value is adapter-supplied data. Pure
contract shape only; no CapabilityProvider implementation, no discovery, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from nexora.market_data.instruments import FeedBinding, InstrumentDefinition

# Normalized volume reason codes (ADR-035 s4.5). Kept equal to the strings already
# used by execution/broker_adapter.py; a test pins the equality.
REASON_VOLUME_BELOW_MIN = "volume_below_min"
REASON_VOLUME_ABOVE_MAX = "volume_above_max"
REASON_VOLUME_STEP_MISMATCH = "volume_not_multiple_of_step"


@dataclass(frozen=True, slots=True, kw_only=True)
class BrokerCapabilities:
    """ADR-033 section 14. ``contract_size`` is read from ``instrument``, never
    duplicated as its own field, so it cannot drift from the bound InstrumentDefinition.
    """

    binding: FeedBinding
    instrument: InstrumentDefinition
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    stops_level: Decimal
    freeze_level: Decimal
    filling_modes: tuple[str, ...]
    execution_mode: str
    session_policy_ref: str
    spread_policy_ref: str
    margin_policy_ref: str
    observed_at: datetime
    # ADR-035 s4.5: None => the volume grid is anchored at volume_min.
    volume_step_anchor: Decimal | None = None

    def __post_init__(self) -> None:
        if self.volume_step_anchor is not None and not self.volume_step_anchor.is_finite():
            raise ValueError("invalid_volume_step_anchor")
        if self.instrument.instrument_id != self.binding.instrument_id:
            raise ValueError("broker_capabilities_instrument_binding_mismatch")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("broker_capabilities_observed_at_requires_timezone")
        for field_name, value in (
            ("volume_min", self.volume_min),
            ("volume_max", self.volume_max),
            ("volume_step", self.volume_step),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"invalid_{field_name}")
        if self.volume_max < self.volume_min:
            raise ValueError("volume_max_below_volume_min")
        for field_name, value in (
            ("stops_level", self.stops_level),
            ("freeze_level", self.freeze_level),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(f"invalid_{field_name}")
        if not self.filling_modes:
            raise ValueError("missing_filling_modes")
        for field_name, text in (
            ("execution_mode", self.execution_mode),
            ("session_policy_ref", self.session_policy_ref),
            ("spread_policy_ref", self.spread_policy_ref),
            ("margin_policy_ref", self.margin_policy_ref),
        ):
            if not text.strip():
                raise ValueError(f"missing_{field_name}")

    @property
    def contract_size(self) -> Decimal:
        return self.instrument.trade_contract_size


# Implementation-safety guard (not a trading policy and not a broker limit): exact
# integer-ratio arithmetic on a Decimal with an absurd exponent would allocate an
# enormous integer. Beyond this magnitude the step check fails closed instead.
_MAX_EXACT_EXPONENT_MAGNITUDE = 10_000


def _on_step_grid(quantity: Decimal, anchor: Decimal, step: Decimal) -> bool:
    """Exact test that ``(quantity - anchor) / step`` is an integer."""

    for value in (quantity, anchor, step):
        if abs(value.adjusted()) > _MAX_EXACT_EXPONENT_MAGNITUDE:
            return False
    ratio = (Fraction(quantity) - Fraction(anchor)) / Fraction(step)
    return ratio.denominator == 1


def validate_volume(capabilities: BrokerCapabilities, quantity: Decimal) -> str | None:
    """Shared pure, TOTAL volume validation (ADR-035 s4.5). Returns a reason code,
    or ``None`` when valid. Never raises.

    Valid iff ``volume_min <= q <= volume_max`` and ``(q - anchor) % volume_step
    == 0`` where ``anchor`` is ``volume_step_anchor`` if declared, else
    ``volume_min``. The step test uses exact rational arithmetic, so the Decimal
    context precision can neither raise nor mis-validate. A non-finite quantity,
    a non-Decimal quantity (including ``bool``), a non-``BrokerCapabilities``
    object, or any unexpected failure is rejected fail-closed with the
    step-mismatch code.
    """

    try:
        if not isinstance(capabilities, BrokerCapabilities):
            return REASON_VOLUME_STEP_MISMATCH
        if not isinstance(quantity, Decimal) or not quantity.is_finite():
            return REASON_VOLUME_STEP_MISMATCH
        if quantity < capabilities.volume_min:
            return REASON_VOLUME_BELOW_MIN
        if quantity > capabilities.volume_max:
            return REASON_VOLUME_ABOVE_MAX
        anchor = (
            capabilities.volume_step_anchor
            if capabilities.volume_step_anchor is not None
            else capabilities.volume_min
        )
        if not _on_step_grid(quantity, anchor, capabilities.volume_step):
            return REASON_VOLUME_STEP_MISMATCH
        return None
    except Exception:
        return REASON_VOLUME_STEP_MISMATCH
