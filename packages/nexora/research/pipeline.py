"""Pure event orchestration; no persistence or transport imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexora.adaptive_box import AdaptiveBoxConfig, AdaptiveBoxSizer, AdaptivePnfRunner
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.entry_readiness import evaluate_entry_readiness
from nexora.features import ResolvedFeatureConfig
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_regime import MarketRegimeEngine, RegimeConfig
from nexora.matrix import MatrixEngine, MatrixResolutionConfig
from nexora.patterns import PatternEngine, PatternStep
from nexora.pnf import PnfConfig
from nexora.signals import ResearchSignal, SignalConfig, SignalEngine
from nexora.structure import StructureEngine
from nexora.trendline import TrendlineEngine


@dataclass(frozen=True)
class ResolutionConfig:
    name: str
    pnf: PnfConfig
    sizing: AdaptiveBoxConfig


@dataclass(frozen=True)
class PipelineConfig:
    version: str
    resolutions: tuple[ResolutionConfig, ...]
    structure_resolution: str
    regime: RegimeConfig
    signals: SignalConfig
    stale_after_events: int

    def __post_init__(self) -> None:
        if not self.version or len(self.resolutions) < 3 or self.stale_after_events < 1:
            raise ValueError("invalid_pipeline_config")
        names = {r.name for r in self.resolutions}
        if len(names) != len(self.resolutions) or self.structure_resolution not in names:
            raise ValueError("invalid_resolution_names")
        if any(r.pnf.symbol != self.signals.symbol for r in self.resolutions):
            raise ValueError("pipeline_symbol_mismatch")
        if self.regime.symbol != self.signals.symbol:
            raise ValueError("pipeline_symbol_mismatch")
        if len({r.pnf.price_source for r in self.resolutions}) != 1:
            raise ValueError("pipeline_price_source_mismatch")


class ResearchPipeline:
    def __init__(
        self, config: PipelineConfig, *, features: ResolvedFeatureConfig | None = None
    ) -> None:
        self.config = config
        self.matrix = MatrixEngine(
            symbol=config.signals.symbol,
            resolutions=tuple(
                MatrixResolutionConfig(r.name, r.pnf.symbol, r.pnf.version, r.sizing.rule_version)
                for r in config.resolutions
            ),
            runners={
                r.name: AdaptivePnfRunner((r.pnf,), AdaptiveBoxSizer(r.sizing))
                for r in config.resolutions
            },
            stale_after_events=config.stale_after_events,
        )
        self.structure = StructureEngine(config.signals.symbol)
        self.trendline = TrendlineEngine(config.signals.symbol)
        self.pattern_engine = PatternEngine(
            symbol=config.signals.symbol,
            resolution=config.structure_resolution,
            price_tolerance=config.signals.pattern_price_tolerance,
            features=features,
        )
        self.regime = MarketRegimeEngine(config.regime)
        self.signals = SignalEngine(config.signals)
        self._seen: dict[str, str] = {}
        self._last: NormalizedPriceEvent | None = None
        self._output: dict[str, Any] = {}

    def process(self, event: NormalizedPriceEvent) -> dict[str, Any]:
        return self._process(event, emit_snapshot=True)

    def replay(self, event: NormalizedPriceEvent) -> None:
        """Advance every engine normally without serializing unused intermediate output."""
        self._process(event, emit_snapshot=False)

    def _process(self, event: NormalizedPriceEvent, *, emit_snapshot: bool) -> dict[str, Any]:
        identity = canonical_hash(event)
        if event.identity_key in self._seen:
            if self._seen[event.identity_key] != identity:
                raise ValueError("event_identity_conflict")
            return self.snapshot() if emit_snapshot else {}
        if event.is_duplicate or event.is_out_of_order:
            raise ValueError("noncanonical_event")
        if event.symbol != self.config.signals.symbol:
            raise ValueError("pipeline_symbol_mismatch")
        if (
            not event.price.is_finite()
            or event.price <= 0
            or event.received_at < event.event_time
            or event.price_source != self.config.resolutions[0].pnf.price_source
        ):
            raise ValueError("invalid_pipeline_event")
        if self._last is not None and (
            event.source_sequence <= self._last.source_sequence
            or event.event_time < self._last.event_time
        ):
            raise ValueError("out_of_order_event")
        runner = self.matrix.runners[self.config.structure_resolution]
        before = len(runner.pnf_engine.state_for(event.symbol).transitions)
        matrix = self.matrix.process(event, now=event.received_at)
        pnf = runner.pnf_engine.state_for(event.symbol)
        pattern_steps: list[PatternStep] = []
        for transition in pnf.transitions[before:]:
            structure_step = self.structure.process(transition)
            self.trendline.process(transition, structure_step)
            pattern_steps.append((transition, structure_step))
        pattern_snapshot = self.pattern_engine.process_event(pattern_steps)
        structure = self.structure.snapshot()
        trendline = self.trendline.snapshot()
        regime = self.regime.classify(structure, matrix)
        signals = self.signals.evaluate(structure=structure, regime=regime, matrix=matrix)
        entry_readiness = evaluate_entry_readiness(
            decision=signals.decision, trendline=trendline, config_version=self.config.version
        )
        self._output = {
            "config_version": self.config.version,
            "event": event,
            "matrix": matrix,
            "structure": structure,
            "trendline": trendline,
            "regime": regime,
            "signals": signals,
            "entry_readiness": entry_readiness,
            "columns": pnf.columns,
            "transitions": pnf.transitions,
            "pattern_engine": pattern_snapshot,
        }
        self._seen[event.identity_key] = identity
        self._last = event
        return self.snapshot() if emit_snapshot else {}

    def snapshot(self) -> dict[str, Any]:
        return dict(canonical_serialize(self._output))

    def research_signals(self) -> tuple[ResearchSignal, ...]:
        return self.signals.snapshot().history
