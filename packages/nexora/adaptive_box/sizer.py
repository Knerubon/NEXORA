"""Causal adaptive box sizing (fixed + ATR-derived candidate)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from nexora.adaptive_box.models import (
    AdaptiveBoxConfig,
    AdaptiveBoxDecision,
    AdaptiveBoxSnapshot,
    AdaptiveBoxState,
)
from nexora.market_data.models import NormalizedPriceEvent


def _quantize(value: Decimal, precision: int) -> Decimal:
    quantum = Decimal(1).scaleb(-precision)
    return value.quantize(quantum)


@dataclass(slots=True)
class _MutableAdaptiveState:
    last_price: Decimal | None = None
    true_ranges: deque[Decimal] = field(default_factory=deque)
    last_effective_box_size: Decimal = Decimal("0")

    def to_public(self, symbol: str) -> AdaptiveBoxState:
        return AdaptiveBoxState(
            symbol=symbol,
            last_price=self.last_price,
            true_ranges=tuple(self.true_ranges),
            last_effective_box_size=self.last_effective_box_size,
        )

    @classmethod
    def from_public(cls, state: AdaptiveBoxState) -> _MutableAdaptiveState:
        mutable = cls(
            last_price=state.last_price,
            true_ranges=deque(state.true_ranges),
            last_effective_box_size=state.last_effective_box_size,
        )
        return mutable


@dataclass(slots=True)
class AdaptiveBoxSizer:
    config: AdaptiveBoxConfig
    _states: dict[str, _MutableAdaptiveState] = field(default_factory=dict, init=False, repr=False)

    def decide(self, event: NormalizedPriceEvent) -> AdaptiveBoxDecision:
        state = self._states.setdefault(event.symbol, _MutableAdaptiveState())
        if state.last_effective_box_size <= 0:
            state.last_effective_box_size = _quantize(
                self.config.fixed_box_size,
                self.config.price_precision,
            )

        if self.config.mode == "fixed":
            state.last_price = event.price
            return AdaptiveBoxDecision(
                symbol=event.symbol,
                event_identity_key=event.identity_key,
                event_sequence=event.source_sequence,
                effective_box_size=state.last_effective_box_size,
                mode=self.config.mode,
                rule_version=self.config.rule_version,
                warmup=False,
                reason="fixed_mode",
            )

        warmup = False
        reason = "atr_ready"
        previous_price = state.last_price
        if previous_price is None:
            warmup = True
            reason = "warmup_missing_previous"
            state.last_price = event.price
            return AdaptiveBoxDecision(
                symbol=event.symbol,
                event_identity_key=event.identity_key,
                event_sequence=event.source_sequence,
                effective_box_size=state.last_effective_box_size,
                mode=self.config.mode,
                rule_version=self.config.rule_version,
                warmup=warmup,
                reason=reason,
            )

        true_range = abs(event.price - previous_price)
        state.last_price = event.price
        state.true_ranges.append(true_range)
        while len(state.true_ranges) > self.config.atr_period:
            state.true_ranges.popleft()

        if len(state.true_ranges) < self.config.atr_period:
            warmup = True
            reason = "warmup_insufficient_window"
            return AdaptiveBoxDecision(
                symbol=event.symbol,
                event_identity_key=event.identity_key,
                event_sequence=event.source_sequence,
                effective_box_size=state.last_effective_box_size,
                mode=self.config.mode,
                rule_version=self.config.rule_version,
                warmup=warmup,
                reason=reason,
            )

        atr = sum(state.true_ranges, start=Decimal("0")) / Decimal(self.config.atr_period)
        candidate = atr * self.config.atr_multiplier
        if candidate <= 0:
            reason = "zero_volatility_hold_last"
            return AdaptiveBoxDecision(
                symbol=event.symbol,
                event_identity_key=event.identity_key,
                event_sequence=event.source_sequence,
                effective_box_size=state.last_effective_box_size,
                mode=self.config.mode,
                rule_version=self.config.rule_version,
                warmup=False,
                reason=reason,
            )

        clamped = min(max(candidate, self.config.min_box_size), self.config.max_box_size)
        quantized = _quantize(clamped, self.config.price_precision)
        if quantized <= 0:
            quantized = _quantize(self.config.min_box_size, self.config.price_precision)
            reason = "quantized_to_min"
        state.last_effective_box_size = quantized
        return AdaptiveBoxDecision(
            symbol=event.symbol,
            event_identity_key=event.identity_key,
            event_sequence=event.source_sequence,
            effective_box_size=quantized,
            mode=self.config.mode,
            rule_version=self.config.rule_version,
            warmup=False,
            reason=reason,
        )

    def snapshot(self) -> AdaptiveBoxSnapshot:
        states = tuple(
            state.to_public(symbol=symbol)
            for symbol, state in sorted(self._states.items())
        )
        return AdaptiveBoxSnapshot(config=self.config, states=states)

    @classmethod
    def from_snapshot(cls, snapshot: AdaptiveBoxSnapshot) -> AdaptiveBoxSizer:
        sizer = cls(config=snapshot.config)
        for state in snapshot.states:
            sizer._states[state.symbol] = _MutableAdaptiveState.from_public(state)
        return sizer
