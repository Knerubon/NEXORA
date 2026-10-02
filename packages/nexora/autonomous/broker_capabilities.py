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

from nexora.market_data.instruments import FeedBinding, InstrumentDefinition


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

    def __post_init__(self) -> None:
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
