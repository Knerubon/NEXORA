"""Adaptive box sizing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

AdaptiveMode = Literal["fixed", "atr"]


class AdaptiveBoxError(ValueError):
    """Sanitized adaptive-box configuration/runtime error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AdaptiveBoxConfig:
    mode: AdaptiveMode
    fixed_box_size: Decimal
    price_precision: int
    rule_version: str
    atr_period: int = 14
    atr_multiplier: Decimal = Decimal("1.0")
    min_box_size: Decimal = Decimal("0.1")
    max_box_size: Decimal = Decimal("50.0")

    def __post_init__(self) -> None:
        if self.fixed_box_size <= 0:
            raise AdaptiveBoxError("invalid_fixed_box_size")
        if self.price_precision < 0 or self.price_precision > 10:
            raise AdaptiveBoxError("invalid_precision")
        if not self.rule_version:
            raise AdaptiveBoxError("missing_rule_version")
        if self.atr_period < 1:
            raise AdaptiveBoxError("invalid_atr_period")
        if self.atr_multiplier <= 0:
            raise AdaptiveBoxError("invalid_atr_multiplier")
        if self.min_box_size <= 0:
            raise AdaptiveBoxError("invalid_min_box_size")
        if self.max_box_size < self.min_box_size:
            raise AdaptiveBoxError("invalid_max_box_size")


@dataclass(frozen=True, slots=True)
class AdaptiveBoxDecision:
    symbol: str
    event_identity_key: str
    event_sequence: int
    effective_box_size: Decimal
    mode: AdaptiveMode
    rule_version: str
    warmup: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AdaptiveBoxState:
    symbol: str
    last_price: Decimal | None
    true_ranges: tuple[Decimal, ...]
    last_effective_box_size: Decimal


@dataclass(frozen=True, slots=True)
class AdaptiveBoxSnapshot:
    config: AdaptiveBoxConfig
    states: tuple[AdaptiveBoxState, ...]

