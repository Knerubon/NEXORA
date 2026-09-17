"""Adaptive sizing + P&F engine integration."""

from __future__ import annotations

from dataclasses import dataclass

from nexora.adaptive_box.models import AdaptiveBoxDecision, AdaptiveBoxSnapshot
from nexora.adaptive_box.sizer import AdaptiveBoxSizer
from nexora.market_data.models import NormalizedPriceEvent
from nexora.pnf import PnfConfig, PnfEngine, PnfSnapshot, PnfTransition


@dataclass(frozen=True, slots=True)
class AdaptivePnfSnapshot:
    pnf_snapshot: PnfSnapshot
    adaptive_snapshot: AdaptiveBoxSnapshot
    decisions: tuple[AdaptiveBoxDecision, ...]


@dataclass(slots=True)
class AdaptivePnfRunner:
    pnf_engine: PnfEngine
    sizer: AdaptiveBoxSizer
    _decisions: list[AdaptiveBoxDecision]

    def __init__(self, pnf_configs: tuple[PnfConfig, ...], sizer: AdaptiveBoxSizer) -> None:
        self.pnf_engine = PnfEngine(configs=pnf_configs)
        self.sizer = sizer
        self._decisions = []

    def process(self, event: NormalizedPriceEvent) -> tuple[PnfTransition, ...]:
        decision = self.sizer.decide(event)
        self._decisions.append(decision)
        return self.pnf_engine.process_with_box(
            event,
            box_size=decision.effective_box_size,
            sizing_rule_version=decision.rule_version,
        )

    def decisions_for(self, symbol: str) -> tuple[AdaptiveBoxDecision, ...]:
        return tuple(decision for decision in self._decisions if decision.symbol == symbol)

    def snapshot(self) -> AdaptivePnfSnapshot:
        return AdaptivePnfSnapshot(
            pnf_snapshot=self.pnf_engine.snapshot(),
            adaptive_snapshot=self.sizer.snapshot(),
            decisions=tuple(self._decisions),
        )

    @classmethod
    def from_snapshot(cls, snapshot: AdaptivePnfSnapshot) -> AdaptivePnfRunner:
        runner = cls(
            pnf_configs=snapshot.pnf_snapshot.configs,
            sizer=AdaptiveBoxSizer.from_snapshot(snapshot.adaptive_snapshot),
        )
        runner.pnf_engine = PnfEngine.from_snapshot(snapshot.pnf_snapshot)
        runner._decisions = list(snapshot.decisions)
        return runner

