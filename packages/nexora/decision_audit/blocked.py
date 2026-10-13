"""EVALUATION_BLOCKED audit record: an additive, isolated contract (ADR-036 D6).

It exists for the case where the SignalEngine *cannot safely evaluate* (the market data is
blocked or unverifiable). Such a moment is not a decision, so the record is deliberately a
different kind from ``DecisionAuditRecord``:

* separate ``record_kind``, separate ``blocked:`` id namespace, separate journal stream prefix
  (``audit-blocked``), so it can never be confused with, collide with or overwrite a decision;
* **no** ``action``, ``score``, ``signal_id`` or ``signal_emitted`` field exists on the type, so
  a BUY/SELL/WAIT, a score or a signal id cannot be fabricated for it, not even by mistake;
* it carries the event reference, the verified quality verdict, a stable reason code and an
  explicit timestamp, and it is durable, append-only and idempotent (same journal contract).

This module only *records*. It does not decide when the engine is skipped (a trading-semantics
decision reserved for Quant/Rin, AGENTS.md section 9) and is not wired into any runtime. A
recorded blocked evaluation can never carry eligibility: the result type has no such field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from nexora.artifacts import canonical_hash, decode
from nexora.data_quality import DataQualityGuard, MarketDataSnapshot, QualityVerdict
from nexora.decision_audit.builder import quality_reference
from nexora.decision_audit.identity import blocked_stream, validate_component
from nexora.decision_audit.identity import correlation_id as make_correlation_id
from nexora.decision_audit.invariants import obtain_verified_quality
from nexora.decision_audit.models import (
    AUDIT_SCHEMA_VERSION,
    AppendOutcome,
    AuditError,
    DataQualityRef,
    InstrumentIdentity,
    MarketDataReference,
)
from nexora.decision_audit.payload_policy import validate_payload
from nexora.decision_audit.store import append_once
from nexora.market_data.models import NormalizedPriceEvent
from nexora.storage import Journal

EVALUATION_BLOCKED_KIND: Literal["EVALUATION_BLOCKED"] = "EVALUATION_BLOCKED"
BLOCKED_ID_PREFIX = "blocked:"
BlockedReason = Literal["data_quality_blocked", "data_quality_unknown"]
_REASON_FOR_STATE: dict[str, BlockedReason] = {
    "blocked": "data_quality_blocked",
    "unknown": "data_quality_unknown",
}


@dataclass(frozen=True, slots=True)
class EvaluationBlockedRecord:
    schema_version: Literal[1]
    record_kind: Literal["EVALUATION_BLOCKED"]
    blocked_id: str  # "blocked:<sha256>"; never a decision_id and never a signal_id
    correlation_id: str
    # Caller-supplied evaluation time (the verdict's ``evaluated_at``); if the clock was
    # unsynchronized, the source event's ``received_at``. Never the wall clock.
    recorded_at: datetime
    environment: str
    instrument: InstrumentIdentity
    event: MarketDataReference
    quality: DataQualityRef
    reason_code: BlockedReason


class EvaluationBlockedOutcome(StrEnum):
    RECORDED = "recorded"
    REPLAYED = "replayed"  # identical record already stored; nothing written
    REJECTED = "rejected"  # nothing stored


@dataclass(frozen=True, slots=True)
class EvaluationBlockedResult:
    # No eligibility/authorization field exists here: a blocked evaluation opens nothing.
    outcome: EvaluationBlockedOutcome
    record: EvaluationBlockedRecord | None
    failure_code: str | None


def build_evaluation_blocked_record(
    *,
    event: NormalizedPriceEvent,
    snapshot: MarketDataSnapshot,
    guard: DataQualityGuard,
    evaluated_at: datetime | None,
    environment: str,
    instrument_id: str | None = None,
) -> EvaluationBlockedRecord:
    environment = validate_component(environment, "missing_environment")
    if not isinstance(event, NormalizedPriceEvent):
        raise AuditError("invalid_quality_input")
    for stamp in (event.event_time, event.received_at):
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise AuditError("invalid_event_time")
    quality = obtain_verified_quality(
        guard=guard, snapshot=snapshot, event=event, evaluated_at=evaluated_at
    )
    reason = _REASON_FOR_STATE.get(quality.state)
    if reason is None:
        # Only a non-ok verdict can block an evaluation; an ok one must not be recorded as such.
        raise AuditError("evaluation_not_blocked")
    recorded_at = _recorded_at(quality, event)
    blocked_id = BLOCKED_ID_PREFIX + canonical_hash(
        {
            "kind": "evaluation_blocked",
            "environment": environment,
            "symbol": event.symbol,
            "event": event.identity_key,
            "snapshot_hash": quality.snapshot_hash,
            "reason": reason,
            "recorded_at": recorded_at,
            "schema": AUDIT_SCHEMA_VERSION,
        }
    )
    record = EvaluationBlockedRecord(
        schema_version=AUDIT_SCHEMA_VERSION,
        record_kind=EVALUATION_BLOCKED_KIND,
        blocked_id=blocked_id,
        correlation_id=make_correlation_id(event.symbol, event.identity_key),
        recorded_at=recorded_at,
        environment=environment,
        instrument=InstrumentIdentity(event.symbol, event.source, event.units, instrument_id),
        event=MarketDataReference(
            event_identity_key=event.identity_key,
            event_time=event.event_time,
            received_at=event.received_at,
            source_sequence=event.source_sequence,
            price_source=event.price_source,
            snapshot_hash=quality.snapshot_hash,
        ),
        quality=quality_reference(quality),
        reason_code=reason,
    )
    check_blocked_invariants(record)
    return record


def check_blocked_invariants(record: EvaluationBlockedRecord) -> None:
    if not isinstance(record, EvaluationBlockedRecord):
        raise AuditError("invalid_record")
    validate_payload(record)
    quality = record.quality
    if (
        record.schema_version != AUDIT_SCHEMA_VERSION
        or record.record_kind != EVALUATION_BLOCKED_KIND
        or not record.blocked_id.startswith(BLOCKED_ID_PREFIX)
        or quality.state not in _REASON_FOR_STATE
        or quality.new_trade_permitted is not False
        or _REASON_FOR_STATE[quality.state] != record.reason_code
        or record.event.snapshot_hash != quality.snapshot_hash
        or not record.environment
    ):
        raise AuditError("blocked_record_inconsistent")


class EvaluationBlockedStore:
    """Append-only, idempotent store on its own stream prefix; no update/delete surface."""

    def __init__(self, journal: Journal, *, environment: str) -> None:
        self._journal = journal
        self._environment = validate_component(environment, "missing_environment")

    @property
    def environment(self) -> str:
        return self._environment

    def stream(self, symbol: str) -> str:
        return blocked_stream(self._environment, symbol)

    def append(self, record: EvaluationBlockedRecord) -> AppendOutcome:
        check_blocked_invariants(record)
        if record.environment != self._environment:
            raise AuditError("audit_environment_mismatch")
        return append_once(
            self._journal, self.stream(record.instrument.symbol), record.blocked_id, record
        )

    def read(self, symbol: str) -> tuple[EvaluationBlockedRecord, ...]:
        try:
            rows = self._journal.read(self.stream(symbol))
            return tuple(decode(EvaluationBlockedRecord, row) for row in rows)
        except AuditError:
            raise
        except Exception:
            raise AuditError("audit_read_failed") from None


class EvaluationBlockedRecorder:
    """Single entry point. It records a blocked evaluation; it can never authorize anything."""

    def __init__(self, store: EvaluationBlockedStore, guard: DataQualityGuard) -> None:
        if not isinstance(store, EvaluationBlockedStore):
            raise TypeError("invalid_store")
        if not isinstance(guard, DataQualityGuard):
            raise TypeError("invalid_guard")
        self._store = store
        self._guard = guard

    def record_blocked(
        self,
        *,
        event: NormalizedPriceEvent,
        snapshot: MarketDataSnapshot,
        evaluated_at: datetime | None,
        instrument_id: str | None = None,
    ) -> EvaluationBlockedResult:
        try:
            record = build_evaluation_blocked_record(
                event=event,
                snapshot=snapshot,
                guard=self._guard,
                evaluated_at=evaluated_at,
                environment=self._store.environment,
                instrument_id=instrument_id,
            )
            appended = self._store.append(record)
        except AuditError as exc:
            return EvaluationBlockedResult(EvaluationBlockedOutcome.REJECTED, None, exc.code)
        except Exception:
            return EvaluationBlockedResult(
                EvaluationBlockedOutcome.REJECTED, None, "audit_unexpected_failure"
            )
        outcome = (
            EvaluationBlockedOutcome.REPLAYED
            if appended is AppendOutcome.REPLAYED
            else EvaluationBlockedOutcome.RECORDED
        )
        return EvaluationBlockedResult(outcome, record, None)


def _recorded_at(quality: QualityVerdict, event: NormalizedPriceEvent) -> datetime:
    return quality.evaluated_at or event.received_at.astimezone(UTC)
