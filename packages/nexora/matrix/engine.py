"""Fan-out matrix orchestration over independent resolution runners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nexora.adaptive_box import AdaptivePnfRunner
from nexora.market_data.models import NormalizedPriceEvent
from nexora.matrix.models import (
    MatrixAlignment,
    MatrixResolutionConfig,
    MatrixResolutionState,
    MatrixSnapshot,
)
from nexora.pnf import PnfTransition


@dataclass(slots=True)
class MatrixEngine:
    symbol: str
    resolutions: tuple[MatrixResolutionConfig, ...]
    runners: dict[str, AdaptivePnfRunner]
    stale_after_events: int = 5
    _latest: dict[str, tuple[int, PnfTransition]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _sequence: int = field(default=0, init=False, repr=False)
    _watermark: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        names = [item.name for item in self.resolutions]
        if len(set(names)) != len(names):
            raise ValueError("duplicate_resolution_name")
        if set(self.runners.keys()) != set(names):
            raise ValueError("runner_resolution_mismatch")

    def process(self, event: NormalizedPriceEvent, *, now: datetime) -> MatrixSnapshot:
        self._watermark = max(self._watermark, event.source_sequence)
        self._sequence += 1
        for config in self.resolutions:
            transitions = self.runners[config.name].process(event)
            if transitions:
                self._latest[config.name] = (event.source_sequence, transitions[-1])
        states: list[MatrixResolutionState] = []
        directions: list[str] = []
        for config in self.resolutions:
            latest_data = self._latest.get(config.name)
            if latest_data is None:
                states.append(
                    MatrixResolutionState(
                        name=config.name,
                        symbol=self.symbol,
                        direction="none",
                        latest_transition=None,
                        status="warmup",
                    )
                )
                continue
            latest_sequence, transition = latest_data
            gap = event.source_sequence - latest_sequence
            stale = gap >= self.stale_after_events
            direction = transition.direction if not stale else "none"
            if direction != "none":
                directions.append(direction)
            states.append(
                MatrixResolutionState(
                    name=config.name,
                    symbol=self.symbol,
                    direction=direction,
                    latest_transition=transition if not stale else None,
                    status="stale" if stale else "ready",
                )
            )
        alignment, strength = _matrix_alignment(directions, len(self.resolutions))
        return MatrixSnapshot(
            schema_version=1,
            symbol=self.symbol,
            sequence=self._sequence,
            watermark_sequence=self._watermark,
            generated_at=now,
            alignment=alignment,
            strength=strength,
            resolutions=tuple(states),
        )

    def snapshot(self, *, now: datetime) -> MatrixSnapshot:
        fake_event = self._watermark
        self._sequence += 1
        states: list[MatrixResolutionState] = []
        directions: list[str] = []
        for config in self.resolutions:
            latest_data = self._latest.get(config.name)
            if latest_data is None:
                states.append(
                    MatrixResolutionState(
                        name=config.name,
                        symbol=self.symbol,
                        direction="none",
                        latest_transition=None,
                        status="unavailable",
                    )
                )
                continue
            latest_sequence, transition = latest_data
            stale = (fake_event - latest_sequence) >= self.stale_after_events
            direction = transition.direction if not stale else "none"
            if direction != "none":
                directions.append(direction)
            states.append(
                MatrixResolutionState(
                    name=config.name,
                    symbol=self.symbol,
                    direction=direction,
                    latest_transition=transition if not stale else None,
                    status="stale" if stale else "ready",
                )
            )
        alignment, strength = _matrix_alignment(directions, len(self.resolutions))
        return MatrixSnapshot(
            schema_version=1,
            symbol=self.symbol,
            sequence=self._sequence,
            watermark_sequence=self._watermark,
            generated_at=now,
            alignment=alignment,
            strength=strength,
            resolutions=tuple(states),
        )


def _matrix_alignment(directions: list[str], resolution_count: int) -> tuple[MatrixAlignment, int]:
    if len(directions) != resolution_count:
        return ("unavailable", 0)
    if all(direction == "X" for direction in directions):
        return ("aligned_bullish", 3)
    if all(direction == "O" for direction in directions):
        return ("aligned_bearish", 3)
    x_count = sum(1 for direction in directions if direction == "X")
    o_count = sum(1 for direction in directions if direction == "O")
    return ("mixed", abs(x_count - o_count))
