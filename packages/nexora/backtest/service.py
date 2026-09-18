"""Backtest lab service for API consumption."""

from __future__ import annotations

from dataclasses import dataclass

from nexora.backtest.fixtures import fixture_config, fixture_dataset_manifest, fixture_signals
from nexora.backtest.hashing import canonical_hash
from nexora.backtest.repository import BacktestRunStore
from nexora.backtest.runner import BacktestRunner


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
