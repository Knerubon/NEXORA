"""PR-4 security delta: real multi-process race on a shared SQLite dedup file.

N processes run the same intent through the TEST-ONLY seam against ONE journal. The adapter
returns a clean zero-fill REJECTED (releasable) or FILLED. Invariant: at most ONE submit per
key, whatever the interleaving (a released generation is never retried, OPEN-14/15)."""

from __future__ import annotations

import multiprocessing as mp
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from nexora.execution.dedup_store import JournalExecutionDedupStore
from nexora.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from nexora.execution.pipeline import (
    ExecutionPipeline,
    non_production_transmission_seam,
)
from nexora.storage import SQLiteJournal

from tests.test_execution_pipeline import AllowPreflightForTestsOnly, _inputs, _result

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PROCESSES = 4
TRIALS = 12


class CountingAdapterForTestsOnly:
    """TEST-ONLY adapter: counts submits; never reaches anything real."""

    NEXORA_NON_PRODUCTION_TEST_ADAPTER = True

    def __init__(self, status: ExecutionStatus) -> None:
        self.status = status
        self.submits = 0

    def capabilities(self) -> Any:  # pragma: no cover
        raise NotImplementedError

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        self.submits += 1
        return _result(request, self.status)


def _worker(
    paths: list[str], status_name: str, barrier: Any, out: Any
) -> None:  # pragma: no cover - runs in a child process
    for trial, path in enumerate(paths):
        adapter = CountingAdapterForTestsOnly(ExecutionStatus(status_name))
        store = JournalExecutionDedupStore(SQLiteJournal(Path(path)))
        pipeline = ExecutionPipeline(
            dedup_store=store,
            clock=lambda: NOW,
            instrument_resolver={"SYM": "inst-1"}.get,
            max_reconciliation_evidence_age=timedelta(seconds=60),
            max_preflight_age=timedelta(seconds=60),
            transmission=non_production_transmission_seam(
                adapter, preflight=AllowPreflightForTestsOnly([])
            ),
        )
        inputs = _inputs()
        barrier.wait(timeout=60)
        try:
            pipeline.run(inputs)
        except Exception:
            pass
        out.put((trial, adapter.submits))


def _race(tmp_path: Path, status: ExecutionStatus) -> list[int]:
    paths: list[str] = []
    for trial in range(TRIALS):
        path = tmp_path / f"d{trial}-{status.value}.sqlite"
        JournalExecutionDedupStore(SQLiteJournal(path)).establish_index_genesis()
        paths.append(str(path))
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(PROCESSES)
    out = ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(paths, status.value, barrier, out))
        for _ in range(PROCESSES)
    ]
    for proc in procs:
        proc.start()
    totals = [0] * TRIALS
    for _ in range(PROCESSES * TRIALS):
        trial, submits = out.get(timeout=120)
        totals[trial] += submits
    for proc in procs:
        proc.join(timeout=60)
    return totals


@pytest.mark.parametrize("status", [ExecutionStatus.REJECTED, ExecutionStatus.FILLED])
def test_shared_sqlite_race_submits_at_most_once_per_key(
    tmp_path: Path, status: ExecutionStatus
) -> None:
    totals = _race(tmp_path, status)
    assert max(totals) <= 1, totals
