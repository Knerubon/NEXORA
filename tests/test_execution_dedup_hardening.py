"""PR-4 prerequisite hardening of the dedup store: exception hierarchy, opaque refs, guards."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from nexora.execution.dedup_store import (
    MAX_OPAQUE_REF_LENGTH,
    DedupKeyState,
    DedupMarkerRefusedError,
    DedupStoreCorruptError,
    DedupStoreError,
    DedupStoreIOError,
    JournalExecutionDedupStore,
    attempt_payload,
    opaque_ref_violation,
)
from nexora.execution.models import ExecutionStatus

from tests.test_execution_dedup_attempt_abort import (
    ATTEMPT,
    KEY,
    STREAM,
    FlakyJournal,
    durable,
    flaky,
    raw_append,
    result,
)

# -- hierarchy --------------------------------------------------------------


def test_io_and_corrupt_errors_are_siblings_under_dedup_store_error() -> None:
    assert issubclass(DedupStoreIOError, DedupStoreError)
    assert issubclass(DedupStoreCorruptError, DedupStoreError)
    assert not issubclass(DedupStoreIOError, DedupStoreCorruptError)
    assert not issubclass(DedupStoreCorruptError, DedupStoreIOError)
    assert issubclass(DedupMarkerRefusedError, DedupStoreError)
    assert not issubclass(DedupMarkerRefusedError, DedupStoreCorruptError)


def _io_calls(s: JournalExecutionDedupStore) -> dict[str, Callable[[], Any]]:
    return {
        "claim": lambda: s.claim(KEY),
        "inspect": lambda: s.inspect(KEY),
        "state": lambda: s.state(KEY),
        "lookup": lambda: s.lookup(KEY),
        "record_attempt": lambda: s.record_attempt(KEY, **ATTEMPT),
        "record_abort": lambda: s.record_abort(KEY),
        "record_result": lambda: s.record_result(KEY, result(ExecutionStatus.REJECTED)),
        "release_for_retry": lambda: s.release_for_retry(KEY),
        "unresolved_among": lambda: s.unresolved_among([KEY]),
    }


@pytest.mark.parametrize(
    "name",
    [
        "claim",
        "inspect",
        "state",
        "lookup",
        "record_attempt",
        "record_abort",
        "record_result",
        "release_for_retry",
        "unresolved_among",
    ],
)
def test_io_failure_is_denied_not_integrity_not_success(name: str, tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    j.fail_read = True
    j.fail_append = True
    call = _io_calls(s)[name]
    with pytest.raises(DedupStoreIOError) as err:
        call()
    assert not isinstance(err.value, DedupStoreCorruptError)
    # A catch site written for integrity errors does NOT swallow I/O errors ...
    swallowed = False
    try:
        call()
    except DedupStoreCorruptError:
        swallowed = True
    except DedupStoreError:
        pass
    assert swallowed is False
    # ... while a catch of the base class still denies both classes.
    with pytest.raises(DedupStoreError):
        call()
    j.fail_read = False
    j.fail_append = False
    # No quarantine was recorded by the I/O failure.
    status = s.inspect(KEY)
    assert status.state is DedupKeyState.CLAIMED_NOT_ATTEMPTED
    assert status.violation_code is None


def _append_bogus(j: FlakyJournal) -> None:
    j.inner.append(STREAM, "bogus#0", {"event": "bogus", "generation": 0, "idempotency_key": KEY})


def test_inspect_site_integrity_quarantines_io_propagates(tmp_path: Path) -> None:
    s, j = flaky(tmp_path)
    s.claim(KEY)
    _append_bogus(j)
    status = s.inspect(KEY)
    assert status.state is DedupKeyState.QUARANTINED
    j.fail_read = True
    with pytest.raises(DedupStoreIOError):
        s.inspect(KEY)  # not reported as QUARANTINED


def test_closed_storage_is_io_class_and_denied(tmp_path: Path) -> None:
    s, journal = durable(tmp_path)
    journal.close()
    with pytest.raises(DedupStoreError) as err:
        s.claim(KEY)
    assert not isinstance(err.value, DedupStoreCorruptError)


def test_integrity_violation_is_not_caught_as_io(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    raw_append(j, "bogus#0", {"event": "bogus", "generation": 0, "idempotency_key": KEY})
    for call in (lambda: s.claim(KEY), lambda: s.lookup(KEY), lambda: s.release_for_retry(KEY)):
        with pytest.raises(DedupStoreCorruptError) as err:
            call()
        assert not isinstance(err.value, DedupStoreIOError)
    j.close()


# -- evidence refs are opaque, bounded, non-secret ---------------------------

_REF_FIELDS = ("request_digest", "reconciliation_evidence_ref", "preflight_decision_ref")


@pytest.mark.parametrize("field", _REF_FIELDS)
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("x" * (MAX_OPAQUE_REF_LENGTH + 1), "too_long"),
        ("user@example.com", "charset_invalid"),
        ("two words", "charset_invalid"),
        ("line\nbreak", "charset_invalid"),
        ('{"a": 1}', "charset_invalid"),
        ("key=abc", "charset_invalid"),
        ("Bearer-abc123", "secret_like"),
        ("password-1", "secret_like"),
        ("api_key:abc", "secret_like"),
        ("prefix-TOKEN-1", "secret_like"),
        ("-----BEGIN-KEY", "secret_like"),
        ("evidence-é", "charset_invalid"),
    ],
)
def test_attempt_rejects_non_opaque_refs(field: str, value: str, reason: str) -> None:
    kwargs = {**ATTEMPT, field: value}
    with pytest.raises(DedupStoreError, match=f"attempt_{field}_{reason}"):
        attempt_payload(KEY, 0, **kwargs)


@pytest.mark.parametrize("field", _REF_FIELDS)
def test_rejected_ref_writes_nothing_and_key_stays_claimed(field: str, tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    with pytest.raises(DedupStoreError):
        s.record_attempt(KEY, **{**ATTEMPT, field: "a@b.co"})
    assert s.state(KEY) is DedupKeyState.CLAIMED_NOT_ATTEMPTED
    assert [e["event"] for e in j.read(STREAM)] == ["claim"]
    j.close()


@pytest.mark.parametrize(
    "value",
    ["evidence-1", "sha256:" + "a" * 64, "preflight/req#3|v2", "x" * MAX_OPAQUE_REF_LENGTH],
)
def test_opaque_refs_accepted(value: str) -> None:
    assert opaque_ref_violation(value) is None
    payload = attempt_payload(KEY, 0, **{**ATTEMPT, "reconciliation_evidence_ref": value})
    assert payload["reconciliation_evidence_ref"] == value


# -- PR-4 guard: access state via inspect()/state(), never latest_result -------


def test_inspect_reports_unsafe_when_latest_result_looks_clean(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_attempt(KEY, **ATTEMPT)
    s.record_result(KEY, result(ExecutionStatus.FILLED, "r1", fill="1.0"))
    s.record_result(KEY, result(ExecutionStatus.REJECTED, "r2"))
    record = s.lookup(KEY)
    assert record is not None
    assert record.latest_result is not None
    assert record.latest_result.status is ExecutionStatus.REJECTED  # looks clean
    assert s.state(KEY) is DedupKeyState.RESULT_UNSAFE  # authoritative
    assert s.release_for_retry(KEY) is False
    assert s.unresolved_among([KEY]) == ()  # caller-scoped accessor is NOT the OPEN-16 gate
    j.close()


def test_unknown_then_rejected_stays_unresolved_and_unreleasable(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    s.record_attempt(KEY, **ATTEMPT)
    s.record_result(KEY, result(ExecutionStatus.UNKNOWN, "r1"))
    s.record_result(KEY, result(ExecutionStatus.REJECTED, "r2"))
    assert s.inspect(KEY).unknown_observed is True
    assert s.unresolved_among([KEY]) == (KEY,)
    assert s.release_for_retry(KEY) is False
    j.close()


def test_quarantined_state_is_visible_only_via_inspect(tmp_path: Path) -> None:
    s, j = durable(tmp_path)
    s.claim(KEY)
    raw_append(j, "bogus#0", {"event": "bogus", "generation": 0, "idempotency_key": KEY})
    assert s.state(KEY) is DedupKeyState.QUARANTINED
    assert s.unresolved_among([KEY]) == ()  # not reported; gate must use inspect()/state()
    with pytest.raises(DedupStoreCorruptError):
        s.lookup(KEY)
    j.close()
