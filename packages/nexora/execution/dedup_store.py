"""Durable duplicate-order-safety store (ADR-034 sections 6/11, IDEMPOTENCY-1; ADR-035 PR-5).

Broker-agnostic. Keys are the frozen ``execution_request_idempotency_key``
values; this module never derives or alters them. Persistence reuses the repo's
existing ``Journal`` abstraction (``nexora.storage``): append-only, content
hashed, first-writer-wins per ``(stream, key)``, so no new table or migration is
introduced. One journal stream holds the events of one idempotency key:

* ``claim#<g>``            -- generation ``g`` of the key was claimed
* ``attempt#<g>``          -- ADR-035 s3.6 write-ahead marker: transmission is about
                              to start (durable BEFORE ``submit``; opaque refs only)
* ``abort#<g>``            -- ADR-035 s3.6: transmission was never started
* ``result#<g>|<id>``      -- an ``ExecutionResult`` observed in generation ``g``
* ``release#<g>``          -- generation ``g`` ended in a clean zero-fill
                              REJECTED and was released for retry

The generation is the count of release events. Only a clean zero-fill REJECTED
(``is_safe_to_retry_without_reconciliation``) may be released; UNKNOWN,
ACCEPTED, PARTIALLY_FILLED and FILLED never are. A crash between claim and
result leaves the key claimed. Any unreadable or inconsistent stored state
raises -- it is never treated as "unseen".

Two failure classes are kept apart (ADR-035 W3 rows 19/20):

* integrity violation (malformed / contradictory / tampered stored events) ->
  ``DedupStoreCorruptError``; ``inspect`` reports state ``QUARANTINED``;
* storage I/O failure -> ``DedupStoreIOError``. It subclasses
  ``DedupStoreCorruptError`` ONLY so that pre-existing callers still fail
  closed; always test ``DedupStoreIOError`` FIRST. It is a plain deny, never a
  state, and is never recorded as quarantine.

UNSAFE EVIDENCE IS MONOTONIC within a generation: once any result of a generation
was UNKNOWN, ACCEPTED, PARTIALLY_FILLED or FILLED, no later result (e.g. a clean
REJECTED) makes it clean, releasable or re-claimable; only a generation whose
results are ALL clean zero-fill REJECTED is releasable. This does not resolve
OPEN-2 and adds no clearing mechanism.

RESULT-WITHOUT-ATTEMPT: for legacy compatibility the store still accepts
``record_result`` in CLAIMED_NOT_ATTEMPTED. PR-4 is the ENFORCEMENT OWNER: a
result may only be recorded when the state is ATTEMPTED_NO_RESULT (acceptance
gate of PR-4). Caller-supplied evidence refs are not validated here for
secrets/PII (PR-4 contract-test gate).

GLOBAL ENUMERATION (DEDUP-ENUM-1) is a storage-layer capability and is NOT implemented
here: the Journal cannot list streams, so `unresolved_among` is caller-scoped only.

EXCEPTION HIERARCHY follow-up gate (before PR-4 integration): sibling IO / integrity
errors under DedupStoreError, with all catches and tests migrated and independently
reviewed. Until then consumers test DedupStoreIOError FIRST or use
`inspect()` / `violation_code`. `DedupRecord.latest_result` is display-only; PR-4 must
consume `inspect()` / `state()`.

Only the ``attempt`` and ``abort`` event names are added to the recognised set;
every other name stays an integrity violation. Release after ``abort``
(OPEN-14), quarantine clearing (OPEN-3) and reconciliation-based release
(OPEN-2) are NOT decided and NOT implemented: an aborted key stays claimed and a
quarantined key stays quarantined.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
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

_EVENT_NAMES = ("claim", "result", "release", "attempt", "abort")
_ATTEMPT_FIELDS = frozenset(
    {
        "event",
        "generation",
        "idempotency_key",
        "request_digest",
        "resolved_quantity",
        "reconciliation_evidence_ref",
        "preflight_decision_ref",
        "written_at",
    }
)
_ABORT_FIELDS = frozenset({"event", "generation", "idempotency_key"})


class DedupStoreError(ValueError):
    """Invalid use of the dedup store (blank key, result without claim, ...)."""


class DedupStoreCorruptError(DedupStoreError):
    """Integrity violation in stored state. Callers must deny, not retry."""


class DedupStoreIOError(DedupStoreCorruptError):
    """Storage I/O failure (``dedup_store_unreadable`` / ``dedup_store_write_failed``).

    A plain deny for this attempt: nothing is recorded as quarantined and the
    read may be retried later. Never read as "unseen". Subclasses
    ``DedupStoreCorruptError`` solely for backward-compatible fail-closed
    handling; it is NOT an integrity violation.
    """


class DedupMarkerRefusedError(DedupStoreError):
    """``attempt``/``abort`` was not durably recorded by this call.

    Raised for a wrong state, a lost race (W2) or an unconfirmed append (W1). The
    caller must treat it as "do not transmit".
    """


class _ConcurrentWrite(Exception):
    """Internal: optimistic append lost a race; the caller treats it as 'not done'."""


class ClaimOutcome(StrEnum):
    FIRST_CLAIM = "first_claim"
    DUPLICATE = "duplicate"


class DedupKeyState(StrEnum):
    """ADR-035 W3 derived state of a key (a pure function of its stored events)."""

    UNCLAIMED = "UNCLAIMED"
    CLAIMED_NOT_ATTEMPTED = "CLAIMED_NOT_ATTEMPTED"
    ATTEMPTED_NO_RESULT = "ATTEMPTED_NO_RESULT"
    RESULT_UNSAFE = "RESULT_UNSAFE"
    RESULT_CLEAN_REJECTED = "RESULT_CLEAN_REJECTED"
    ABORTED_NEVER_ATTEMPTED = "ABORTED_NEVER_ATTEMPTED"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True, slots=True, kw_only=True)
class DedupRecord:
    """Display record. `latest_result` is a DISPLAY value (latest-wins) and MUST NOT be
    used for any safety decision; PR-4 must consume `inspect()` / `state()` only."""

    idempotency_key: str
    generation: int
    latest_result: ExecutionResult | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DedupKeyStatus:
    """W3 status. `latest_result` is display-only (latest-wins); decide on `state`,
    `unknown_observed` and `violation_code`, never on the latest result."""

    idempotency_key: str
    state: DedupKeyState
    generation: int | None  # None only when QUARANTINED
    latest_result: ExecutionResult | None
    violation_code: str | None  # set only when QUARANTINED
    # True if the current generation EVER observed an UNKNOWN result (monotonic; a later
    # REJECTED never clears it).
    unknown_observed: bool = False


class ExecutionDedupStore(Protocol):
    def claim(self, idempotency_key: str) -> ClaimOutcome:
        """Atomically claim the key. FIRST_CLAIM for exactly one caller."""
        ...

    def record_result(self, idempotency_key: str, result: ExecutionResult) -> None:
        """Append the outcome against a currently claimed key."""
        ...

    def lookup(self, idempotency_key: str) -> DedupRecord | None:
        """Display lookup; None if never claimed. `latest_result` is latest-wins DISPLAY data
        and MUST NOT drive any safety decision; use `inspect()` / `state()` (PR-4)."""
        ...

    def release_for_retry(self, idempotency_key: str) -> bool:
        """Release only if the generation has >=1 result, ALL clean zero-fill REJECTED, and
        no unsafe evidence (UNKNOWN/ACCEPTED/PARTIALLY_FILLED/FILLED) was ever observed
        (monotonic; a later REJECTED never clears it). Never releases ABORTED/ATTEMPTED."""
        ...

    def record_attempt(
        self,
        idempotency_key: str,
        *,
        request_digest: str,
        resolved_quantity: Decimal | None,
        reconciliation_evidence_ref: str,
        preflight_decision_ref: str,
        written_at: datetime,
    ) -> None:
        """Durably write ``attempt#g``; raises (caller must not submit) unless this call won."""
        ...

    def record_abort(self, idempotency_key: str) -> None:
        """Durably write ``abort#g``; raises unless this call won."""
        ...

    def record_late_result(
        self, idempotency_key: str, generation: int, result: ExecutionResult
    ) -> bool:
        """Explicit-generation append of a result for an already released generation."""
        ...

    def inspect(self, idempotency_key: str) -> DedupKeyStatus:
        """W3 state. Integrity violation -> QUARANTINED; I/O failure raises."""
        ...

    def state(self, idempotency_key: str) -> DedupKeyState:
        """Convenience wrapper over ``inspect``."""
        ...

    def unresolved_among(self, keys: Iterable[str]) -> tuple[str, ...]:
        """TEMPORARY CALLER-SCOPED accessor, NOT global safety proof (DEDUP-ENUM-1 is not
        implemented; unnamed keys are invisible).

        Returns caller-supplied keys that are ATTEMPTED_NO_RESULT or ever observed UNKNOWN
        in the current generation. RESULT_UNSAFE keys (incl. ever ACCEPTED/PARTIALLY_FILLED/
        FILLED) are NOT reported here per ADR s3.8 and QUARANTINED keys are not either: the
        OPEN-16 gate MUST consume `inspect()`/`state()` (RESULT_UNSAFE, QUARANTINED), not
        only this method."""
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


