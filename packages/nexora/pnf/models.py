"""P&F engine data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.market_data.models import NormalizedPriceEvent

ColumnDirection = Literal["X", "O"]
PnfTransitionType = Literal["seed", "extension", "reversal"]
PnfTransitionReason = Literal["seed_confirmed", "extended", "reversed"]


class PnfInputError(ValueError):
    """Sanitized P&F input/config error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PnfConfig:
    symbol: str
    box_size: Decimal
    reversal_boxes: int
    price_precision: int
    price_source: str
    version: str

    def __post_init__(self) -> None:
        if not self.symbol:
            raise PnfInputError("missing_symbol")
        if self.box_size <= 0:
            raise PnfInputError("invalid_box_size")
        if self.reversal_boxes < 1:
            raise PnfInputError("invalid_reversal_boxes")
        if self.price_precision < 0 or self.price_precision > 10:
            raise PnfInputError("invalid_precision")
        if not self.price_source:
            raise PnfInputError("missing_price_source")
        if not self.version:
            raise PnfInputError("missing_version")


@dataclass(frozen=True, slots=True)
class PnfColumn:
    column_id: int
    direction: ColumnDirection
    open_price: Decimal
    close_price: Decimal
    box_count: int
    started_at: datetime
    updated_at: datetime
    source_event_id: str


@dataclass(frozen=True, slots=True)
class PnfTransition:
    type: PnfTransitionType
    reason: PnfTransitionReason
    symbol: str
    column_id: int
    direction: ColumnDirection
    from_price: Decimal
    to_price: Decimal
    boxes_moved: int
    event_time: datetime
    source_event_id: str
    identity_key: str
    config_version: str


@dataclass(frozen=True, slots=True)
class PnfSymbolState:
    symbol: str
    config_version: str
    seed_price: Decimal | None
    seed_event_id: str | None
    columns: tuple[PnfColumn, ...]
    transitions: tuple[PnfTransition, ...]


@dataclass(frozen=True, slots=True)
class PnfSnapshot:
    schema_version: Literal[1]
    configs: tuple[PnfConfig, ...]
    symbols: tuple[PnfSymbolState, ...]


@dataclass(slots=True)
class _MutableSymbolState:
    seed_price: Decimal | None = None
    seed_event_id: str | None = None
    columns: list[PnfColumn] = field(default_factory=list)
    transitions: list[PnfTransition] = field(default_factory=list)
    seen_identity_keys: set[str] = field(default_factory=set, repr=False)

    @property
    def current_column(self) -> PnfColumn | None:
        if not self.columns:
            return None
        return self.columns[-1]

    def to_public(self, *, symbol: str, config_version: str) -> PnfSymbolState:
        return PnfSymbolState(
            symbol=symbol,
            config_version=config_version,
            seed_price=self.seed_price,
            seed_event_id=self.seed_event_id,
            columns=tuple(self.columns),
            transitions=tuple(self.transitions),
        )

    @classmethod
    def from_public(cls, state: PnfSymbolState) -> _MutableSymbolState:
        mutable = cls(
            seed_price=state.seed_price,
            seed_event_id=state.seed_event_id,
            columns=list(state.columns),
            transitions=list(state.transitions),
        )
        mutable.seen_identity_keys = {transition.identity_key for transition in state.transitions}
        return mutable


def ensure_event_for_config(event: NormalizedPriceEvent, config: PnfConfig) -> None:
    if event.symbol != config.symbol:
        raise PnfInputError("symbol_mismatch")
    if event.price_source != config.price_source:
        raise PnfInputError("price_source_mismatch")
    if event.precision != config.price_precision:
        raise PnfInputError("precision_mismatch")
    if event.is_out_of_order:
        raise PnfInputError("out_of_order_event")

