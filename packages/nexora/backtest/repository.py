"""Durable backtest artifact store with explicit injected journal."""

from __future__ import annotations

from nexora.artifacts import canonical_serialize
from nexora.backtest.models import BacktestRun
from nexora.storage import Journal, SQLiteJournal


class BacktestRunStore:
    def __init__(self, journal: Journal | None = None) -> None:
        self.journal = journal if journal is not None else SQLiteJournal(":memory:")

    def append(self, run: BacktestRun) -> None:
        self.journal.append("backtest-runs", run.run_id, canonical_serialize(run))

    def list_runs(self) -> tuple[dict[str, object], ...]:
        return self.journal.read("backtest-runs")

    def compare(self, run_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        return tuple(item for item in self.list_runs() if item["run_id"] in run_ids)
