"""Durable observation orchestration with explicit research/paper configuration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import RLock
from typing import Any

from nexora.artifacts import canonical_hash, decode
from nexora.experience import ExperienceService
from nexora.market_data.models import NormalizedPriceEvent
from nexora.paper.session import PaperSession, PaperSessionConfig
from nexora.research import PipelineConfig, ResearchPipeline
from nexora.signals import ResearchSignal
from nexora.storage import Journal


@dataclass(frozen=True)
class RuntimeConfig:
    pipeline: PipelineConfig
    units: str
    paper: PaperSessionConfig | None = None
    implementation_version: str = "research-pipeline-v2"


class ResearchRuntime:
    def __init__(self, config: RuntimeConfig, journal: Journal) -> None:
        self.config, self.journal = config, journal
        self.stream = "research:" + canonical_hash(config)
        self._lock = RLock()
        self.error: str | None = None
        journal.append(self.stream + ":config", "config", config)
        self.paper = PaperSession(config.paper, journal) if config.paper is not None else None
        self._rebuild()

    def _rebuild(self) -> None:
        self.engine = ResearchPipeline(self.config.pipeline)
        self.experience = ExperienceService(self.journal, self.config, self.stream)
        self._events: list[NormalizedPriceEvent] = []
        for row in self.journal.iter_read(self.stream):
            event = decode(NormalizedPriceEvent, row["event"])
            self.engine.replay(event)
            self._events.append(event)
            self._paper_event(event, str(row.get("completeness", "unknown")), row["output"])
            self.experience.observe(
                event,
                row["output"],
                completeness=str(row.get("completeness", "unknown")),
                metadata=row.get("observation_metadata"),
            )
            if len(self._events) % 1000 == 0:
                logging.getLogger("uvicorn.error").info(
                    "Research recovery: replayed %d events", len(self._events)
                )

    def ingest(
        self,
        event: NormalizedPriceEvent,
        *,
        completeness: str = "unknown",
        observation_metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            try:
                if event.units != self.config.units:
                    raise ValueError("event_units_mismatch")
                for stored in self._events:
                    if stored.identity_key == event.identity_key:
                        if canonical_hash(stored) != canonical_hash(event):
                            raise ValueError("event_identity_conflict")
                        return
                output = self.engine.process(event)
                inserted = self.journal.append(
                    self.stream,
                    event.identity_key,
                    {
                        "event": event,
                        "output": output,
                        "completeness": completeness,
                        "observation_metadata": observation_metadata,
                    },
                    expected_count=len(self._events),
                )
                if inserted:
                    self._events.append(event)
                self._paper_event(event, completeness, output)
                self.experience.observe(
                    event,
                    output,
                    completeness=completeness,
                    metadata=observation_metadata,
                )
                self.error = None
            except Exception:
                self.error = "research_processing_failed"
                self._rebuild()
                raise

    def _paper_event(
        self, event: NormalizedPriceEvent, completeness: str, output: dict[str, Any]
    ) -> None:
        if self.paper is not None:
            # Recorded decisions are immutable even when a newer engine rebuilds
            # research state. Never retroactively submit its new interpretation.
            payload = output.get("signals", {}).get("latest")
            latest = decode(ResearchSignal, payload) if payload is not None else None
            if (
                latest is not None
                and latest.status == "active"
                and latest.decision_time == event.received_at
            ):
                for row in self.journal.read(self.paper.stream):
                    if row.get("signal_id") == latest.signal_id:
                        # Older serialized decisions lack the additive defaults.
                        # Accept the original hash or its typed reconstruction,
                        # but retain identity-conflict rejection for changed data.
                        if row["signal_hash"] not in {
                            canonical_hash(payload),
                            canonical_hash(latest),
                        }:
                            raise ValueError("signal_identity_conflict")
                        return
                self.paper.submit(latest, price=event.price, quality=completeness)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": self.journal.backend,
                "error": self.error,
                "event_count": len(self._events),
                "output": self.engine.snapshot(),
            }

    def events(self) -> tuple[NormalizedPriceEvent, ...]:
        with self._lock:
            return tuple(self._events)
