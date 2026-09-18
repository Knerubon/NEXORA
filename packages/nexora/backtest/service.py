"""Read stored results; never bootstrap demo trades into production state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexora.backtest.repository import BacktestRunStore
from nexora.backtest.runner import BacktestRunner
from nexora.research.runtime import ResearchRuntime
from nexora.storage import Journal


@dataclass(slots=True)
class BacktestLabService:
    runner: BacktestRunner
    store: BacktestRunStore
    runtime: ResearchRuntime | None = None

    @classmethod
    def bootstrap(
        cls, journal: Journal | None = None, runtime: ResearchRuntime | None = None
    ) -> BacktestLabService:
        return cls(BacktestRunner(), BacktestRunStore(journal), runtime)

    def list_runs(self) -> tuple[dict[str, object], ...]:
        return self.store.list_runs()

    def compare(self, run_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        return self.store.compare(run_ids)

    def paper_replay(self) -> dict[str, Any]:
        if self.runtime is None or self.runtime.paper is None:
            return {
                "status": "unavailable",
                "state": {"status": "unavailable"},
                "orders": [],
                "fills": [],
                "ledger": [],
                "decisions": [],
                "accepted": 0,
                "rejected": 0,
                "reason": "paper_not_configured",
            }
        return self.runtime.paper.snapshot()

    def risk_replay(self) -> dict[str, Any]:
        paper = self.paper_replay()
        return {
            "status": paper["status"],
            "accepted": paper["accepted"],
            "rejected": paper["rejected"],
            "decisions": paper["decisions"],
            "state": paper.get("risk_state"),
        }

    def paper_snapshot(self) -> dict[str, Any]:
        paper = self.paper_replay()
        return {
            "status": paper["status"],
            "accepted": paper["accepted"],
            "rejected": paper["rejected"],
            "fills": len(paper["fills"]),
        }
