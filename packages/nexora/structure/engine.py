"""Causal market structure extraction from P&F transitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from nexora.pnf import PnfTransition
from nexora.structure.models import CandidateLevel, ConfirmedPivot, StructureSnapshot


@dataclass(slots=True)
class StructureEngine:
    symbol: str
    _sequence: int = field(default=0, init=False, repr=False)
    _transitions: list[PnfTransition] = field(default_factory=list, init=False, repr=False)
    _pivots: list[ConfirmedPivot] = field(default_factory=list, init=False, repr=False)
    _levels: list[CandidateLevel] = field(default_factory=list, init=False, repr=False)

    def process(self, transition: PnfTransition) -> StructureSnapshot:
        if transition.symbol != self.symbol:
            return self.snapshot()
        self._sequence += 1
        self._transitions.append(transition)
        self._invalidate_levels(transition)
        pivot = self._confirm_last_pivot()
        if pivot is not None:
            self._pivots.append(pivot)
            self._update_levels(pivot)
        return self.snapshot()

    def snapshot(self) -> StructureSnapshot:
        if not self._levels:
            unavailable_time = self._transitions[-1].event_time if self._transitions else None
            if unavailable_time is not None:
                unavailable = CandidateLevel(
                    side="support",
                    price=self._transitions[-1].to_price,
                    status="unavailable",
                    source_pivot_id=None,
                    updated_at=unavailable_time,
                )
                return StructureSnapshot(
                    schema_version=1,
                    symbol=self.symbol,
                    sequence=self._sequence,
                    pivots=tuple(self._pivots),
                    levels=(unavailable,),
                )
        return StructureSnapshot(
            schema_version=1,
            symbol=self.symbol,
            sequence=self._sequence,
            pivots=tuple(self._pivots),
            levels=tuple(self._levels),
        )

    def _confirm_last_pivot(self) -> ConfirmedPivot | None:
        if len(self._transitions) < 3:
            return None
        left = self._transitions[-3]
        center = self._transitions[-2]
        right = self._transitions[-1]
        if center.to_price > left.to_price and center.to_price > right.to_price:
            return ConfirmedPivot(
                kind="high",
                price=center.to_price,
                occurrence_time=center.event_time,
                confirmation_time=right.event_time,
                source_transition_id=center.identity_key,
                config_version=center.config_version,
            )
        if center.to_price < left.to_price and center.to_price < right.to_price:
            return ConfirmedPivot(
                kind="low",
                price=center.to_price,
                occurrence_time=center.event_time,
                confirmation_time=right.event_time,
                source_transition_id=center.identity_key,
                config_version=center.config_version,
            )
        return None

    def _update_levels(self, pivot: ConfirmedPivot) -> None:
        if pivot.kind == "high":
            self._levels.append(
                CandidateLevel(
                    side="resistance",
                    price=pivot.price,
                    status="confirmed",
                    source_pivot_id=pivot.source_transition_id,
                    updated_at=pivot.confirmation_time,
                )
            )
        else:
            self._levels.append(
                CandidateLevel(
                    side="support",
                    price=pivot.price,
                    status="confirmed",
                    source_pivot_id=pivot.source_transition_id,
                    updated_at=pivot.confirmation_time,
                )
            )

    def _invalidate_levels(self, transition: PnfTransition) -> None:
        updated: list[CandidateLevel] = []
        for level in self._levels:
            if level.status in {"invalidated", "unavailable"}:
                updated.append(level)
                continue
            if level.side == "support" and transition.to_price < level.price:
                updated.append(
                    CandidateLevel(
                        side=level.side,
                        price=level.price,
                        status="invalidated",
                        source_pivot_id=level.source_pivot_id,
                        updated_at=transition.event_time,
                    )
                )
                continue
            if level.side == "resistance" and transition.to_price > level.price:
                updated.append(
                    CandidateLevel(
                        side=level.side,
                        price=level.price,
                        status="invalidated",
                        source_pivot_id=level.source_pivot_id,
                        updated_at=transition.event_time,
                    )
                )
                continue
            updated.append(level)
        self._levels = updated