def attempt_payload(
    idempotency_key: str,
    generation: int,
    *,
    request_digest: str,
    resolved_quantity: Decimal | None,
    reconciliation_evidence_ref: str,
    preflight_decision_ref: str,
    written_at: datetime,
) -> dict[str, Any]:
    """Deterministic ``attempt#g`` payload (ADR-035 s3.6): opaque refs only, no broker payload."""

    for name, value in (
        ("request_digest", request_digest),
        ("reconciliation_evidence_ref", reconciliation_evidence_ref),
        ("preflight_decision_ref", preflight_decision_ref),
    ):
        if not isinstance(value, str) or not value.strip():
            raise DedupStoreError(f"attempt_{name}_blank")
    if resolved_quantity is not None and (
        not isinstance(resolved_quantity, Decimal) or not resolved_quantity.is_finite()
    ):
        raise DedupStoreError("attempt_resolved_quantity_invalid")
    if (
        not isinstance(written_at, datetime)
        or written_at.tzinfo is None
        or written_at.utcoffset() is None
    ):
        raise DedupStoreError("attempt_written_at_naive")
    return {
        "event": "attempt",
        "generation": generation,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "resolved_quantity": (
            None if resolved_quantity is None else canonical_serialize(resolved_quantity)
        ),
        "reconciliation_evidence_ref": reconciliation_evidence_ref,
        "preflight_decision_ref": preflight_decision_ref,
        "written_at": canonical_serialize(written_at),
    }


