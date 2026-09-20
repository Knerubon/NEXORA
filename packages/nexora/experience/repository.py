"""Append-only Experience artifacts in the existing PostgreSQL/SQLite journal."""

from __future__ import annotations

from typing import Any

from nexora.artifacts import canonical_serialize, decode
from nexora.experience.models import HORIZONS, Experience
from nexora.storage import Journal

SNAPSHOTS = "experience:v1:snapshots"


class ExperienceRepository:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def save(self, experience: Experience) -> None:
        self.journal.append(SNAPSHOTS, experience.experience_id, experience)

    def all(self) -> tuple[Experience, ...]:
        return tuple(decode(Experience, row) for row in self.journal.read(SNAPSHOTS))

    def get(self, experience_id: str) -> Experience | None:
        return next((e for e in self.all() if e.experience_id == experience_id), None)

    def outcomes(self, experience_id: str) -> tuple[dict[str, Any], ...]:
        return self.journal.read(f"experience:v1:{experience_id}:outcomes")

    def lifecycle(self, experience_id: str) -> tuple[dict[str, Any], ...]:
        return self.journal.read(f"experience:v1:{experience_id}:lifecycle")

    def detail(self, experience_id: str) -> dict[str, Any] | None:
        experience = self.get(experience_id)
        if experience is None:
            return None
        result = dict(canonical_serialize(experience))
        result.pop("context_json")
        result["context"] = experience.context()
        result["lifecycle"] = self.lifecycle(experience_id)
        return result

    def recent(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        ordered = sorted(self.all(), key=lambda e: (e.t0, e.experience_id), reverse=True)
        return [
            {
                "experience_id": e.experience_id,
                "scope": e.scope,
                "t0": e.t0,
                "action": e.action,
                "symbol": e.context()["event"]["symbol"],
                "source": e.context()["event"]["source"],
                "state": (self.lifecycle(e.experience_id) or ({"state": "pending"},))[-1]["state"],
                "completed_horizons": len(self.outcomes(e.experience_id)),
            }
            for e in ordered[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
        ]

    def summary(self) -> dict[str, Any]:
        experiences = self.all()
        completed = sum(len(self.outcomes(e.experience_id)) == len(HORIZONS) for e in experiences)
        return {
            "total": len(experiences),
            "completed": completed,
            "pending": len(experiences) - completed,
            "by_action": {
                action: sum(e.action == action for e in experiences)
                for action in ("BUY", "SELL", "WAIT")
            },
            "unavailable_decision": sum(e.action is None for e in experiences),
        }

    def raw_observations(
        self,
        experience_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        experience = self.get(experience_id)
        if experience is None:
            return []
        # Shared source facts are referenced, not copied into every Experience.
        t0 = canonical_serialize(experience.t0)
        final = next((r for r in self.outcomes(experience_id) if r["horizon_minutes"] == 60), None)
        rows = []
        for row in self.journal.read(f"experience:v1:{experience.scope}:observations"):
            if row["event"]["event_time"] > t0 and row["event"]["received_at"] > t0:
                rows.append(row)
            if final is not None and row["event"]["identity_key"] == final["endpoint_event_id"]:
                break
        return rows[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
