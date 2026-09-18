"""Persistence contract for matrix snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.matrix.models import MatrixSnapshot


class MatrixSnapshotStore:
    def __init__(self) -> None:
        self._rows: list[str] = []

    def append(self, snapshot: MatrixSnapshot) -> None:
        payload = asdict(snapshot)
        payload["generated_at"] = snapshot.generated_at.isoformat()
        for resolution in payload["resolutions"]:
            transition = resolution["latest_transition"]
            if transition is not None:
                transition["event_time"] = transition["event_time"].isoformat()
        self._rows.append(json.dumps(payload, sort_keys=True, default=str))

    def replay(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(row) for row in self._rows)

    def rebuild(self) -> tuple[dict[str, object], ...]:
        return self.replay()
