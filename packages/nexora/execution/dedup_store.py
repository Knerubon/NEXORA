"""Durable duplicate-order-safety store (ADR-034 sections 6/11, IDEMPOTENCY-1).

Broker-agnostic. Keys are the frozen ``execution_request_idempotency_key``
values; this module never derives or alters them. Persistence reuses the repo's
existing ``Journal`` abstraction (``nexora.storage``): append-only, content
hashed, first-writer-wins per ``(stream, key)``, so no new table or migration is
introduced. One journal stream holds the events of one idempotency key:

* ``claim#<g>``            -- generation ``g`` of the key was claimed
* ``result#<g>|<id>``      -- an ``ExecutionResult`` observed in generation ``g``
* ``release#<g>``          -- generation ``g`` ended in a clean zero-fill
                              REJECTED and was released for retry

The generation is the count of release events. Only a clean zero-fill REJECTED
(``is_safe_to_retry_without_reconciliation``) may be released; UNKNOWN,
ACCEPTED, PARTIALLY_FILLED and FILLED never are. A crash between claim and
result leaves the key claimed. Any unreadable or inconsistent stored state
raises ``DedupStoreCorruptError`` -- it is never treated as "unseen".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from nexora.artifacts import canonical_serialize
from nexora.autonomous_contracts import TradeIntentKind
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionResult,
    ExecutionStatus,
    is_safe_to_retry_without_reconciliation,
)
from nexora.storage import Journal

STREAM_PREFIX = "execution-dedup:"


class DedupStoreError(ValueError):
    """Invalid use of the dedup store (blank key, result without claim, ...)."""


class DedupStoreCorruptError(DedupStoreError):
    """Stored state is unreadable or inconsistent. Callers must deny, not retry."""


class _ConcurrentWrite(Exception):
    """Internal: optimistic append lost a race; the caller treats it as 'not done'."""


class ClaimOutcome(StrEnum):
    FIRST_CLAIM = "first_claim"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True, kw_only=True)
class DedupRecord:
    idempotency_key: str
    generation: int
    latest_result: ExecutionResult | None


class ExecutionDedupStore(Protocol):
    def claim(self, idempotency_key: str) -> ClaimOutcome:
        """Atomically claim the key. FIRST_CLAIM for exactly one caller."""
        ...

    def record_result(self, idempotency_key: str, result: ExecutionResult) -> None:
        """Append the outcome against a currently claimed key."""
        ...

    def lookup(self, idempotency_key: str) -> DedupRecord | None:
        """Current claim state, or None if the key was never claimed."""
        ...

    def release_for_retry(self, idempotency_key: str) -> bool:
        """Release only if the latest result is a clean zero-fill REJECTED."""
        ...


def serialize_result(result: ExecutionResult) -> dict[str, Any]:
    value = canonical_serialize(result)
    if not isinstance(value, dict):  # pragma: no cover
        raise DedupStoreError("result_not_serializable")
    return value


def deserialize_result(raw: object) -> ExecutionResult:
    try:
        if not isinstance(raw, dict):
            raise TypeError("result_not_object")

        def dec(name: str) -> Decimal | None:
            item = raw.get(name)
            return None if item is None else Decimal(str(item))

        requested = dec("requested_quantity")  # None only for MODIFY_PROTECTION (ADR-035)
        filled = dec("filled_quantity")
        if filled is None:
            raise TypeError("missing_quantity")
        raw_action = raw.get("action")
        return ExecutionResult(
            result_id=raw["result_id"],
            request_ref=raw["request_ref"],
            status=ExecutionStatus(raw["status"]),
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=dec("remaining_quantity"),
            broker_order_ref=raw.get("broker_order_ref"),
            broker_deal_ref=raw.get("broker_deal_ref"),
            execution_price=dec("execution_price"),
            reason_code=raw.get("reason_code"),
            observed_at=datetime.fromisoformat(raw["observed_at"]),
            action=None if raw_action is None else TradeIntentKind(raw_action),
            nexora_position_ref=raw.get("nexora_position_ref"),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, ExecutionContractError) as exc:
        raise DedupStoreCorruptError("dedup_result_unreadable") from exc


class JournalExecutionDedupStore:
    """``ExecutionDedupStore`` over an injected ``Journal`` (SQLite/PostgreSQL)."""

    def __init__(self, journal: Journal) -> None:
        self._journal = journal
        self._lock = RLock()

    # -- public API ---------------------------------------------------------

    def claim(self, idempotency_key: str) -> ClaimOutcome:
        stream = _stream(idempotency_key)
        with self._lock:
            generation, _ = self._state(idempotency_key)
            first = self._append(
                stream,
                f"claim#{generation}",
                {"event": "claim", "generation": generation, "idempotency_key": idempotency_key},
            )
        return ClaimOutcome.FIRST_CLAIM if first else ClaimOutcome.DUPLICATE

    def record_result(self, idempotency_key: str, result: ExecutionResult) -> None:
        with self._lock:
            generation, claimed = self._state(idempotency_key)
            if not claimed:
                raise DedupStoreError("result_without_claim")
            self._append(
                _stream(idempotency_key),
                f"result#{generation}|{result.result_id}",
                {
                    "event": "result",
                    "generation": generation,
                    "idempotency_key": idempotency_key,
                    "result": serialize_result(result),
                },
            )

    def lookup(self, idempotency_key: str) -> DedupRecord | None:
        with self._lock:
            events = self._events(idempotency_key)
            generation, claimed = self._state(idempotency_key, events)
            if not claimed:
                return None
            return DedupRecord(
                idempotency_key=idempotency_key,
                generation=generation,
                latest_result=self._latest_result(events, generation),
            )

    def release_for_retry(self, idempotency_key: str) -> bool:
        """True only if THIS call durably wrote the release.

        The release append is optimistic: it carries the event count that was
        read, so any concurrent write to the key's stream (another instance or
        process recording a result, releasing, ...) makes it fail and the key
        stays claimed. Independently, ``_state`` re-checks every released
        generation, so a stale writer cannot make an unsafe outcome re-claimable.
        """

        with self._lock:
            events = self._events(idempotency_key)
            generation, claimed = self._state(idempotency_key, events)
            if not claimed:
                return False
            latest = self._latest_result(events, generation)
            if latest is None or not is_safe_to_retry_without_reconciliation(latest):
                return False
            try:
                return self._append(
                    _stream(idempotency_key),
                    f"release#{generation}",
                    {
                        "event": "release",
                        "generation": generation,
                        "idempotency_key": idempotency_key,
                        "released_result_id": latest.result_id,
                    },
                    expected_count=len(events),
                )
            except _ConcurrentWrite:
                return False

    # -- internals ----------------------------------------------------------

    def _append(
        self,
        stream: str,
        key: str,
        payload: dict[str, Any],
        *,
        expected_count: int | None = None,
    ) -> bool:
        try:
            return bool(self._journal.append(stream, key, payload, expected_count=expected_count))
        except ValueError as exc:
            if str(exc) == "journal_concurrent_write":
                raise _ConcurrentWrite from exc
            raise DedupStoreCorruptError("dedup_store_write_failed") from exc
        except Exception as exc:  # fail closed on any storage failure
            raise DedupStoreCorruptError("dedup_store_write_failed") from exc

    def _events(self, idempotency_key: str) -> tuple[dict[str, Any], ...]:
        try:
            events = self._journal.read(_stream(idempotency_key))
        except Exception as exc:  # fail closed on any storage failure
            raise DedupStoreCorruptError("dedup_store_unreadable") from exc
        for event in events:
            if (
                not isinstance(event, dict)
                or event.get("idempotency_key") != idempotency_key
                or event.get("event") not in ("claim", "result", "release")
                or not isinstance(event.get("generation"), int)
            ):
                raise DedupStoreCorruptError("dedup_event_malformed")
        return events

    def _state(
        self, idempotency_key: str, events: tuple[dict[str, Any], ...] | None = None
    ) -> tuple[int, bool]:
        """(current generation, whether that generation is claimed).

        Fails closed (raises) if any *released* generation's latest result, by
        journal order, is not a clean zero-fill REJECTED -- e.g. a result that
        landed after the release. Such a key must never become re-claimable.
        """

        if events is None:
            events = self._events(idempotency_key)
        releases = sorted(e["generation"] for e in events if e["event"] == "release")
        claims = {e["generation"] for e in events if e["event"] == "claim"}
        generation = len(releases)
        if releases != list(range(generation)):
            raise DedupStoreCorruptError("dedup_release_sequence_broken")
        if any(g not in claims for g in releases):
            raise DedupStoreCorruptError("dedup_release_without_claim")
        if any(g > generation for g in claims):
            raise DedupStoreCorruptError("dedup_claim_beyond_generation")
        if any(
            e["generation"] > generation or e["generation"] not in claims
            for e in events
            if e["event"] == "result"
        ):
            raise DedupStoreCorruptError("dedup_result_without_claim")
        for released in range(generation):
            last = self._latest_result(events, released)
            if last is None or not is_safe_to_retry_without_reconciliation(last):
                raise DedupStoreCorruptError("dedup_released_generation_not_safe")
        return generation, generation in claims

    @staticmethod
    def _latest_result(
        events: tuple[dict[str, Any], ...], generation: int
    ) -> ExecutionResult | None:
        latest: ExecutionResult | None = None
        for event in events:
            if event["event"] == "result" and event["generation"] == generation:
                latest = deserialize_result(event.get("result"))
        return latest


class _MemoryJournal:
    """Minimal in-process ``Journal`` with the same first-writer-wins contract."""

    backend = "memory"

    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: dict[str, dict[str, tuple[str, str]]] = {}

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        encoded = json.dumps(canonical_serialize(payload), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self._lock:
            rows = self._rows.setdefault(stream, {})
            if key in rows:
                if rows[key][0] != digest:
                    raise ValueError("journal_identity_conflict")
                return False
            if expected_count is not None and len(rows) != expected_count:
                raise ValueError("journal_concurrent_write")
            rows[key] = (digest, encoded)
            return True

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(json.loads(enc) for _, enc in self._rows.get(stream, {}).values())

    def iter_read(self, stream: str) -> Iterator[dict[str, Any]]:
        yield from self.read(stream)

    def close(self) -> None:
        return None


class InMemoryExecutionDedupStore(JournalExecutionDedupStore):
    """Process-local store (tests, paper). Same semantics, no durability."""

    def __init__(self) -> None:
        super().__init__(_MemoryJournal())


def _stream(idempotency_key: str) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise DedupStoreError("blank_idempotency_key")
    return f"{STREAM_PREFIX}{idempotency_key}"
