"""Causal regime classifier from structure and matrix snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field

from nexora.market_regime.models import RegimeConfig, RegimeLabel, RegimeSnapshot, RegimeState
from nexora.matrix import MatrixSnapshot
from nexora.structure import StructureSnapshot


@dataclass(slots=True)
class MarketRegimeEngine:
    config: RegimeConfig
    _sequence: int = field(default=0, init=False, repr=False)
    _previous_label: RegimeLabel = field(default="unknown", init=False, repr=False)

    def classify(self, structure: StructureSnapshot, matrix: MatrixSnapshot) -> RegimeSnapshot:
        self._sequence += 1
        label: RegimeLabel = "unknown"
        reason = "insufficient_inputs"
        if matrix.alignment == "unavailable" or not structure.pivots:
            state = RegimeState(
                label="unknown",
                reason=reason,
                effective_time=matrix.generated_at,
                source_ref=None,
                config_version=self.config.version,
            )
            return RegimeSnapshot(
                schema_version=1,
                symbol=self.config.symbol,
                sequence=self._sequence,
                state=state,
            )

        prices = [pivot.price for pivot in structure.pivots[-self.config.lookback :]]
        if len(prices) >= 2:
            slope = prices[-1] - prices[0]
            width = max(prices) - min(prices)
            if width >= self.config.high_volatility_min_width:
                label = "high_volatility"
                reason = "volatility_width_threshold"
            elif abs(slope) >= self.config.trend_min_slope:
                label = "trend"
                reason = "trend_slope_threshold"
            else:
                label = "range"
                reason = "range_compaction"
            if (
                self._previous_label != "unknown"
                and label != self._previous_label
                and (
                    (
                        self._previous_label == "high_volatility"
                        and width >= self.config.high_volatility_min_width - self.config.hysteresis
                    )
                    or (
                        self._previous_label == "trend"
                        and label == "range"
                        and abs(slope) >= self.config.trend_min_slope - self.config.hysteresis
                    )
                )
            ):
                label = self._previous_label
                reason = "hysteresis_hold"
        else:
            label = "unknown"
            reason = "warmup"

        source_ref = structure.pivots[-1].source_transition_id
        self._previous_label = label
        state = RegimeState(
            label=label,
            reason=reason,
            effective_time=matrix.generated_at,
            source_ref=source_ref,
            config_version=self.config.version,
        )
        return RegimeSnapshot(
            schema_version=1,
            symbol=self.config.symbol,
            sequence=self._sequence,
            state=state,
        )
