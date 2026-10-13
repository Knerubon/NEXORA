"""DEDUP-ENUM-1 Option A: durable index stream + global enumeration of unresolved keys.

The index is discovery metadata only; every candidate is re-derived via ``inspect``.
All journals live under pytest ``tmp_path`` (or in memory). PostgreSQL is not exercised.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.execution.dedup_store import (
    INDEX_STREAM,
    STREAM_PREFIX,
    ClaimOutcome,
    DedupEnumerationIncompleteError,
    DedupKeyState,
    DedupStoreCorruptError,
    DedupStoreError,
    DedupStoreIOError,
    InMemoryExecutionDedupStore,
    JournalExecutionDedupStore,
)
from nexora.execution.models import ExecutionResult, ExecutionStatus
from nexora.storage import SQLiteJournal

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


def fresh_store() -> JournalExecutionDedupStore:
    s = InMemoryExecutionDedupStore()
    s.establish_index_genesis()
    return s


def keys_of(store: JournalExecutionDedupStore) -> list[str]:
    return [s.idempotency_key for s in store.enumerate_unresolved()]


def populate(store: JournalExecutionDedupStore) -> None:
    store.claim("k-claimed")
    store.claim("k-attempted")
    store.record_attempt("k-attempted", **ATTEMPT)
    store.claim("k-unsafe")
    store.record_attempt("k-unsafe", **ATTEMPT)
    store.record_result("k-unsafe", result(ExecutionStatus.FILLED, fill="1.0"))
    store.claim("k-unknown")
    store.record_attempt("k-unknown", **ATTEMPT)
    store.record_result("k-unknown", result(ExecutionStatus.UNKNOWN))
    store.claim("k-aborted")
    store.record_abort("k-aborted")
    store.claim("k-clean")
    store.record_attempt("k-clean", **ATTEMPT)
    store.record_result("k-clean", result(ExecutionStatus.REJECTED))
    store.claim("k-released")
    store.record_attempt("k-released", **ATTEMPT)
    store.record_result("k-released", result(ExecutionStatus.REJECTED))
    assert store.release_for_retry("k-released")


EXPECTED = ["k-aborted", "k-attempted", "k-claimed", "k-unknown", "k-unsafe"]


def test_enumerates_included_states_and_excludes_clean() -> None:
    store = fresh_store()
    populate(store)
    statuses = {s.idempotency_key: s for s in store.enumerate_unresolved()}
    assert sorted(statuses) == EXPECTED
    assert statuses["k-unsafe"].state is DedupKeyState.RESULT_UNSAFE
    assert statuses["k-attempted"].state is DedupKeyState.ATTEMPTED_NO_RESULT
    assert statuses["k-claimed"].state is DedupKeyState.CLAIMED_NOT_ATTEMPTED
    assert statuses["k-aborted"].state is DedupKeyState.ABORTED_NEVER_ATTEMPTED
    assert statuses["k-unknown"].unknown_observed
    assert store.state("k-clean") is DedupKeyState.RESULT_CLEAN_REJECTED
    assert store.state("k-released") is DedupKeyState.UNCLAIMED


def test_ever_unknown_stays_included_after_later_rejected() -> None:
    store = fresh_store()
    store.claim("k")
    store.record_attempt("k", **ATTEMPT)
    store.record_result("k", result(ExecutionStatus.UNKNOWN, rid="u"))
    store.record_result("k", result(ExecutionStatus.REJECTED, rid="r"))
    assert not store.release_for_retry("k")
    assert keys_of(store) == ["k"]  # monotonic unsafe evidence intact


def test_quarantined_key_is_included(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("k")
    journal.append(
        f"{STREAM_PREFIX}k", "bogus", {"event": "nope", "generation": 0, "idempotency_key": "k"}
    )
    assert [s.state for s in store.enumerate_unresolved()] == [DedupKeyState.QUARANTINED]


def test_deterministic_sorted_and_stable() -> None:
    store = fresh_store()
    for k in ("zz", "aa", "mm", "bb"):
        store.claim(k)
    first = store.enumerate_unresolved()
    assert [s.idempotency_key for s in first] == ["aa", "bb", "mm", "zz"]
    assert store.enumerate_unresolved() == first


def test_sqlite_and_memory_agree_and_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "d.sqlite"
    durable = JournalExecutionDedupStore(SQLiteJournal(path))
    durable.establish_index_genesis()
    populate(durable)
    memory = fresh_store()
    populate(memory)
    expected = [(s.idempotency_key, s.state, s.generation) for s in memory.enumerate_unresolved()]
    assert [
        (s.idempotency_key, s.state, s.generation) for s in durable.enumerate_unresolved()
    ] == expected
    reopened = JournalExecutionDedupStore(SQLiteJournal(path))
    assert keys_of(reopened) == EXPECTED
    assert [
        (s.idempotency_key, s.state, s.generation) for s in reopened.enumerate_unresolved()
    ] == expected


def test_key_unknown_to_any_caller_set_is_found() -> None:
    store = fresh_store()
    store.claim("never-named")
    assert store.unresolved_among(["something-else"]) == ()  # caller-scoped: blind
    assert keys_of(store) == ["never-named"]


def test_claim_outcomes_unchanged_and_index_idempotent(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    store = JournalExecutionDedupStore(journal)
    assert store.claim("k") is ClaimOutcome.FIRST_CLAIM
    assert store.claim("k") is ClaimOutcome.DUPLICATE
    assert store.claim("k") is ClaimOutcome.DUPLICATE
    assert len(journal.read(INDEX_STREAM)) == 1  # no unbounded duplicates
    assert store.register_known_keys(["k", "j"]) == 1
    assert store.register_known_keys(["k", "j"]) == 0


# -- ordering / crash injection ------------------------------------------------------


class CrashAfterIndexJournal:
    """Delegating journal that dies on the first write to a dedup key stream."""

    backend = "crash"

    def __init__(self, inner: SQLiteJournal, *, crash_on_claim: bool = True) -> None:
        self.inner = inner
        self.crash_on_claim = crash_on_claim
        self.order: list[str] = []

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        self.order.append(stream)
        if self.crash_on_claim and stream.startswith(STREAM_PREFIX):
            raise RuntimeError("simulated crash before claim")
        return self.inner.append(stream, key, payload, expected_count=expected_count)

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        return self.inner.read(stream)

    def iter_read(self, stream: str) -> Any:
        return self.inner.iter_read(stream)

    def close(self) -> None:
        self.inner.close()


def test_index_is_written_before_claim(tmp_path: Path) -> None:
    journal = CrashAfterIndexJournal(SQLiteJournal(tmp_path / "d.sqlite"), crash_on_claim=False)
    JournalExecutionDedupStore(journal).claim("k")
    assert journal.order[0] == INDEX_STREAM
    assert journal.order[1].startswith(STREAM_PREFIX)


def test_crash_between_index_and_claim_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "d.sqlite"
    crashing = JournalExecutionDedupStore(CrashAfterIndexJournal(SQLiteJournal(path)))
    with pytest.raises(DedupStoreIOError):
        crashing.claim("k")
    after = JournalExecutionDedupStore(SQLiteJournal(path))
    after.establish_index_genesis()
    assert after.state("k") is DedupKeyState.UNCLAIMED  # index entry, no claim
    assert keys_of(after) == []  # harmless: not unresolved, and never "allowed" by the index
    assert after.claim("k") is ClaimOutcome.FIRST_CLAIM  # retry works
    assert keys_of(after) == ["k"]


def test_index_write_failure_blocks_claim(tmp_path: Path) -> None:
    class IndexDown(CrashAfterIndexJournal):
        def append(
            self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
        ) -> bool:
            if stream == INDEX_STREAM:
                raise OSError("index down")
            return super().append(stream, key, payload, expected_count=expected_count)

    inner = SQLiteJournal(tmp_path / "d.sqlite")
    store = JournalExecutionDedupStore(IndexDown(inner, crash_on_claim=False))
    with pytest.raises(DedupStoreIOError):
        store.claim("k")
    assert inner.read(f"{STREAM_PREFIX}k") == ()  # claim never written without index


# -- fail closed ---------------------------------------------------------------------


class FlakyIndexJournal:
    backend = "flaky"

    def __init__(self, inner: SQLiteJournal) -> None:
        self.inner = inner
        self.fail_index_read = False
        self.fail_key_read = False

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        return self.inner.append(stream, key, payload, expected_count=expected_count)

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        if (stream == INDEX_STREAM and self.fail_index_read) or (
            stream != INDEX_STREAM and self.fail_key_read
        ):
            raise OSError("read down")
        return self.inner.read(stream)

    def iter_read(self, stream: str) -> Any:
        return self.inner.iter_read(stream)

    def close(self) -> None:
        self.inner.close()


def test_index_read_failure_is_io_error(tmp_path: Path) -> None:
    journal = FlakyIndexJournal(SQLiteJournal(tmp_path / "d.sqlite"))
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("k")
    journal.fail_index_read = True
    with pytest.raises(DedupStoreIOError):
        store.enumerate_unresolved()


def test_per_key_read_failure_propagates_never_partial(tmp_path: Path) -> None:
    journal = FlakyIndexJournal(SQLiteJournal(tmp_path / "d.sqlite"))
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("a")
    store.claim("b")
    journal.fail_key_read = True
    with pytest.raises(DedupStoreIOError):
        store.enumerate_unresolved()


def test_missing_genesis_fails_closed_with_distinct_reason() -> None:
    store = InMemoryExecutionDedupStore()
    store.claim("k")
    with pytest.raises(DedupEnumerationIncompleteError, match="genesis_missing") as info:
        store.enumerate_unresolved()
    assert not isinstance(info.value, (DedupStoreIOError, DedupStoreCorruptError))
    assert isinstance(info.value, DedupStoreError)


def test_empty_journal_without_genesis_is_not_complete() -> None:
    with pytest.raises(DedupEnumerationIncompleteError):
        InMemoryExecutionDedupStore().enumerate_unresolved()


def test_genesis_empty_means_complete_empty() -> None:
    assert fresh_store().enumerate_unresolved() == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"event": "index_register"},
        {"event": "index_register", "idempotency_key": "  "},
        {"event": "index_register", "idempotency_key": 7},
        {"event": "index_register", "idempotency_key": "x", "extra": 1},
        {"event": "other", "idempotency_key": "x"},
        {"event": "index_genesis", "version": 2},
    ],
)
def test_malformed_index_row_raises_corrupt(tmp_path: Path, bad: dict[str, Any]) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("good")
    journal.append(INDEX_STREAM, "key#evil", bad)
    with pytest.raises(DedupStoreCorruptError):
        store.enumerate_unresolved()


def test_duplicate_registration_payload_raises_corrupt(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("k")
    journal.append(INDEX_STREAM, "key#alias", {"event": "index_register", "idempotency_key": "k"})
    with pytest.raises(DedupStoreCorruptError):
        store.enumerate_unresolved()


def test_tampered_index_row_hash_raises_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "d.sqlite"
    journal = SQLiteJournal(path)
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("k")
    with journal.connection:
        journal.connection.execute(
            "UPDATE research_journal SET payload=? WHERE stream=? AND event_key=?",
            ('{"event": "index_register", "idempotency_key": "other"}', INDEX_STREAM, "key#k"),
        )
    with pytest.raises(DedupStoreCorruptError):
        JournalExecutionDedupStore(SQLiteJournal(path)).enumerate_unresolved()


def test_corrupt_key_stream_is_quarantined_not_dropped(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis()
    store.claim("k")
    with journal.connection:
        journal.connection.execute(
            "UPDATE research_journal SET payload='{\"x\":1}' WHERE stream=?",
            (f"{STREAM_PREFIX}k",),
        )
    statuses = store.enumerate_unresolved()
    assert [s.state for s in statuses] == [DedupKeyState.QUARANTINED]


# -- legacy keys ---------------------------------------------------------------------


def _legacy_claim(journal: SQLiteJournal, key: str) -> None:
    """A claim written by pre-index code: stream row only, no index row."""

    journal.append(
        f"{STREAM_PREFIX}{key}",
        "claim#0",
        {"event": "claim", "generation": 0, "idempotency_key": key},
    )


def test_legacy_claim_without_index_is_not_claimed_complete(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    _legacy_claim(journal, "legacy")
    store = JournalExecutionDedupStore(journal)
    store.claim("new")
    with pytest.raises(DedupEnumerationIncompleteError):  # no genesis -> refuse
        store.enumerate_unresolved()
    assert store.unindexed_claims_among(["legacy", "new"]) == ("legacy",)


def test_legacy_keys_registered_then_genesis_makes_them_discoverable(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "d.sqlite")
    _legacy_claim(journal, "legacy")
    store = JournalExecutionDedupStore(journal)
    store.claim("new")
    store.establish_index_genesis(["legacy"])
    store.establish_index_genesis(["legacy"])  # idempotent
    assert keys_of(store) == ["legacy", "new"]
    assert store.unindexed_claims_among(["legacy", "new"]) == ()


def test_unlisted_legacy_key_remains_invisible_after_genesis(tmp_path: Path) -> None:
    """Documented limit: genesis is an operator assertion the store cannot verify."""

    journal = SQLiteJournal(tmp_path / "d.sqlite")
    _legacy_claim(journal, "forgotten")
    store = JournalExecutionDedupStore(journal)
    store.establish_index_genesis([])
    assert keys_of(store) == []  # invisible to enumeration ...
    assert store.unindexed_claims_among(["forgotten"]) == ("forgotten",)  # ... but auditable


# -- index is never permission -------------------------------------------------------


def test_index_row_alone_never_yields_allow_or_inclusion() -> None:
    store = fresh_store()
    store.register_known_keys(["only-indexed"])
    assert store.state("only-indexed") is DedupKeyState.UNCLAIMED
    assert store.lookup("only-indexed") is None
    assert keys_of(store) == []
    assert store.claim("only-indexed") is ClaimOutcome.FIRST_CLAIM  # still needs a real claim
    assert keys_of(store) == ["only-indexed"]


def test_registration_does_not_alter_existing_key_state() -> None:
    store = fresh_store()
    store.claim("k")
    store.record_attempt("k", **ATTEMPT)
    store.record_result("k", result(ExecutionStatus.FILLED, fill="1.0"))
    before = store.inspect("k")
    store.register_known_keys(["k"])
    store.establish_index_genesis(["k"])
    assert store.inspect("k") == before
    assert not store.release_for_retry("k")


def test_blank_key_registration_refused() -> None:
    store = fresh_store()
    with pytest.raises(DedupStoreError):
        store.register_known_keys([" "])
    with pytest.raises(DedupStoreError):
        store.claim("")


# -- concurrency ---------------------------------------------------------------------


def test_two_instances_no_lost_registration(tmp_path: Path) -> None:
    path = tmp_path / "d.sqlite"
    a = JournalExecutionDedupStore(SQLiteJournal(path))
    b = JournalExecutionDedupStore(SQLiteJournal(path))
    a.establish_index_genesis()
    errors: list[BaseException] = []

    def work(store: JournalExecutionDedupStore, keys: list[str]) -> None:
        try:
            for k in keys:
                store.claim(k)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    shared = [f"shared-{i}" for i in range(15)]
    ta = threading.Thread(target=work, args=(a, shared + [f"a-{i}" for i in range(15)]))
    tb = threading.Thread(target=work, args=(b, shared[::-1] + [f"b-{i}" for i in range(15)]))
    ta.start()
    tb.start()
    ta.join()
    tb.join()
    assert not errors
    expected = sorted(shared + [f"a-{i}" for i in range(15)] + [f"b-{i}" for i in range(15)])
    assert keys_of(a) == expected
    assert keys_of(b) == expected
    assert len(SQLiteJournal(path).read(INDEX_STREAM)) == len(expected) + 1  # + genesis


def test_two_processes_no_lost_registration(tmp_path: Path) -> None:
    path = tmp_path / "d.sqlite"
    JournalExecutionDedupStore(SQLiteJournal(path)).establish_index_genesis()
    code = (
        "import sys;"
        "from nexora.execution.dedup_store import JournalExecutionDedupStore as S;"
        "from nexora.storage import SQLiteJournal as J;"
        "s=S(J(sys.argv[1]));"
        "[s.claim(f'{sys.argv[2]}-{i}') for i in range(10)];"
        "[s.claim(f'common-{i}') for i in range(10)]"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, str(path), tag]) for tag in ("p1", "p2")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    store = JournalExecutionDedupStore(SQLiteJournal(path))
    expected = sorted(
        [f"{t}-{i}" for t in ("p1", "p2") for i in range(10)] + [f"common-{i}" for i in range(10)]
    )
    assert keys_of(store) == expected


def test_protocol_exposes_accessor() -> None:
    from nexora.execution.dedup_store import ExecutionDedupStore

    assert "enumerate_unresolved" in dir(ExecutionDedupStore)
    _: Callable[[], Any] = fresh_store().enumerate_unresolved
