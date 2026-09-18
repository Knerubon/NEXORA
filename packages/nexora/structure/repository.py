"""Persistence contract for structure snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.structure.models import StructureSnapshot


class StructureSnapshotStore:
    def __init__(self) -> None:
        self._rows: list[str] = []

    def append(self, snapshot: StructureSnapshot) -> None:
        payload = asdict(snapshot)
        for pivot in payload["pivots"]:
            pivot["occurrence_time"] = pivot["occurrence_time"].isoformat()
            pivot["confirmation_time"] = pivot["confirmation_time"].isoformat()
        for level in payload["levels"]:
            level["updated_at"] = level["updated_at"].isoformat()
        self._rows.append(json.dumps(payload, sort_keys=True, default=str))

    def replay(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(row) for row in self._rows)

    def rebuild(self) -> tuple[dict[str, object], ...]:
        return self.replay()
