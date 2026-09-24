"""Durable observation orchestration with explicit research/paper configuration."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from nexora.artifacts import canonical_hash, decode
from nexora.experience import ExperienceService
from nexora.market_data.models import NormalizedPriceEvent
from nexora.paper.session import PaperSession, PaperSessionConfig
from nexora.research import PipelineConfig, ResearchPipeline, checkpoint_state
from nexora.research import checkpoint as ckpt
from nexora.signals import ResearchSignal
from nexora.storage import Journal

# Live events between periodic checkpoints; bounds a crash restart's replay delta (ADR-022).
DEFAULT_CHECKPOINT_INTERVAL = 100
_log = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class RuntimeConfig:
    pipeline: PipelineConfig
    units: str
    paper: PaperSessionConfig | None = None
    implementation_version: str = "research-pipeline-v2"


class ResearchRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        journal: Journal,
        *,
        checkpoints: ckpt.CheckpointStore | None = None,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
    ) -> None:
        if checkpoint_interval < 0:
            raise ValueError("invalid_checkpoint_interval")
        self.config, self.journal = config, journal
        self.stream = "research:" + canonical_hash(config)
        self._lock = RLock()
        self.error: str | None = None
        self.checkpoints, self.checkpoint_interval = checkpoints, checkpoint_interval
        self.last_recovery: dict[str, Any] = {}
        journal.append(self.stream + ":config", "config", config)
        self.paper = PaperSession(config.paper, journal) if config.paper is not None else None
        self._rebuild()

    def _anchored(self) -> ckpt.AnchoredJournal | None:
        return self.journal if isinstance(self.journal, ckpt.AnchoredJournal) else None

    def _rebuild(self) -> None:
        """Rebuild from the journal, from a verified checkpoint onward when one is usable."""
        started = time.monotonic()
        # Only a completed rebuild or ingest leaves state that a checkpoint may capture.
        # An interrupted rebuild can leave the last row's side effects unapplied in
        # memory; no checkpoint is written again until a later rebuild completes.
        self._recovered = self._consistent = False
        self._since_checkpoint = 0
        self._last_sequence = 0
        self.engine = ResearchPipeline(self.config.pipeline)
        self.experience = ExperienceService(self.journal, self.config, self.stream)
        self._events: list[NormalizedPriceEvent] = []
        anchored = self._anchored()
        recovery: dict[str, Any] = {"mode": "full", "reason": None, "checkpoint_sequence": None}
        if self.checkpoints is None:
            recovery["reason"] = "checkpoints_disabled"
        elif anchored is None:
            recovery["reason"] = "journal_not_anchored"
            _log.info("Research recovery: journal backend has no row anchors; full replay")
        else:
            recovery.update(self._restore(self.checkpoints, anchored))
        rows: Iterator[tuple[int, dict[str, Any]]] = (
            anchored.iter_rows(self.stream, after=self._last_sequence)
            if anchored is not None
            else ((0, row) for row in self.journal.iter_read(self.stream))
        )
        replayed = 0
        for sequence, row in rows:
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
            self._last_sequence = sequence
            replayed += 1
            if replayed % 1000 == 0:
                _log.info("Research recovery: replayed %d events", replayed)
        self._recovered = self._consistent = True
        recovery["replayed"] = replayed
        recovery["events"] = len(self._events)
        recovery["duration_ms"] = round((time.monotonic() - started) * 1000)
        self.last_recovery = recovery
        _log.info(
            "Research recovery: completed mode=%s replayed=%d events=%d in %d ms",
            recovery["mode"],
            replayed,
            len(self._events),
            recovery["duration_ms"],
        )
        if recovery["mode"] == "full" or replayed:
            self._write_checkpoint()

    def _restore(
        self, store: ckpt.CheckpointStore, journal: ckpt.AnchoredJournal
    ) -> dict[str, Any]:
        try:
            loaded = store.load(self.stream)
            if loaded is None:
                _log.info("Research recovery: no checkpoint; full replay")
                return {"reason": "no_checkpoint"}
            header, blob = loaded
            _log.info(
                "Research recovery: checkpoint found checkpoint_sequence=%d events=%d",
                header.last_sequence,
                header.event_count,
            )
            payload = ckpt.verify(
                header, blob, environment=store.environment, stream=self.stream, journal=journal
            )
            try:
                # Explicit per-component contracts onto freshly constructed components.
                engine, events, memory = checkpoint_state.restore_state(
                    payload, self.config.pipeline
                )
            except checkpoint_state.StateInvalid:
                raise ckpt.CheckpointRejected("state_invalid") from None
            if (
                not events
                or len(events) != header.event_count
                or events[-1].identity_key != header.last_event_key
                or ckpt.state_hash(engine.snapshot()) != header.state_hash
            ):
                raise ckpt.CheckpointRejected("state_validation_failed")
            experience = ExperienceService(self.journal, self.config, self.stream)
            experience.restore_state(memory)
        except Exception as error:
            reason = (
                error.reason if isinstance(error, ckpt.CheckpointRejected) else "checkpoint_corrupt"
            )
            _log.warning(
                "Research recovery: checkpoint invalid reason=%s; falling back to full replay",
                reason,
            )
            return {"reason": reason}
        # Adopt nothing until every check passed; the journal alone decides the rest.
        self.engine, self.experience, self._events = engine, experience, list(events)
        self._last_sequence = header.last_sequence
        _log.info("Research recovery: restored checkpoint at sequence %d", header.last_sequence)
        return {"mode": "checkpoint", "checkpoint_sequence": header.last_sequence}

    def _write_checkpoint(self) -> None:
        """Capture consistent state; a failure never affects research processing."""
        store, journal = self.checkpoints, self._anchored()
        if store is None or journal is None or not self._events:
            return
        if not (self._recovered and self._consistent):
            return
        started = time.monotonic()
        try:
            identity = journal.row_identity(self.stream, self._last_sequence)
            if identity is None or identity[0] != self._events[-1].identity_key:
                raise ValueError("checkpoint_anchor_mismatch")
            blob, blob_hash = ckpt.encode(
                checkpoint_state.encode_state(
                    self.engine, self._events, self.experience.checkpoint_state()
                )
            )
            store.save(
                ckpt.CheckpointHeader(
                    format=ckpt.FORMAT,
                    schema_version=ckpt.SCHEMA_VERSION,
                    environment=store.environment,
                    stream=self.stream,
                    code_fingerprint=ckpt.code_fingerprint(),
                    last_sequence=self._last_sequence,
                    last_event_key=identity[0],
                    last_content_hash=identity[1],
                    event_count=len(self._events),
                    state_hash=ckpt.state_hash(self.engine.snapshot()),
                    blob_hash=blob_hash,
                    blob_size=len(blob),
                    created_at=datetime.now(UTC).isoformat(),
                ),
                blob,
            )
        except Exception:
            _log.warning("Research checkpoint: write failed; journal unaffected", exc_info=True)
            return
        self._since_checkpoint = 0
        _log.info(
            "Research checkpoint: written sequence=%d events=%d bytes=%d in %d ms",
            self._last_sequence,
            len(self._events),
            len(blob),
            round((time.monotonic() - started) * 1000),
        )

    def checkpoint(self) -> None:
        """Write a checkpoint of events processed since the last one (e.g. at shutdown)."""
        with self._lock:
            if self._since_checkpoint:
                self._write_checkpoint()

    def ingest(
        self,
        event: NormalizedPriceEvent,
        *,
        completeness: str = "unknown",
        observation_metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if not self._recovered:
                # An interrupted rebuild may have stopped inside the last journal row, which
                # the journal's expected_count guard cannot detect; finish recovery first.
                try:
                    self._rebuild()
                except Exception:
                    self.error = "research_processing_failed"
                    raise
            try:
                if event.units != self.config.units:
                    raise ValueError("event_units_mismatch")
                for stored in self._events:
                    if stored.identity_key == event.identity_key:
                        if canonical_hash(stored) != canonical_hash(event):
                            raise ValueError("event_identity_conflict")
                        return
                self._consistent = False
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
            if inserted and (journal := self._anchored()) is not None:
                self._last_sequence = journal.sequence_of(self.stream, event.identity_key) or 0
                self._since_checkpoint += 1
            self._consistent = True
            if self.checkpoint_interval and self._since_checkpoint >= self.checkpoint_interval:
                self._write_checkpoint()

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
