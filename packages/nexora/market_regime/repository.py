"""Persistence contract for regime snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.market_regime.models import RegimeSnapshot


class RegimeSnapshotStore:
    def __init__(self) -> None:
        self._rows: list[str] = []

    def append(self, snapshot: RegimeSnapshot) -> None:
        payload = asdict(snapshot)
        payload["state"]["effective_time"] = snapshot.state.effective_time.isoformat()
        self._rows.append(json.dumps(payload, sort_keys=True, default=str))

    def replay(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(row) for row in self._rows)

    def rebuild(self) -> tuple[dict[str, object], ...]:
        return self.replay()
