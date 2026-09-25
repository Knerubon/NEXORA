"""Internal post-decision observer. Recovery consumes original committed runtime rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.experience.engine import (
    Plan,
    advance,
    current_signal,
    eligible,
    fingerprint,
    initial_lifecycle,
    materialize,
    measure,
    observation_digest,
    plan_of,
    recorded,
    scope_for,
)
from nexora.experience.models import HORIZONS, Experience
from nexora.experience.repository import ExperienceRepository
from nexora.market_data.models import NormalizedPriceEvent
from nexora.storage import Journal


@dataclass
class _Derived:
    """Values derived from the immutable config and pending Experiences (ADR-031).

    Never checkpointed: a restore starts empty and rebuilds them on demand, so
    checkpoint state and every journal write are unchanged.
    """

    config: tuple[dict[str, Any], str] | None = None
    scopes: dict[tuple[Any, ...], str] = field(default_factory=dict)
    plans: dict[str, Plan] = field(default_factory=dict)


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
        self._derived = _Derived()

    def observe(
        self,
        event: NormalizedPriceEvent,
        output: dict[str, Any],
        *,
        completeness: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # One canonical walk and encoding of the recorded output serves the fingerprint,
        # the digest and, only when a new Experience starts, the T0 snapshot (ADR-031).
        # Every value equals what `freeze()` and `canonical_hash` produce.
        config, config_hash = self._config()
        frame = recorded(output)
        scope = self._scope(config, event)
        # Reads what materialize() reads, so malformed recorded output fails on every event
        # as it did when the full context was built for each one.
        current_signal(frame.canonical, event)
        snapshot_fingerprint = fingerprint(scope, frame.canonical)
        identity = (scope, event.identity_key)
        digest = observation_digest(event, frame, completeness, metadata)
        if identity in self._seen:
            if self._seen[identity] != digest:
                raise ValueError("experience_event_identity_conflict")
            return
        last = self._last.get(scope)
        candidate = (
            materialize(
                config,
                config_hash,
                self.runtime_stream,
                event,
                frame,
                completeness,
                metadata,
                scope,
                snapshot_fingerprint,
            )
            if last is None or snapshot_fingerprint != last.fingerprint
            else None
        )
        raw = {"event": event, "completeness": completeness, "metadata": metadata}
        # This commit precedes every outcome/lifecycle write using the observation.
        self.journal.append(f"experience:v1:{scope}:observations", event.identity_key, raw)
        if candidate is not None:
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
            prepared = self._plan(experience)
            for index, state in enumerate(
                advance(experience, self._states[eid], event, prepared=prepared)
            ):
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
                    prepared=prepared,
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
                self._derived.plans.pop(eid, None)
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
        self._derived = _Derived()

    def _config(self) -> tuple[dict[str, Any], str]:
        """Canonical runtime config and its hash; the config is immutable per service."""
        if self._derived.config is None:
            config = canonical_serialize(self.config)
            self._derived.config = (config, canonical_hash(config))
        return self._derived.config

    def _scope(self, config: dict[str, Any], event: NormalizedPriceEvent) -> str:
        key = (event.source, event.symbol, event.price_source, event.units)
        scopes = self._derived.scopes
        if key not in scopes:
            scopes[key] = scope_for(config, event)
        return scopes[key]

    def _plan(self, experience: Experience) -> Plan:
        """`plan_of` once per pending Experience; it is immutable, so the result is too."""
        eid, plans = experience.experience_id, self._derived.plans
        if eid not in plans:
            plans[eid] = plan_of(experience)
        return plans[eid]

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
