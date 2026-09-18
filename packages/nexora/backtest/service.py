"""Backtest lab service for API consumption."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from nexora.backtest.fixtures import fixture_config, fixture_dataset_manifest, fixture_signals
from nexora.backtest.hashing import canonical_hash
from nexora.backtest.repository import BacktestRunStore
from nexora.backtest.runner import BacktestRunner
from nexora.risk import RiskEngine, replay_signals_with_risk, risk_policy_fixture


@dataclass(slots=True)
class BacktestLabService:
    runner: BacktestRunner
    store: BacktestRunStore

    @classmethod
    def bootstrap(cls) -> BacktestLabService:
        service = cls(runner=BacktestRunner(), store=BacktestRunStore())
        service.seed_default_runs()
        return service

    def seed_default_runs(self) -> None:
        if self.store.list_runs():
            return
        dataset = fixture_dataset_manifest()
        dataset_hash = canonical_hash(dataset)
        signals = fixture_signals()
        for mode in ("baseline", "fixed_pnf", "adaptive_pnf"):
            config = fixture_config(mode)
            run = self.runner.run(
                dataset=dataset,
                config=config,
                expected_dataset_hash=dataset_hash,
                signals=signals,
            )
            self.store.append(run)

    def list_runs(self) -> tuple[dict[str, object], ...]:
        return self.store.list_runs()

    def compare(self, run_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        return self.store.compare(run_ids)

    def risk_replay(self) -> dict[str, object]:
        engine = RiskEngine(risk_policy_fixture())
        result = replay_signals_with_risk(
            engine=engine,
            signals=fixture_signals(),
            quality_status="complete",
            starting_equity=Decimal("10000"),
        )
        return {
            "accepted": result.accepted,
            "rejected": result.rejected,
            "decisions": [asdict(decision) for decision in result.decisions],
            "state": asdict(engine.state()),
        }
