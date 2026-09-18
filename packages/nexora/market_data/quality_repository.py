"""Persistence contract for market-data quality sidecar snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.market_data.quality import QualitySnapshot


class QualitySnapshotStore:
    def __init__(self) -> None:
        self._rows: list[str] = []

    def append(self, snapshot: QualitySnapshot) -> None:
        payload = asdict(snapshot)
        payload["observed_at"] = snapshot.observed_at.isoformat()
        self._rows.append(json.dumps(payload, sort_keys=True, default=str))

    def replay(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(row) for row in self._rows)

    def rebuild(self) -> tuple[dict[str, object], ...]:
        return self.replay()
