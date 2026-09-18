"""Explainable research signal engine."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from nexora.market_regime import RegimeSnapshot
from nexora.matrix import MatrixSnapshot
from nexora.signals.models import ResearchSignal, SignalConfig, SignalSide, SignalSnapshot
from nexora.structure import StructureSnapshot


@dataclass(slots=True)
class SignalEngine:
    config: SignalConfig
    _sequence: int = field(default=0, init=False, repr=False)
    _history: list[ResearchSignal] = field(default_factory=list, init=False, repr=False)
    _last_signal_sequence: int | None = field(default=None, init=False, repr=False)

    def evaluate(
        self,
        *,
        structure: StructureSnapshot,
        regime: RegimeSnapshot,
        matrix: MatrixSnapshot,
    ) -> SignalSnapshot:
        self._sequence += 1
        self._expire_if_needed()

        if self._cooldown_active():
            return self.snapshot()
        if (
            not structure.pivots
            or matrix.alignment == "unavailable"
            or regime.state.label == "unknown"
        ):
            return self.snapshot()

        latest_pivot = structure.pivots[-1]
        side: SignalSide | None = None
        reasons: tuple[str, ...]
        reason_codes: tuple[str, ...]
        if matrix.alignment == "aligned_bullish" and regime.state.label == "trend":
            side = "long"
            reasons = (
                "Aligned bullish matrix with trend regime",
                "Confirmed pivot context available",
            )
            reason_codes = ("matrix_bullish", "regime_trend")
        elif matrix.alignment == "aligned_bearish" and regime.state.label == "trend":
            side = "short"
            reasons = (
                "Aligned bearish matrix with trend regime",
                "Confirmed pivot context available",
            )
            reason_codes = ("matrix_bearish", "regime_trend")
        elif regime.state.label == "high_volatility" and matrix.alignment == "mixed":
            side = "short" if latest_pivot.kind == "high" else "long"
            reasons = (
                "Mixed matrix with high volatility",
                "Signal follows latest confirmed pivot polarity",
            )
            reason_codes = ("matrix_mixed", "regime_high_volatility")
        else:
            return self.snapshot()

        signal = ResearchSignal(
            signal_id=f"{self.config.symbol}:{self._sequence}:{side}",
            symbol=self.config.symbol,
            side=side,
            sequence=self._sequence,
            occurrence_time=latest_pivot.occurrence_time,
            confirmation_time=latest_pivot.confirmation_time,
            decision_time=matrix.generated_at,
            reasons=reasons,
            reason_codes=reason_codes,
            source_refs=(
                latest_pivot.source_transition_id,
                regime.state.source_ref or "regime:none",
                *(
                    state.latest_transition.identity_key
                    if state.latest_transition is not None
                    else "matrix:none"
                    for state in matrix.resolutions
                ),
            ),
            config_version=self.config.version,
            engine_versions=(
                matrix.resolutions[0].latest_transition.config_version
                if matrix.resolutions and matrix.resolutions[0].latest_transition is not None
                else "unknown",
                regime.state.config_version,
            ),
            status="active",
        )
        if self._history and self._is_duplicate(self._history[-1], signal):
            return self.snapshot()
        self._history.append(signal)
        self._last_signal_sequence = self._sequence
        return self.snapshot()

    def snapshot(self) -> SignalSnapshot:
        latest = self._history[-1] if self._history else None
        return SignalSnapshot(
            schema_version=1,
            symbol=self.config.symbol,
            sequence=self._sequence,
            latest=latest,
            history=tuple(self._history),
        )

    @classmethod
    def from_snapshot(cls, config: SignalConfig, snapshot: SignalSnapshot) -> SignalEngine:
        engine = cls(config=config)
        engine._sequence = snapshot.sequence
        engine._history = list(snapshot.history)
        if snapshot.latest is not None and snapshot.latest.status == "active":
            engine._last_signal_sequence = snapshot.latest.sequence
        return engine

    def _cooldown_active(self) -> bool:
        if self._last_signal_sequence is None:
            return False
        return (self._sequence - self._last_signal_sequence) <= self.config.cooldown_events

    def _expire_if_needed(self) -> None:
        if not self._history:
            return
        latest = self._history[-1]
        if (
            latest.status == "active"
            and (self._sequence - latest.sequence) > self.config.expiry_events
        ):
            self._history[-1] = replace(latest, status="expired")

    @staticmethod
    def _is_duplicate(previous: ResearchSignal, current: ResearchSignal) -> bool:
        return (
            previous.side == current.side
            and previous.source_refs == current.source_refs
            and previous.reason_codes == current.reason_codes
            and previous.status == "active"
        )
