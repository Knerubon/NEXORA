"""Deterministic fixed-box Point & Figure engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from nexora.market_data.models import NormalizedPriceEvent
from nexora.pnf.models import (
    PnfColumn,
    PnfConfig,
    PnfInputError,
    PnfSnapshot,
    PnfSymbolState,
    PnfTransition,
    PnfTransitionReason,
    PnfTransitionType,
    _MutableSymbolState,
    ensure_event_for_config,
)


def _quantize(value: Decimal, precision: int) -> Decimal:
    quantum = Decimal(1).scaleb(-precision)
    return value.quantize(quantum)


def _boxes_between(start: Decimal, end: Decimal, box_size: Decimal) -> int:
    diff = abs(end - start)
    if diff < box_size:
        return 0
    ratio = (diff / box_size).to_integral_value(rounding=ROUND_FLOOR)
    return int(ratio)


def _column_bounds(column: PnfColumn) -> tuple[Decimal, Decimal]:
    if column.direction == "X":
        return (column.open_price, column.close_price)
    return (column.close_price, column.open_price)


@dataclass(slots=True)
class PnfEngine:
    configs: tuple[PnfConfig, ...]
    _configs_by_symbol: dict[str, PnfConfig] = field(init=False, repr=False)
    _states: dict[str, _MutableSymbolState] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.configs:
            raise PnfInputError("missing_configs")
        self._configs_by_symbol = {config.symbol: config for config in self.configs}
        if len(self._configs_by_symbol) != len(self.configs):
            raise PnfInputError("duplicate_symbol_config")
        self._states: dict[str, _MutableSymbolState] = {}
        for config in self.configs:
            self._states[config.symbol] = _MutableSymbolState()

    def process(self, event: NormalizedPriceEvent) -> tuple[PnfTransition, ...]:
        config = self._configs_by_symbol.get(event.symbol)
        if config is None:
            raise PnfInputError("symbol_not_configured")
        ensure_event_for_config(event, config)
        state = self._states[event.symbol]
        if event.is_duplicate:
            state.seen_identity_keys.add(event.identity_key)
            return ()
        if event.identity_key in state.seen_identity_keys:
            return ()
        state.seen_identity_keys.add(event.identity_key)

        price = _quantize(event.price, config.price_precision)
        active_box_size = config.box_size
        if state.seed_price is None:
            state.seed_price = price
            state.seed_event_id = event.source_event_id
            return ()
        if state.current_column is None:
            return self._try_seed_column(state, config, event, price, active_box_size)
        return self._advance_column(state, config, event, price, active_box_size)

    def process_with_box(
        self,
        event: NormalizedPriceEvent,
        *,
        box_size: Decimal,
        sizing_rule_version: str,
    ) -> tuple[PnfTransition, ...]:
        if box_size <= 0:
            raise PnfInputError("invalid_box_size")
        if not sizing_rule_version:
            raise PnfInputError("missing_sizing_rule_version")
        config = self._configs_by_symbol.get(event.symbol)
        if config is None:
            raise PnfInputError("symbol_not_configured")
        ensure_event_for_config(event, config)
        state = self._states[event.symbol]
        if event.is_duplicate:
            state.seen_identity_keys.add(event.identity_key)
            return ()
        if event.identity_key in state.seen_identity_keys:
            return ()
        state.seen_identity_keys.add(event.identity_key)

        price = _quantize(event.price, config.price_precision)
        active_box_size = _quantize(box_size, config.price_precision)
        if active_box_size <= 0:
            raise PnfInputError("invalid_box_size")
        if state.seed_price is None:
            state.seed_price = price
            state.seed_event_id = event.source_event_id
            return ()
        if state.current_column is None:
            return self._try_seed_column(
                state,
                config,
                event,
                price,
                active_box_size,
                sizing_rule_version=sizing_rule_version,
            )
        return self._advance_column(
            state,
            config,
            event,
            price,
            active_box_size,
            sizing_rule_version=sizing_rule_version,
        )

    def snapshot(self) -> PnfSnapshot:
        symbols: list[PnfSymbolState] = []
        for config in self.configs:
            state = self._states[config.symbol]
            symbols.append(state.to_public(symbol=config.symbol, config_version=config.version))
        return PnfSnapshot(schema_version=1, configs=self.configs, symbols=tuple(symbols))

    @classmethod
    def from_snapshot(cls, snapshot: PnfSnapshot) -> PnfEngine:
        engine = cls(configs=snapshot.configs)
        states = {
            symbol_state.symbol: _MutableSymbolState.from_public(symbol_state)
            for symbol_state in snapshot.symbols
        }
        for config in snapshot.configs:
            if config.symbol in states:
                engine._states[config.symbol] = states[config.symbol]
        return engine

    def state_for(self, symbol: str) -> PnfSymbolState:
        config = self._configs_by_symbol.get(symbol)
        if config is None:
            raise PnfInputError("symbol_not_configured")
        return self._states[symbol].to_public(symbol=symbol, config_version=config.version)

    def _try_seed_column(
        self,
        state: _MutableSymbolState,
        config: PnfConfig,
        event: NormalizedPriceEvent,
        price: Decimal,
        box_size: Decimal,
        sizing_rule_version: str | None = None,
    ) -> tuple[PnfTransition, ...]:
        assert state.seed_price is not None
        up_boxes = _boxes_between(state.seed_price, price, box_size)
        if price > state.seed_price and up_boxes >= 1:
            new_high = _quantize(
                state.seed_price + (box_size * up_boxes),
                config.price_precision,
            )
            column = PnfColumn(
                column_id=1,
                direction="X",
                open_price=state.seed_price,
                close_price=new_high,
                box_count=up_boxes,
                started_at=event.event_time,
                updated_at=event.event_time,
                source_event_id=event.source_event_id,
            )
            transition = self._build_transition(
                event=event,
                config=config,
                column=column,
                transition_type="seed",
                reason="seed_confirmed",
                from_price=state.seed_price,
                to_price=new_high,
                boxes_moved=up_boxes,
                effective_box_size=box_size,
                sizing_rule_version=sizing_rule_version or config.version,
            )
            state.columns.append(column)
            state.transitions.append(transition)
            return (transition,)

        down_boxes = _boxes_between(state.seed_price, price, box_size)
        if price < state.seed_price and down_boxes >= 1:
            new_low = _quantize(
                state.seed_price - (box_size * down_boxes),
                config.price_precision,
            )
            column = PnfColumn(
                column_id=1,
                direction="O",
                open_price=state.seed_price,
                close_price=new_low,
                box_count=down_boxes,
                started_at=event.event_time,
                updated_at=event.event_time,
                source_event_id=event.source_event_id,
            )
            transition = self._build_transition(
                event=event,
                config=config,
                column=column,
                transition_type="seed",
                reason="seed_confirmed",
                from_price=state.seed_price,
                to_price=new_low,
                boxes_moved=down_boxes,
                effective_box_size=box_size,
                sizing_rule_version=sizing_rule_version or config.version,
            )
            state.columns.append(column)
            state.transitions.append(transition)
            return (transition,)
        return ()

    def _advance_column(
        self,
        state: _MutableSymbolState,
        config: PnfConfig,
        event: NormalizedPriceEvent,
        price: Decimal,
        box_size: Decimal,
        sizing_rule_version: str | None = None,
    ) -> tuple[PnfTransition, ...]:
        current = state.current_column
        assert current is not None
        low, high = _column_bounds(current)

        if current.direction == "X":
            extension_boxes = _boxes_between(high, price, box_size) if price >= high else 0
            if extension_boxes >= 1:
                new_high = _quantize(
                    high + (box_size * extension_boxes),
                    config.price_precision,
                )
                updated = PnfColumn(
                    column_id=current.column_id,
                    direction="X",
                    open_price=current.open_price,
                    close_price=new_high,
                    box_count=current.box_count + extension_boxes,
                    started_at=current.started_at,
                    updated_at=event.event_time,
                    source_event_id=event.source_event_id,
                )
                transition = self._build_transition(
                    event=event,
                    config=config,
                    column=updated,
                    transition_type="extension",
                    reason="extended",
                    from_price=high,
                    to_price=new_high,
                    boxes_moved=extension_boxes,
                    effective_box_size=box_size,
                    sizing_rule_version=sizing_rule_version or config.version,
                )
                state.columns[-1] = updated
                state.transitions.append(transition)
                return (transition,)

            reversal_trigger = high - (box_size * config.reversal_boxes)
            if price <= reversal_trigger:
                reversal_boxes = _boxes_between(high, price, box_size)
                if reversal_boxes >= config.reversal_boxes:
                    new_open = _quantize(high - box_size, config.price_precision)
                    new_close = _quantize(
                        high - (box_size * reversal_boxes),
                        config.price_precision,
                    )
                    reversed_column = PnfColumn(
                        column_id=current.column_id + 1,
                        direction="O",
                        open_price=new_open,
                        close_price=new_close,
                        box_count=reversal_boxes,
                        started_at=event.event_time,
                        updated_at=event.event_time,
                        source_event_id=event.source_event_id,
                    )
                    transition = self._build_transition(
                        event=event,
                        config=config,
                        column=reversed_column,
                        transition_type="reversal",
                        reason="reversed",
                        from_price=high,
                        to_price=new_close,
                        boxes_moved=reversal_boxes,
                        effective_box_size=box_size,
                        sizing_rule_version=sizing_rule_version or config.version,
                    )
                    state.columns.append(reversed_column)
                    state.transitions.append(transition)
                    return (transition,)
            return ()

        extension_boxes = _boxes_between(low, price, box_size) if price <= low else 0
        if extension_boxes >= 1:
            new_low = _quantize(
                low - (box_size * extension_boxes),
                config.price_precision,
            )
            updated = PnfColumn(
                column_id=current.column_id,
                direction="O",
                open_price=current.open_price,
                close_price=new_low,
                box_count=current.box_count + extension_boxes,
                started_at=current.started_at,
                updated_at=event.event_time,
                source_event_id=event.source_event_id,
            )
            transition = self._build_transition(
                event=event,
                config=config,
                column=updated,
                transition_type="extension",
                reason="extended",
                from_price=low,
                to_price=new_low,
                boxes_moved=extension_boxes,
                effective_box_size=box_size,
                sizing_rule_version=sizing_rule_version or config.version,
            )
            state.columns[-1] = updated
            state.transitions.append(transition)
            return (transition,)

        reversal_trigger = low + (box_size * config.reversal_boxes)
        if price >= reversal_trigger:
            reversal_boxes = _boxes_between(low, price, box_size)
            if reversal_boxes >= config.reversal_boxes:
                new_open = _quantize(low + box_size, config.price_precision)
                new_close = _quantize(
                    low + (box_size * reversal_boxes),
                    config.price_precision,
                )
                reversed_column = PnfColumn(
                    column_id=current.column_id + 1,
                    direction="X",
                    open_price=new_open,
                    close_price=new_close,
                    box_count=reversal_boxes,
                    started_at=event.event_time,
                    updated_at=event.event_time,
                    source_event_id=event.source_event_id,
                )
                transition = self._build_transition(
                    event=event,
                    config=config,
                    column=reversed_column,
                    transition_type="reversal",
                    reason="reversed",
                    from_price=low,
                    to_price=new_close,
                    boxes_moved=reversal_boxes,
                    effective_box_size=box_size,
                    sizing_rule_version=sizing_rule_version or config.version,
                )
                state.columns.append(reversed_column)
                state.transitions.append(transition)
                return (transition,)
        return ()

    def _build_transition(
        self,
        *,
        event: NormalizedPriceEvent,
        config: PnfConfig,
        column: PnfColumn,
        transition_type: PnfTransitionType,
        reason: PnfTransitionReason,
        from_price: Decimal,
        to_price: Decimal,
        boxes_moved: int,
        effective_box_size: Decimal,
        sizing_rule_version: str,
    ) -> PnfTransition:
        return PnfTransition(
            type=transition_type,
            reason=reason,
            symbol=event.symbol,
            column_id=column.column_id,
            direction=column.direction,
            from_price=from_price,
            to_price=to_price,
            boxes_moved=boxes_moved,
            event_time=event.event_time,
            source_event_id=event.source_event_id,
            identity_key=event.identity_key,
            config_version=config.version,
            effective_box_size=effective_box_size,
            sizing_rule_version=sizing_rule_version,
        )