def _nonblank_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_generation(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_attempt(event: dict[str, Any]) -> bool:
    if set(event) != _ATTEMPT_FIELDS:
        return False
    if not all(
        _nonblank_str(event[f])
        for f in ("request_digest", "reconciliation_evidence_ref", "preflight_decision_ref")
    ):
        return False
    quantity = event["resolved_quantity"]
    try:
        if quantity is not None:
            if not isinstance(quantity, str) or not Decimal(quantity).is_finite():
                return False
        written = event["written_at"]
        if not isinstance(written, str):
            return False
        parsed = datetime.fromisoformat(written)
    except (ValueError, InvalidOperation):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _is_row_integrity_failure(exc: Exception) -> bool:
    """A journal row that fails hash/JSON verification is tampering, not an I/O fault."""

    return isinstance(exc, json.JSONDecodeError) or (
        type(exc) is ValueError and str(exc) == "journal_corrupt"
    )


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
            events = self._events(idempotency_key)
            generation, claimed = self._state(idempotency_key, events)
            if not claimed:
                raise DedupStoreError("result_without_claim")
            if self._has(events, "abort", generation):
                raise DedupStoreError("result_after_abort")
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

    def record_late_result(
        self, idempotency_key: str, generation: int, result: ExecutionResult
    ) -> bool:
        """Durably append a result for an ALREADY RELEASED generation (ADR-035 s3.9/W5).

        The evidence must not be dropped, so this is the one mutator that is still
        accepted on a quarantined key; it only needs a readable stream in which
        ``generation`` has both a ``claim`` and a ``release``. A late non-clean
        result makes the key ``QUARANTINED`` on the next read. True only if THIS
        call wrote the row.
        """

        if not _valid_generation(generation):
            raise DedupStoreError("late_result_generation_invalid")
        with self._lock:
            events = self._events(idempotency_key)
            if not self._has(events, "claim", generation) or not self._has(
                events, "release", generation
            ):
                raise DedupStoreError("late_result_generation_not_released")
            return self._append(
                _stream(idempotency_key),
                f"result#{generation}|{result.result_id}",
                {
                    "event": "result",
                    "generation": generation,
                    "idempotency_key": idempotency_key,
                    "result": serialize_result(result),
                },
            )

    def record_attempt(
        self,
        idempotency_key: str,
        *,
        request_digest: str,
        resolved_quantity: Decimal | None,
        reconciliation_evidence_ref: str,
        preflight_decision_ref: str,
        written_at: datetime,
    ) -> None:
        """Write ``attempt#g`` (W1/W2). Returning normally means this call won durably.

        Anything else raises, so a caller that cannot prove the marker is durable
        cannot reach ``submit``.
        """

        _stream(idempotency_key)
        with self._lock:
            events = self._events(idempotency_key)
            generation, claimed = self._state(idempotency_key, events)
            self._require_not_attempted(events, generation, claimed)
            payload = attempt_payload(
                idempotency_key,
                generation,
                request_digest=request_digest,
                resolved_quantity=resolved_quantity,
                reconciliation_evidence_ref=reconciliation_evidence_ref,
                preflight_decision_ref=preflight_decision_ref,
                written_at=written_at,
            )
            self._append_marker(idempotency_key, f"attempt#{generation}", payload, len(events))

    def record_abort(self, idempotency_key: str) -> None:
        """Write ``abort#g`` (W2): 'transmission was never started'. Raises unless this call won."""

        _stream(idempotency_key)
        with self._lock:
            events = self._events(idempotency_key)
            generation, claimed = self._state(idempotency_key, events)
            self._require_not_attempted(events, generation, claimed)
            self._append_marker(
                idempotency_key,
                f"abort#{generation}",
                {"event": "abort", "generation": generation, "idempotency_key": idempotency_key},
                len(events),
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

    def inspect(self, idempotency_key: str) -> DedupKeyStatus:
        """W3 state of the key.

        An integrity violation yields ``QUARANTINED`` (deny everything). A storage
        I/O failure is NOT a state: ``DedupStoreIOError`` propagates.
        """

        with self._lock:
            try:
                events = self._events(idempotency_key)
                generation, claimed = self._state(idempotency_key, events)
                state, latest = self._w3(events, generation, claimed)
            except DedupStoreIOError:
                raise
            except DedupStoreCorruptError as exc:
                return DedupKeyStatus(
                    idempotency_key=idempotency_key,
                    state=DedupKeyState.QUARANTINED,
                    generation=None,
                    latest_result=None,
                    violation_code=str(exc),
                    unknown_observed=False,
                )
        return DedupKeyStatus(
            idempotency_key=idempotency_key,
            state=state,
            generation=generation,
            latest_result=latest,
            violation_code=None,
            unknown_observed=any(
                r.status is ExecutionStatus.UNKNOWN for r in self._results(events, generation)
            ),
        )

    def state(self, idempotency_key: str) -> DedupKeyState:
        return self.inspect(idempotency_key).state

    def unresolved_among(self, keys: Iterable[str]) -> tuple[str, ...]:
        """TEMPORARY CALLER-SCOPED accessor (not global safety proof).

        Keys (from the CALLER-SUPPLIED set) that are ATTEMPTED_NO_RESULT or whose
        current generation EVER observed an UNKNOWN result (ADR-035 s3.8; unsafe
        history is monotonic, a later REJECTED does not clear it), sorted and
        de-duplicated.

        This is NOT the global enumeration: the journal cannot list streams, so keys
        the caller does not name are invisible here (global enumeration is
        DEDUP-ENUM-1, a storage-layer capability owned elsewhere). QUARANTINED keys are not part
        of the ADR definition and are NOT returned; callers needing them must read
        ``inspect`` per key. I/O failure propagates (never silently dropped).
        """

        unresolved: list[str] = []
        for key in sorted(set(keys)):
            status = self.inspect(key)
            if status.state is DedupKeyState.ATTEMPTED_NO_RESULT or status.unknown_observed:
                unresolved.append(key)
        return tuple(unresolved)

    def release_for_retry(self, idempotency_key: str) -> bool:
        """True only if THIS call durably wrote the release.

        The release append is optimistic: it carries the event count that was
        read, so any concurrent write to the key's stream (another instance or
        process recording a result, releasing, ...) makes it fail and the key
        stays claimed. Independently, ``_state`` re-checks every released
        generation, so a stale writer cannot make an unsafe outcome re-claimable.
        An aborted generation is never released (OPEN-14 is undecided).
        """

        with self._lock:
            events = self._events(idempotency_key)
            generation, claimed = self._state(idempotency_key, events)
            if not claimed or self._has(events, "abort", generation):
                return False
            results = self._results(events, generation)
            if not results or not all(is_safe_to_retry_without_reconciliation(r) for r in results):
                return False  # no results, or unsafe evidence EVER seen (monotonic)
            latest = results[-1]
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

    def _require_not_attempted(
        self, events: tuple[dict[str, Any], ...], generation: int, claimed: bool
    ) -> None:
        state, _ = self._w3(events, generation, claimed)
        if state is not DedupKeyState.CLAIMED_NOT_ATTEMPTED:
            raise DedupMarkerRefusedError(f"dedup_marker_state_invalid:{state.value}")

    def _append_marker(
        self, idempotency_key: str, key: str, payload: dict[str, Any], observed: int
    ) -> None:
        try:
            won = self._append(_stream(idempotency_key), key, payload, expected_count=observed)
        except _ConcurrentWrite as exc:
            raise DedupMarkerRefusedError("dedup_marker_race_lost") from exc
        if not won:
            raise DedupMarkerRefusedError("dedup_marker_not_confirmed")

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
            if str(exc) == "journal_identity_conflict":
                raise DedupStoreCorruptError("dedup_event_identity_conflict") from exc
            raise DedupStoreIOError("dedup_store_write_failed") from exc
        except Exception as exc:  # fail closed on any storage failure
            raise DedupStoreIOError("dedup_store_write_failed") from exc

    def _events(self, idempotency_key: str) -> tuple[dict[str, Any], ...]:
        try:
            events = self._journal.read(_stream(idempotency_key))
        except DedupStoreError:
            raise
        except Exception as exc:  # fail closed on any storage failure
            if _is_row_integrity_failure(exc):
                raise DedupStoreCorruptError("dedup_journal_row_corrupt") from exc
            raise DedupStoreIOError("dedup_store_unreadable") from exc
        for event in events:
            if (
                not isinstance(event, dict)
                or event.get("idempotency_key") != idempotency_key
                or event.get("event") not in _EVENT_NAMES
                or not _valid_generation(event.get("generation"))
                or (event["event"] == "attempt" and not _valid_attempt(event))
                or (event["event"] == "abort" and set(event) != _ABORT_FIELDS)
            ):
                raise DedupStoreCorruptError("dedup_event_malformed")
        return events

    @staticmethod
    def _has(events: tuple[dict[str, Any], ...], name: str, generation: int) -> bool:
        return any(e["event"] == name and e["generation"] == generation for e in events)

    def _state(
        self, idempotency_key: str, events: tuple[dict[str, Any], ...] | None = None
    ) -> tuple[int, bool]:
        """(current generation, whether that generation is claimed).

        Fails closed (raises ``DedupStoreCorruptError``) on any integrity violation,
        e.g. a released generation whose result is not a clean zero-fill REJECTED
        (including one that landed after the release) -- such a key must never
        become re-claimable.
        """

        if events is None:
            events = self._events(idempotency_key)
        releases = sorted(e["generation"] for e in events if e["event"] == "release")
        claims = {e["generation"] for e in events if e["event"] == "claim"}
        attempts = {e["generation"] for e in events if e["event"] == "attempt"}
        aborts = {e["generation"] for e in events if e["event"] == "abort"}
        generation = len(releases)
        if releases != list(range(generation)):
            raise DedupStoreCorruptError("dedup_release_sequence_broken")
        if any(g not in claims for g in releases):
            raise DedupStoreCorruptError("dedup_release_without_claim")
        if any(g > generation for g in claims):
            raise DedupStoreCorruptError("dedup_claim_beyond_generation")
        if any(g not in claims for g in attempts | aborts):
            raise DedupStoreCorruptError("dedup_marker_without_claim")
        if attempts & aborts:
            raise DedupStoreCorruptError("dedup_attempt_and_abort")
        if any(
            e["generation"] > generation or e["generation"] not in claims
            for e in events
            if e["event"] == "result"
        ):
            raise DedupStoreCorruptError("dedup_result_without_claim")
        if any(e["event"] == "result" and e["generation"] in aborts for e in events):
            raise DedupStoreCorruptError("dedup_result_after_abort")
        if any(g in aborts for g in releases):
            raise DedupStoreCorruptError("dedup_release_after_abort")
        seen_result: set[int] = set()
        for e in events:  # journal order
            if e["event"] == "result":
                seen_result.add(e["generation"])
            elif e["event"] == "attempt" and e["generation"] in seen_result:
                raise DedupStoreCorruptError("dedup_attempt_after_result")
        # Monotonic unsafe evidence (Rin): a released generation must have at least one
        # result and EVERY result (earlier or later than a clean one, before or after the
        # release) must be a clean zero-fill REJECTED.
        for released in range(generation):
            results = self._results(events, released)
            if not results or not all(is_safe_to_retry_without_reconciliation(r) for r in results):
                raise DedupStoreCorruptError("dedup_released_generation_not_safe")
        return generation, generation in claims

    def _w3(
        self, events: tuple[dict[str, Any], ...], generation: int, claimed: bool
    ) -> tuple[DedupKeyState, ExecutionResult | None]:
        """ADR-035 W3 table for the current generation (call only after ``_state``)."""

        if not claimed:
            return DedupKeyState.UNCLAIMED, None
        if self._has(events, "abort", generation):
            return DedupKeyState.ABORTED_NEVER_ATTEMPTED, None
        results = self._results(events, generation)
        if results:
            if all(is_safe_to_retry_without_reconciliation(r) for r in results):
                return DedupKeyState.RESULT_CLEAN_REJECTED, results[-1]
            return DedupKeyState.RESULT_UNSAFE, results[-1]
        if self._has(events, "attempt", generation):
            return DedupKeyState.ATTEMPTED_NO_RESULT, None
        return DedupKeyState.CLAIMED_NOT_ATTEMPTED, None

    @staticmethod
    def _results(events: tuple[dict[str, Any], ...], generation: int) -> list[ExecutionResult]:
        return [
            deserialize_result(e.get("result"))
            for e in events
            if e["event"] == "result" and e["generation"] == generation
        ]

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
