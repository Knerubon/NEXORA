from __future__ import annotations

import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.execution.dedup_store import (
    ClaimOutcome,
    DedupStoreCorruptError,
    DedupStoreError,
    ExecutionDedupStore,
    InMemoryExecutionDedupStore,
    JournalExecutionDedupStore,
    serialize_result,
)
from nexora.execution.models import ExecutionResult, ExecutionStatus
from nexora.storage import SQLiteJournal

KEY = "exec:CLOSE:prop-1"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


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


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ExecutionDedupStore:
    if request.param == "memory":
        return InMemoryExecutionDedupStore()
    return durable(tmp_path)[0]


def test_second_claim_is_duplicate(store: ExecutionDedupStore) -> None:
    assert store.claim(KEY) is ClaimOutcome.FIRST_CLAIM
    assert store.claim(KEY) is ClaimOutcome.DUPLICATE
    assert store.lookup("exec:CLOSE:other") is None


def test_reduce_and_close_are_distinct(store: ExecutionDedupStore) -> None:
    assert store.claim("exec:REDUCE:p") is ClaimOutcome.FIRST_CLAIM
    assert store.claim("exec:CLOSE:p") is ClaimOutcome.FIRST_CLAIM


@pytest.mark.parametrize(
    "status,fill",
    [
        (ExecutionStatus.UNKNOWN, "0"),
        (ExecutionStatus.ACCEPTED, "0"),
        (ExecutionStatus.PARTIALLY_FILLED, "0.5"),
        (ExecutionStatus.FILLED, "1.0"),
    ],
)
def test_unsafe_results_never_reclaimable(
    store: ExecutionDedupStore, status: ExecutionStatus, fill: str
) -> None:
    store.claim(KEY)
    store.record_result(KEY, result(status, fill=fill))
    assert store.release_for_retry(KEY) is False
    assert store.claim(KEY) is ClaimOutcome.DUPLICATE


def test_later_unknown_result_blocks_release(store: ExecutionDedupStore) -> None:
    store.claim(KEY)
    store.record_result(KEY, result(ExecutionStatus.REJECTED, rid="r1"))
    store.record_result(KEY, result(ExecutionStatus.UNKNOWN, rid="r2"))
    assert store.release_for_retry(KEY) is False


def test_crash_between_claim_and_result_fails_closed(store: ExecutionDedupStore) -> None:
    store.claim(KEY)
    assert store.release_for_retry(KEY) is False
    assert store.claim(KEY) is ClaimOutcome.DUPLICATE
    record = store.lookup(KEY)
    assert record is not None and record.latest_result is None


def test_clean_rejected_released_once_then_reclaimed(store: ExecutionDedupStore) -> None:
    store.claim(KEY)
    store.record_result(KEY, result(ExecutionStatus.REJECTED))
    assert store.release_for_retry(KEY) is True
    assert store.claim(KEY) is ClaimOutcome.FIRST_CLAIM
    assert store.claim(KEY) is ClaimOutcome.DUPLICATE
    # the old REJECTED must not authorize releasing the new, unresolved generation
    assert store.release_for_retry(KEY) is False
    record = store.lookup(KEY)
    assert record is not None and record.generation == 1 and record.latest_result is None


def test_misuse_raises(store: ExecutionDedupStore) -> None:
    with pytest.raises(DedupStoreError):
        store.record_result(KEY, result(ExecutionStatus.UNKNOWN))
    with pytest.raises(DedupStoreError):
        store.claim("  ")


def test_restart_survival(tmp_path: Path) -> None:
    first, journal = durable(tmp_path)
    assert first.claim(KEY) is ClaimOutcome.FIRST_CLAIM
    first.record_result(KEY, result(ExecutionStatus.UNKNOWN))
    journal.close()

    second, journal2 = durable(tmp_path)
    assert second.claim(KEY) is ClaimOutcome.DUPLICATE
    record = second.lookup(KEY)
    assert record is not None and record.latest_result == result(ExecutionStatus.UNKNOWN)
    assert second.release_for_retry(KEY) is False
    assert second.claim(KEY) is ClaimOutcome.DUPLICATE
    journal2.close()


def test_release_survives_restart(tmp_path: Path) -> None:
    first, journal = durable(tmp_path)
    first.claim(KEY)
    first.record_result(KEY, result(ExecutionStatus.REJECTED))
    first.release_for_retry(KEY)
    journal.close()
    second, journal2 = durable(tmp_path)
    assert second.claim(KEY) is ClaimOutcome.FIRST_CLAIM
    journal2.close()
    third, journal3 = durable(tmp_path)
    assert third.claim(KEY) is ClaimOutcome.DUPLICATE
    journal3.close()


def _race(impl: ExecutionDedupStore) -> list[ClaimOutcome]:
    outcomes: list[ClaimOutcome] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        outcomes.append(impl.claim(KEY))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_concurrent_claims_single_winner(kind: str, tmp_path: Path) -> None:
    impl: ExecutionDedupStore = (
        InMemoryExecutionDedupStore() if kind == "memory" else durable(tmp_path)[0]
    )
    outcomes = _race(impl)
    assert outcomes.count(ClaimOutcome.FIRST_CLAIM) == 1
    assert outcomes.count(ClaimOutcome.DUPLICATE) == 7


def test_two_instances_same_storage_single_winner(tmp_path: Path) -> None:
    a, ja = durable(tmp_path)
    b, jb = durable(tmp_path)
    outcomes = [a.claim(KEY), b.claim(KEY)]
    assert sorted(outcomes) == [ClaimOutcome.DUPLICATE, ClaimOutcome.FIRST_CLAIM]
    ja.close()
    jb.close()


def test_corrupt_store_fails_closed(tmp_path: Path) -> None:
    s, journal = durable(tmp_path)
    s.claim(KEY)
    journal.connection.execute(
        "UPDATE research_journal SET payload=? WHERE event_key='claim#0'", ('{"tampered":1}',)
    )
    journal.connection.commit()
    with pytest.raises(DedupStoreCorruptError):
        s.claim(KEY)
    with pytest.raises(DedupStoreCorruptError):
        s.lookup(KEY)
    journal.close()


def test_unreadable_result_fails_closed(tmp_path: Path) -> None:
    s, journal = durable(tmp_path)
    s.claim(KEY)
    journal.append(
        f"execution-dedup:{KEY}",
        "result#0|bad",
        {"event": "result", "generation": 0, "idempotency_key": KEY, "result": {"status": "??"}},
    )
    with pytest.raises(DedupStoreCorruptError):
        s.lookup(KEY)
    with pytest.raises(DedupStoreCorruptError):
        s.release_for_retry(KEY)
    journal.close()


def test_closed_storage_fails_closed(tmp_path: Path) -> None:
    s, journal = durable(tmp_path)
    journal.close()
    with pytest.raises(DedupStoreCorruptError):
        s.claim(KEY)


def test_serialization_deterministic() -> None:
    assert serialize_result(result(ExecutionStatus.UNKNOWN)) == serialize_result(
        result(ExecutionStatus.UNKNOWN)
    )
    assert serialize_result(result(ExecutionStatus.FILLED, fill="1.0"))["filled_quantity"] == "1"
