"""Transaction-cost accounting with explicit UNKNOWN / UNAVAILABLE states.

Nothing here assumes a broker, symbol or lot size. A cost the caller does not know is
reported as such and is never silently treated as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

CostState = Literal["known", "unknown", "unavailable"]
COMPONENTS = ("spread", "commission", "slippage", "swap")


class CostInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CostComponent:
    """One cost input. `amount` is per unit of size (per swap-day for swap)."""

    state: CostState
    amount: Decimal | None = None

    def __post_init__(self) -> None:
        if self.state == "known":
            if self.amount is None or not self.amount.is_finite() or self.amount < 0:
                raise CostInputError("invalid_known_cost")
        elif self.amount is not None:
            raise CostInputError("amount_forbidden_when_not_known")


KNOWN_ZERO = CostComponent("known", Decimal("0"))
UNKNOWN = CostComponent("unknown")
UNAVAILABLE = CostComponent("unavailable")


@dataclass(frozen=True, slots=True)
class CostModel:
    """Per-component cost states. Spread may instead be derived from observed bid/ask."""

    version: str
    currency: str
    spread: CostComponent
    commission: CostComponent
    slippage: CostComponent
    swap: CostComponent
    spread_from_quotes: bool = False


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    known_total: Decimal
    known: tuple[tuple[str, Decimal], ...]
    unknown: tuple[str, ...]
    unavailable: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """True only when every component was known; otherwise net PnL is a bound."""
        return not self.unknown and not self.unavailable


def compute_costs(
    model: CostModel,
    *,
    size: Decimal,
    holding_days: Decimal,
    observed_spread: Decimal | None = None,
) -> CostBreakdown:
    """Costs for one round trip. `observed_spread` is a per-unit quoted spread, if any."""
    for value in (size, holding_days):
        if not value.is_finite() or value < 0:
            raise CostInputError("invalid_size_or_holding")
    if size == 0:
        raise CostInputError("invalid_size_or_holding")
    if observed_spread is not None and (not observed_spread.is_finite() or observed_spread < 0):
        raise CostInputError("invalid_observed_spread")

    spread = model.spread
    if model.spread_from_quotes:
        spread = UNAVAILABLE if observed_spread is None else CostComponent("known", observed_spread)

    known: list[tuple[str, Decimal]] = []
    unknown: list[str] = []
    unavailable: list[str] = []
    for name, component in (
        ("spread", spread),
        ("commission", model.commission),
        ("slippage", model.slippage),
        ("swap", model.swap),
    ):
        if component.state == "unknown":
            unknown.append(name)
        elif component.state == "unavailable":
            unavailable.append(name)
        else:
            assert component.amount is not None
            multiplier = size * holding_days if name == "swap" else size
            known.append((name, component.amount * multiplier))
    return CostBreakdown(
        known_total=sum((amount for _, amount in known), Decimal("0")),
        known=tuple(known),
        unknown=tuple(unknown),
        unavailable=tuple(unavailable),
    )
