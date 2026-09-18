"""Persistence contract for backtest runs."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.backtest.models import BacktestRun


class BacktestRunStore:
    def __init__(self) -> None:
        self._rows: list[str] = []

    def append(self, run: BacktestRun) -> None:
        payload = asdict(run)
        payload["started_at"] = run.started_at.isoformat()
        payload["completed_at"] = run.completed_at.isoformat()
        for signal in payload["signals"]:
            signal["occurrence_time"] = signal["occurrence_time"].isoformat()
            signal["confirmation_time"] = signal["confirmation_time"].isoformat()
            signal["decision_time"] = signal["decision_time"].isoformat()
        for trade in payload["trades"]:
            trade["entry_time"] = trade["entry_time"].isoformat()
            trade["exit_time"] = trade["exit_time"].isoformat()
        self._rows.append(json.dumps(payload, sort_keys=True, default=str))

    def list_runs(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(row) for row in self._rows)

    def compare(self, run_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        rows = self.list_runs()
        return tuple(item for item in rows if item["run_id"] in run_ids)
