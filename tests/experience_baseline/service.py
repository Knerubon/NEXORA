"""Internal post-decision observer. Recovery consumes original committed runtime rows."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from nexora.artifacts import canonical_hash
from nexora.experience.models import HORIZONS, Experience
from nexora.experience.repository import ExperienceRepository
from nexora.market_data.models import NormalizedPriceEvent
from nexora.storage import Journal

from tests.experience_baseline.engine import advance, eligible, freeze, initial_lifecycle, measure


class ExperienceService:
    """Single writer, rebuilt in order from the durable runtime log on startup/retry."""

    def __init__(self, journal: Journal, config: Any, runtime_stream: str) -> None:
        self.repository = ExperienceRepository(journal)
        self.journal, self.config, self.runtime_stream = journal, config, runtime_stream
        self._last: dict[str, Experience] = {}
        self._pending: dict[str, Experience] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._samples: dict[str, list[dict[str, Any]]] = {}
        self._completed: dict[str, set[int]] = {}
        self._seen: dict[tuple[str, str], str] = {}

    def observe(
        self,
        event: NormalizedPriceEvent,
        output: dict[str, Any],
        *,
        completeness: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        candidate = freeze(
            self.config,
            self.runtime_stream,
            event,
            output,
            completeness,
            metadata,
        )
        scope = candidate.scope
        identity = (scope, event.identity_key)
        digest = canonical_hash((event, output, completeness, metadata))
        if identity in self._seen:
            if self._seen[identity] != digest:
                raise ValueError("experience_event_identity_conflict")
            return
        raw = {"event": event, "completeness": completeness, "metadata": metadata}
        # This commit precedes every outcome/lifecycle write using the observation.
        self.journal.append(f"experience:v1:{scope}:observations", event.identity_key, raw)
        last = self._last.get(scope)
        if last is None or candidate.fingerprint != last.fingerprint:
            self.repository.save(candidate)
            self._last[scope] = candidate
            self._pending[candidate.experience_id] = candidate
            self._samples[candidate.experience_id] = []
            self._completed[candidate.experience_id] = set()
            self._persist_state(candidate, initial_lifecycle(candidate), event, "initial")
        for eid, experience in list(self._pending.items()):
            if experience.scope != scope or not eligible(experience, event):
                continue
            self._samples[eid].append(raw)
            for index, state in enumerate(advance(experience, self._states[eid], event)):
                self._persist_state(experience, state, event, str(index))
            for minutes in HORIZONS:
                if minutes in self._completed[eid]:
                    continue
                if event.event_time < experience.t0 + timedelta(minutes=minutes):
                    continue
                outcome = measure(
                    experience,
                    minutes,
                    self._samples[eid],
                    event,
                )
                self.journal.append(f"experience:v1:{eid}:outcomes", str(minutes), outcome)
                self._completed[eid].add(minutes)
            if len(self._completed[eid]) == len(HORIZONS):
                state = self._states[eid]
                if state["state"] not in {"TP2", "INVALIDATED"}:
                    closing = (
                        "CLOSED"
                        if state["entered_at"] is not None
                        or (experience.action not in {"BUY", "SELL"})
                        else "EXPIRED"
                    )
                    self._persist_state(experience, {**state, "state": closing}, event, "close")
                del self._pending[eid]
                del self._samples[eid]
        self._seen[identity] = digest

    def checkpoint_state(self) -> dict[str, Any]:
        """Replay-derived memory only; every record it refers to is already journaled."""
        return {
            "last": self._last,
            "pending": self._pending,
            "states": self._states,
            "samples": self._samples,
            "completed": self._completed,
            "seen": self._seen,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Adopt memory captured by `checkpoint_state` after the same journal rows."""
        self._last = state["last"]
        self._pending = state["pending"]
        self._states = state["states"]
        self._samples = state["samples"]
        self._completed = state["completed"]
        self._seen = state["seen"]

    def _persist_state(
        self,
        experience: Experience,
        state: dict[str, Any],
        event: NormalizedPriceEvent,
        suffix: str,
    ) -> None:
        self.journal.append(
            f"experience:v1:{experience.experience_id}:lifecycle",
            f"{event.identity_key}:{suffix}",
            {
                **state,
                "event_id": event.identity_key,
                "event_time": event.event_time,
                "recorded_at": event.received_at,
                "hypothetical_only": True,
            },
        )
        self._states[experience.experience_id] = state
