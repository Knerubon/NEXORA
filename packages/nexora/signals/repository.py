"""Persistence contract for signal snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.signals.models import SignalSnapshot


class SignalSnapshotStore:
    def __init__(self) -> None:
        self._rows: list[str] = []

    def append(self, snapshot: SignalSnapshot) -> None:
        payload = asdict(snapshot)
        latest = snapshot.latest
        if payload["latest"] is not None and latest is not None:
            payload["latest"]["occurrence_time"] = latest.occurrence_time.isoformat()
            payload["latest"]["confirmation_time"] = latest.confirmation_time.isoformat()
            payload["latest"]["decision_time"] = latest.decision_time.isoformat()
        for signal in payload["history"]:
            signal["occurrence_time"] = signal["occurrence_time"].isoformat()
            signal["confirmation_time"] = signal["confirmation_time"].isoformat()
            signal["decision_time"] = signal["decision_time"].isoformat()
        self._rows.append(json.dumps(payload, sort_keys=True, default=str))

    def replay(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(row) for row in self._rows)

    def rebuild(self) -> tuple[dict[str, object], ...]:
        return self.replay()
