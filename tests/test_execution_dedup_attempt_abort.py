"""ADR-035 PR-5: write-ahead attempt/abort markers, W3 states, quarantine, failure classes.

All journals live under pytest ``tmp_path`` (or in memory). PostgreSQL is not exercised.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.execution.dedup_store import (
    ClaimOutcome,
    DedupKeyState,
    DedupMarkerRefusedError,
    DedupStoreCorruptError,
    DedupStoreError,
    DedupStoreIOError,
    InMemoryExecutionDedupStore,
    JournalExecutionDedupStore,
    attempt_payload,
    serialize_result,
)
from nexora.execution.models import ExecutionResult, ExecutionStatus
from nexora.storage import SQLiteJournal

KEY = "exec:CLOSE:prop-7"
STREAM = f"execution-dedup:{KEY}"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
ATTEMPT: dict[str, Any] = {
    "request_digest": "sha256:abc",
    "resolved_quantity": Decimal("1.50"),
    "reconciliation_evidence_ref": "evidence-1",
    "preflight_decision_ref": "preflight-1",
    "written_at": NOW,
}


def result(status: ExecutionStatus, rid: str = "r1", fill: str = "0") -> ExecutionResult:
    qty, filled = Decimal("1.0"), Decimal(fill)
    unknown = status is ExecutionStatus.UNKNOWN
    return ExecutionResult(
        result_id=rid,
        request_ref="req-1",
        status=status,
        requested_quantity=qty,
        filled_quantity=filled,
        remaining_quantity=None if unknown else qty - filled,
        reason_code="x" if status is ExecutionStatus.REJECTED else None,
        observed_at=NOW,
    )


def durable(path: Path) -> tuple[JournalExecutionDedupStore, SQLiteJournal]:
    journal = SQLiteJournal(path / "dedup.sqlite")
    return JournalExecutionDedupStore(journal), journal


def raw_append(journal: SQLiteJournal, key: str, payload: dict[str, Any]) -> None:
    journal.append(STREAM, key, payload)


class FlakyJournal:
    """Delegating journal with injectable I/O failures."""

    backend = "flaky"

    def __init__(self, inner: SQLiteJournal) -> None:
        self.inner = inner
        self.fail_read = False
        self.fail_append = False
        self.unconfirmed_append = False

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        if self.fail_append:
            raise OSError("disk_full")
        if self.unconfirmed_append:
            return False
        return self.inner.append(stream, key, payload, expected_count=expected_count)

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        if self.fail_read:
            raise OSError("disk_gone")
        return self.inner.read(stream)

    def iter_read(self, stream: str) -> Iterator[dict[str, Any]]:
        yield from self.read(stream)

    def close(self) -> None:
        self.inner.close()


def flaky(path: Path) -> tuple[JournalExecutionDedupStore, FlakyJournal]:
    journal = FlakyJournal(SQLiteJournal(path / "dedup.sqlite"))
    return JournalExecutionDedupStore(journal), journal


def state(store: JournalExecutionDedupStore) -> DedupKeyState:
    return store.state(KEY)


# -- payload ---------------------------------------------------------------


def test_attempt_payload_is_deterministic_and_opaque() -> None:
    a = attempt_payload(KEY, 0, **ATTEMPT)
    b = attempt_payload(KEY, 0, **ATTEMPT)
    assert a == b
    assert a["resolved_quantity"] == "1.5"
    assert a["written_at"] == "2026-01-01T00:00:00+00:00"
    assert set(a) == {
        "event",
        "generation",
        "idempotency_key",
        "request_digest",
        "resolved_quantity",
        "reconciliation_evidence_ref",
        "preflight_decision_ref",
        "written_at",
    }
    assert (
        attempt_payload(KEY, 0, **{**ATTEMPT, "resolved_quantity": None})["resolved_quantity"]
        is None
    )


@pytest.mark.parametrize(
    "override",
    [
        {"request_digest": " "},
        {"reconciliation_evidence_ref": ""},
        {"preflight_decision_ref": " "},
        {"resolved_quantity": Decimal("NaN")},
        {"written_at": datetime(2026, 1, 1)},
    ],
)
def test_attempt_payload_rejects_invalid_input(override: dict[str, Any]) -> None:
    store = InMemoryExecutionDedupStore()
    store.claim(KEY)
    with pytest.raises(DedupStoreError):
        store.record_attempt(KEY, **{**ATTEMPT, **override})
    assert store.state(KEY) is DedupKeyState.CLAIMED_NOT_ATTEMPTED


# -- W3 state table --------------------------------------------------------


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_dedup_states_follow_w3_table(kind: str, tmp_path: Path) -> None:
    def fresh(name: str) -> JournalExecutionDedupStore:
        if kind == "memory":
            return InMemoryExecutionDedupStore()
        (tmp_path / name).mkdir()
        return durable(tmp_path / name)[0]

    s = fresh("a")
    assert s.state(KEY) is DedupKeyState.UNCLAIMED
    s.claim(KEY)
    assert s.state(KEY) is DedupKeyState.CLAIMED_NOT_ATTEMPTED
    s.record_attempt(KEY, **ATTEMPT)
    assert s.state(KEY) is DedupKeyState.ATTEMPTED_NO_RESULT
    s.record_result(KEY, result(ExecutionStatus.REJECTED))
    assert s.state(KEY) is DedupKeyState.RESULT_CLEAN_REJECTED
    assert s.release_for_retry(KEY) is True
    assert s.state(KEY) is DedupKeyState.UNCLAIMED  # new generation, unclaimed

    for status, fill in [
        (ExecutionStatus.UNKNOWN, "0"),
        (ExecutionStatus.ACCEPTED, "0"),
        (ExecutionStatus.PARTIALLY_FILLED, "0.5"),
        (ExecutionStatus.FILLED, "1.0"),
    ]:
        u = fresh(f"u-{status.value}")
        u.claim(KEY)
        u.record_attempt(KEY, **ATTEMPT)
        u.record_result(KEY, result(status, fill=fill))
        assert u.state(KEY) is DedupKeyState.RESULT_UNSAFE

    ab = fresh("abort")
    ab.claim(KEY)
    ab.record_abort(KEY)
    assert ab.state(KEY) is DedupKeyState.ABORTED_NEVER_ATTEMPTED


def test_legacy_result_without_attempt_still_classified(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_result(KEY, result(ExecutionStatus.REJECTED))
    assert s.state(KEY) is DedupKeyState.RESULT_CLEAN_REJECTED
    j.close()


# -- mutual exclusion ------------------------------------------------------


def test_attempt_requires_claim_and_is_single_shot(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    with pytest.raises(DedupMarkerRefusedError, match="UNCLAIMED"):
        s.record_attempt(KEY, **ATTEMPT)
    with pytest.raises(DedupMarkerRefusedError, match="UNCLAIMED"):
        s.record_abort(KEY)
    s.claim(KEY)
    s.record_attempt(KEY, **ATTEMPT)
    with pytest.raises(DedupMarkerRefusedError, match="ATTEMPTED_NO_RESULT"):
        s.record_attempt(KEY, **ATTEMPT)
    with pytest.raises(DedupMarkerRefusedError, match="ATTEMPTED_NO_RESULT"):
        s.record_abort(KEY)
    j.close()


def test_attempt_and_abort_mutually_exclusive(tmp_path: Path) -> None:
    # Order 1: abort lands between attempt's read and attempt's append.
    d1 = tmp_path / "o1"
    d1.mkdir()
    a, ja = durable(d1)
    b, jb = durable(d1)
    a.claim(KEY)
    original = a._append

    def abort_first(*args: Any, **kwargs: Any) -> bool:
        if args[1].startswith("attempt#"):
            b.record_abort(KEY)
        return original(*args, **kwargs)

    a._append = abort_first  # type: ignore[method-assign]
    with pytest.raises(DedupMarkerRefusedError, match="race_lost"):
        a.record_attempt(KEY, **ATTEMPT)
    a._append = original  # type: ignore[method-assign]
    assert a.state(KEY) is DedupKeyState.ABORTED_NEVER_ATTEMPTED
    assert not any(e["event"] == "attempt" for e in ja.read(STREAM))
    ja.close()
    jb.close()

    # Order 2: attempt lands between abort's read and abort's append.
    d2 = tmp_path / "o2"
    d2.mkdir()
    a, ja = durable(d2)
    b, jb = durable(d2)
    a.claim(KEY)
    original = a._append

    def attempt_first(*args: Any, **kwargs: Any) -> bool:
        if args[1].startswith("abort#"):
            b.record_attempt(KEY, **ATTEMPT)
        return original(*args, **kwargs)

    a._append = attempt_first  # type: ignore[method-assign]
    with pytest.raises(DedupMarkerRefusedError, match="race_lost"):
        a.record_abort(KEY)
    a._append = original  # type: ignore[method-assign]
    assert a.state(KEY) is DedupKeyState.ATTEMPTED_NO_RESULT
    assert not any(e["event"] == "abort" for e in ja.read(STREAM))
    ja.close()
    jb.close()


def test_attempt_abort_threads_two_instances_exactly_one_wins(tmp_path: Path) -> None:
    a, ja = durable(tmp_path)
    b, jb = durable(tmp_path)
    a.claim(KEY)
    barrier = threading.Barrier(8)
    wins: list[str] = []
    refused: list[str] = []
    lock = threading.Lock()

    def worker(store: JournalExecutionDedupStore, do_attempt: bool) -> None:
        barrier.wait()
        try:
            if do_attempt:
                store.record_attempt(KEY, **ATTEMPT)
            else:
                store.record_abort(KEY)
            with lock:
                wins.append("attempt" if do_attempt else "abort")
        except DedupMarkerRefusedError:
            with lock:
                refused.append("x")

    threads = [
        threading.Thread(target=worker, args=(a if i % 2 else b, bool(i % 3 == 0)))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(refused) == 7
    kinds = {e["event"] for e in ja.read(STREAM)}
    assert (kinds & {"attempt", "abort"}) == {wins[0]}
    ja.close()
    jb.close()


# -- attempted no result / abort never released ----------------------------


def test_attempted_no_result_never_released(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_attempt(KEY, **ATTEMPT)
    assert s.release_for_retry(KEY) is False
    assert s.claim(KEY) is ClaimOutcome.DUPLICATE
    assert s.state(KEY) is DedupKeyState.ATTEMPTED_NO_RESULT
    j.close()
    s2, j2 = durable(tmp_path)  # restart
    assert s2.state(KEY) is DedupKeyState.ATTEMPTED_NO_RESULT
    assert s2.release_for_retry(KEY) is False
    assert s2.claim(KEY) is ClaimOutcome.DUPLICATE
    j2.close()


def test_abort_stays_claimed_and_cannot_be_released_or_reclaimed(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_abort(KEY)
    assert s.release_for_retry(KEY) is False
    assert s.claim(KEY) is ClaimOutcome.DUPLICATE
    with pytest.raises(DedupStoreError, match="result_after_abort"):
        s.record_result(KEY, result(ExecutionStatus.REJECTED))
    j.close()
    s2, j2 = durable(tmp_path)
    assert s2.state(KEY) is DedupKeyState.ABORTED_NEVER_ATTEMPTED
    assert s2.claim(KEY) is ClaimOutcome.DUPLICATE
    assert s2.release_for_retry(KEY) is False
    j2.close()


# -- W1: marker failure means the caller cannot submit ---------------------


def test_attempt_append_failure_signals_failure_and_writes_nothing(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    j.fail_append = True
    with pytest.raises(DedupStoreIOError):
        s.record_attempt(KEY, **ATTEMPT)
    j.fail_append = False
    assert s.state(KEY) is DedupKeyState.CLAIMED_NOT_ATTEMPTED


def test_unconfirmed_attempt_append_signals_failure(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    j.unconfirmed_append = True
    with pytest.raises(DedupMarkerRefusedError, match="not_confirmed"):
        s.record_attempt(KEY, **ATTEMPT)
    with pytest.raises(DedupMarkerRefusedError, match="not_confirmed"):
        s.record_abort(KEY)


def test_attempt_read_failure_signals_failure(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    j.fail_read = True
    with pytest.raises(DedupStoreIOError):
        s.record_attempt(KEY, **ATTEMPT)


# -- failure classes -------------------------------------------------------


def test_io_failure_is_plain_deny_not_quarantine(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    j.fail_read = True
    with pytest.raises(DedupStoreIOError) as read_err:
        s.inspect(KEY)
    assert str(read_err.value) == "dedup_store_unreadable"
    with pytest.raises(DedupStoreIOError):
        s.claim(KEY)
    j.fail_read = False
    j.fail_append = True
    with pytest.raises(DedupStoreIOError) as write_err:
        s.record_result(KEY, result(ExecutionStatus.REJECTED))
    assert str(write_err.value) == "dedup_store_write_failed"
    j.fail_append = False
    # Nothing was recorded as quarantine; the key reads normally once I/O is back.
    status = s.inspect(KEY)
    assert status.state is DedupKeyState.CLAIMED_NOT_ATTEMPTED
    assert status.violation_code is None
    assert j.inner.read(STREAM) == ({"event": "claim", "generation": 0, "idempotency_key": KEY},)


def test_io_failure_on_first_claim_is_not_unseen(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    j.fail_append = True
    with pytest.raises(DedupStoreIOError):
        s.claim(KEY)
    j.fail_append = False
    assert s.state(KEY) is DedupKeyState.UNCLAIMED
    assert j.inner.read(STREAM) == ()


def test_integrity_violation_is_not_io_class(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    raw_append(j, "bogus#0", {"event": "bogus", "generation": 0, "idempotency_key": KEY})
    status = s.inspect(KEY)
    assert status.state is DedupKeyState.QUARANTINED
    assert status.violation_code == "dedup_event_malformed"
    with pytest.raises(DedupStoreCorruptError) as err:
        s.claim(KEY)
    assert not isinstance(err.value, DedupStoreIOError)
    j.close()


def test_tampered_row_quarantines(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    j.connection.execute(
        "UPDATE research_journal SET payload=? WHERE event_key='claim#0'", ('{"tampered":1}',)
    )
    j.connection.commit()
    status = s.inspect(KEY)
    assert status.state is DedupKeyState.QUARANTINED
    assert status.violation_code == "dedup_journal_row_corrupt"
    for call in (
        lambda: s.claim(KEY),
        lambda: s.record_attempt(KEY, **ATTEMPT),
        lambda: s.record_abort(KEY),
        lambda: s.release_for_retry(KEY),
        lambda: s.lookup(KEY),
    ):
        with pytest.raises(DedupStoreCorruptError):
            call()
    j.close()


def test_closed_journal_is_io_class(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    j.close()
    with pytest.raises(DedupStoreIOError):
        s.inspect(KEY)


# -- event whitelist -------------------------------------------------------


@pytest.mark.parametrize("name", ["reconciled", "quarantine_resolved", "unknown_event", "Attempt"])
def test_unrecognized_event_names_are_malformed(name: str, tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    raw_append(j, f"{name}#0", {"event": name, "generation": 0, "idempotency_key": KEY})
    assert s.state(KEY) is DedupKeyState.QUARANTINED
    assert s.inspect(KEY).violation_code == "dedup_event_malformed"
    with pytest.raises(DedupStoreCorruptError):
        s.claim(KEY)
    j.close()


def _raw_attempt(**over: Any) -> dict[str, Any]:
    payload = attempt_payload(KEY, 0, **ATTEMPT)
    payload.update(over)
    return payload


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra": "x"},
        {"request_digest": ""},
        {"resolved_quantity": "NaN"},
        {"written_at": "2026-01-01T00:00:00"},
        {"generation": True},
        {"generation": -1},
    ],
)
def test_malformed_attempt_payload_quarantines(mutation: dict[str, Any], tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    raw_append(j, "attempt#0", _raw_attempt(**mutation))
    assert s.state(KEY) is DedupKeyState.QUARANTINED
    j.close()


def _raw_abort() -> tuple[str, dict[str, Any]]:
    return "abort#0", {"event": "abort", "generation": 0, "idempotency_key": KEY}


def _raw_result() -> tuple[str, dict[str, Any]]:
    return "result#0|r1", {
        "event": "result",
        "generation": 0,
        "idempotency_key": KEY,
        "result": serialize_result(result(ExecutionStatus.REJECTED)),
    }


def test_contradictory_marker_histories_quarantine(tmp_path: Path) -> None:
    cases: dict[str, tuple[bool, list[tuple[str, dict[str, Any]]]]] = {
        "dedup_attempt_and_abort": (True, [("attempt#0", _raw_attempt()), _raw_abort()]),
        "dedup_marker_without_claim": (False, [("attempt#3", _raw_attempt(generation=3))]),
        "dedup_result_after_abort": (True, [_raw_abort(), _raw_result()]),
        "dedup_attempt_after_result": (True, [_raw_result(), ("attempt#0", _raw_attempt())]),
    }
    for index, (code, (claim_first, events)) in enumerate(cases.items()):
        d = tmp_path / f"c{index}"
        d.mkdir()
        s, j = durable(d)
        if claim_first:
            s.claim(KEY)
        for key, payload in events:
            raw_append(j, key, payload)
        status = s.inspect(KEY)
        assert status.state is DedupKeyState.QUARANTINED, code
        assert status.violation_code == code
        j.close()


def test_abort_then_release_event_is_integrity_violation(tmp_path: Path) -> None:
    # OPEN-14 undecided: a release recorded for an aborted generation is never legitimate.
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_abort(KEY)
    raw_append(
        j,
        "release#0",
        {"event": "release", "generation": 0, "idempotency_key": KEY, "released_result_id": "x"},
    )
    assert s.inspect(KEY).violation_code == "dedup_release_after_abort"
    j.close()


# -- quarantine / late result ----------------------------------------------


def _released(tmp_path: Path) -> tuple[JournalExecutionDedupStore, SQLiteJournal]:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_attempt(KEY, **ATTEMPT)
    s.record_result(KEY, result(ExecutionStatus.REJECTED, "r1"))
    assert s.release_for_retry(KEY) is True
    return s, j


def test_quarantined_key_denies(tmp_path: Path) -> None:
    s, j = _released(tmp_path)
    assert s.record_late_result(KEY, 0, result(ExecutionStatus.UNKNOWN, "late")) is True
    status = s.inspect(KEY)
    assert status.state is DedupKeyState.QUARANTINED
    assert status.violation_code == "dedup_released_generation_not_safe"
    calls: list[Callable[[], object]] = [
        lambda: s.claim(KEY),
        lambda: s.record_attempt(KEY, **ATTEMPT),
        lambda: s.record_abort(KEY),
        lambda: s.record_result(KEY, result(ExecutionStatus.REJECTED, "r2")),
        lambda: s.release_for_retry(KEY),
        lambda: s.lookup(KEY),
    ]
    for call in calls:
        with pytest.raises(DedupStoreCorruptError):
            call()
    # a second instance and a restarted instance read the same quarantine
    other = JournalExecutionDedupStore(j)
    with pytest.raises(DedupStoreCorruptError):
        other.claim(KEY)
    j.close()
    s2, j2 = durable(tmp_path)
    assert s2.state(KEY) is DedupKeyState.QUARANTINED
    j2.close()


def test_late_unsafe_result_is_durable_and_never_auto_cleared(tmp_path: Path) -> None:
    s, j = _released(tmp_path)
    s.record_late_result(KEY, 0, result(ExecutionStatus.FILLED, "late1", fill="1.0"))
    # a later clean REJECTED must NOT make the key claimable again
    s.record_late_result(KEY, 0, result(ExecutionStatus.REJECTED, "late2"))
    assert s.state(KEY) is DedupKeyState.QUARANTINED
    ids = [e["result"]["result_id"] for e in j.read(STREAM) if e["event"] == "result"]
    assert ids == ["r1", "late1", "late2"]
    with pytest.raises(DedupStoreCorruptError):
        s.claim(KEY)
    j.close()


def test_late_clean_rejected_result_is_harmless(tmp_path: Path) -> None:
    s, j = _released(tmp_path)
    assert s.record_late_result(KEY, 0, result(ExecutionStatus.REJECTED, "late")) is True
    assert s.record_late_result(KEY, 0, result(ExecutionStatus.REJECTED, "late")) is False
    assert s.state(KEY) is DedupKeyState.UNCLAIMED
    j.close()


def test_late_result_requires_released_claimed_generation(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    with pytest.raises(DedupStoreError, match="not_released"):
        s.record_late_result(KEY, 0, result(ExecutionStatus.UNKNOWN))
    s.claim(KEY)
    with pytest.raises(DedupStoreError, match="not_released"):
        s.record_late_result(KEY, 0, result(ExecutionStatus.UNKNOWN))
    with pytest.raises(DedupStoreError, match="generation_invalid"):
        s.record_late_result(KEY, -1, result(ExecutionStatus.UNKNOWN))
    j.close()


def test_stale_writer_late_result_from_other_instance_quarantines(tmp_path: Path) -> None:
    a, ja = _released(tmp_path)
    b = JournalExecutionDedupStore(SQLiteJournal(tmp_path / "dedup.sqlite"))
    b.record_late_result(KEY, 0, result(ExecutionStatus.ACCEPTED, "late"))
    assert a.state(KEY) is DedupKeyState.QUARANTINED
    ja.close()


# -- unresolved keys (caller-supplied set only) ------------------------------


def test_unresolved_among_classifies_caller_supplied_keys(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    keys = {
        "k-unclaimed": None,
        "k-claimed": "claim",
        "k-attempted": "attempt",
        "k-unknown": "unknown",
        "k-filled": "filled",
        "k-rejected": "rejected",
        "k-aborted": "abort",
    }
    for key, step in keys.items():
        if step is None:
            continue
        s.claim(key)
        if step == "abort":
            s.record_abort(key)
        elif step != "claim":
            s.record_attempt(key, **ATTEMPT)
            if step == "unknown":
                s.record_result(key, result(ExecutionStatus.UNKNOWN))
            elif step == "filled":
                s.record_result(key, result(ExecutionStatus.FILLED, fill="1.0"))
            elif step == "rejected":
                s.record_result(key, result(ExecutionStatus.REJECTED))
    assert s.unresolved_among([*keys, "k-attempted"]) == ("k-attempted", "k-unknown")
    assert s.unresolved_among([]) == ()
    j.close()


def test_unresolved_among_propagates_io_failure(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    j.fail_read = True
    with pytest.raises(DedupStoreIOError):
        s.unresolved_among([KEY])


# -- restart / regression ----------------------------------------------------


def test_marker_state_survives_restart(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_attempt(KEY, **ATTEMPT)
    s.claim("exec:CLOSE:other")
    s.record_abort("exec:CLOSE:other")
    j.close()
    s2, j2 = durable(tmp_path)
    assert s2.state(KEY) is DedupKeyState.ATTEMPTED_NO_RESULT
    assert s2.state("exec:CLOSE:other") is DedupKeyState.ABORTED_NEVER_ATTEMPTED
    stored = [e for e in j2.read(STREAM) if e["event"] == "attempt"]
    assert stored == [attempt_payload(KEY, 0, **ATTEMPT)]
    j2.close()


def test_new_generation_attempt_after_clean_release(tmp_path: Path) -> None:
    s, j = _released(tmp_path)
    assert s.claim(KEY) is ClaimOutcome.FIRST_CLAIM
    s.record_attempt(KEY, **{**ATTEMPT, "resolved_quantity": Decimal("2")})
    assert s.state(KEY) is DedupKeyState.ATTEMPTED_NO_RESULT
    attempts = [e for e in j.read(STREAM) if e["event"] == "attempt"]
    assert [a["generation"] for a in attempts] == [0, 1]
    assert attempts[1]["resolved_quantity"] == "2"
    j.close()
