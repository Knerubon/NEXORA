"""Checkpoint age trigger (ADR-029 H3) and recovery observability (ADR-029 H5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from nexora.research.checkpoint import CheckpointStore
from nexora.research.runtime import ResearchRuntime
from nexora.storage import SQLiteJournal

from tests.test_recovery_checkpoint import (
    _api_environment,
    full_replay,
    runtime_config,
    state,
    store_for,
    stream_events,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def counting_saves(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the last_sequence of every checkpoint written."""
    saves: list[int] = []
    original = CheckpointStore.save

    def save(self: CheckpointStore, header: Any, blob: bytes) -> Path:
        saves.append(header.last_sequence)
        return original(self, header, blob)

    monkeypatch.setattr(CheckpointStore, "save", save)
    return saves


def aged(
    tmp_path: Path, clock: FakeClock, *, interval: int = 100, max_age: float | None = 60
) -> tuple[SQLiteJournal, ResearchRuntime]:
    journal = SQLiteJournal(tmp_path / "r.sqlite")
    runtime = ResearchRuntime(
        runtime_config(),
        journal,
        checkpoints=store_for(tmp_path),
        checkpoint_interval=interval,
        checkpoint_max_age=max_age,
        clock=clock,
    )
    return journal, runtime


def ingest_at(runtime: ResearchRuntime, clock: FakeClock, at: float, event: Any) -> None:
    clock.now = at
    runtime.ingest(event)


# H3 -----------------------------------------------------------------------------------
def test_age_trigger_writes_before_the_count_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves = counting_saves(monkeypatch)
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock)
    try:
        events = stream_events(3)
        ingest_at(runtime, clock, 0, events[0])
        ingest_at(runtime, clock, 59.9, events[1])
        assert saves == []
        ingest_at(runtime, clock, 60, events[2])
        assert len(saves) == 1 and runtime.recovery_status()["uncovered_events"] == 0
        header, _ = store_for(tmp_path).load(runtime.stream) or (None, None)
        assert header is not None and header.event_count == 3
    finally:
        journal.close()


def test_age_counts_from_oldest_uncovered_ingest_not_from_last_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves = counting_saves(monkeypatch)
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock)
    try:
        events = stream_events(6)
        for at, event in zip((0, 10, 60), events[:3], strict=True):
            ingest_at(runtime, clock, at, event)
        assert len(saves) == 1
        # A long idle gap without events writes nothing and does not make the next event due.
        ingest_at(runtime, clock, 500, events[3])
        ingest_at(runtime, clock, 559, events[4])
        assert len(saves) == 1
        ingest_at(runtime, clock, 560, events[5])
        assert len(saves) == 2
    finally:
        journal.close()


def test_age_is_checked_on_new_ingests_and_never_writes_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves = counting_saves(monkeypatch)
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock, max_age=1)
    try:
        event = stream_events(1)[0]
        ingest_at(runtime, clock, 5, event)
        ingest_at(runtime, clock, 6, event)
        assert saves == []
        ingest_at(runtime, clock, 1_000, event)  # duplicate identity: not an ingest, no timer
        runtime.checkpoint()
        assert len(saves) == 1  # only the explicit checkpoint of the one uncovered event
        ingest_at(runtime, clock, 5_000, event)
        runtime.checkpoint()
        assert len(saves) == 1
    finally:
        journal.close()


def test_count_and_age_due_together_write_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves = counting_saves(monkeypatch)
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock, interval=3, max_age=10)
    try:
        for at, event in zip((0, 5, 20, 21), stream_events(4), strict=True):
            ingest_at(runtime, clock, at, event)
        assert len(saves) == 1 and runtime.recovery_status()["uncovered_events"] == 1
    finally:
        journal.close()


def test_count_trigger_unchanged_when_age_trigger_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves = counting_saves(monkeypatch)
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock, interval=4, max_age=None)
    try:
        for i, event in enumerate(stream_events(9)):
            ingest_at(runtime, clock, i * 1_000_000, event)
        assert len(saves) == 2  # after events 4 and 8, as before ADR-029
    finally:
        journal.close()


def test_failed_age_write_retries_on_next_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock, max_age=10)
    attempts: list[int] = []
    original = CheckpointStore.save

    def flaky(self: CheckpointStore, header: Any, blob: bytes) -> Path:
        attempts.append(header.last_sequence)
        if len(attempts) == 1:
            raise OSError("simulated disk full")
        return original(self, header, blob)

    monkeypatch.setattr(CheckpointStore, "save", flaky)
    try:
        events = stream_events(3)
        ingest_at(runtime, clock, 0, events[0])
        ingest_at(runtime, clock, 10, events[1])  # due; write fails, ingest succeeds
        assert runtime.error is None and runtime.recovery_status()["uncovered_events"] == 2
        ingest_at(runtime, clock, 11, events[2])
        assert len(attempts) == 2 and runtime.recovery_status()["uncovered_events"] == 0
    finally:
        journal.close()


def test_age_triggered_checkpoint_recovers_to_full_replay_state(tmp_path: Path) -> None:
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock, max_age=30)
    try:
        events = stream_events(40)
        for i, event in enumerate(events):
            ingest_at(runtime, clock, i * 7, event)
        written = runtime.recovery_status()["last_checkpoint"]
        assert written is not None and 0 < written["events"] < len(events)
        recovered = ResearchRuntime(runtime_config(), journal, checkpoints=store_for(tmp_path))
        assert recovered.last_recovery["mode"] == "checkpoint"
        assert recovered.last_recovery["replayed"] == len(events) - written["events"]
        assert state(recovered) == state(full_replay(runtime_config(), journal))
    finally:
        journal.close()


@pytest.mark.parametrize("value", [0, -1, float("nan")])
def test_invalid_checkpoint_max_age_is_rejected(tmp_path: Path, value: float) -> None:
    journal = SQLiteJournal(tmp_path / "r.sqlite")
    try:
        with pytest.raises(ValueError, match="invalid_checkpoint_max_age"):
            ResearchRuntime(runtime_config(), journal, checkpoint_max_age=value)
    finally:
        journal.close()


def test_api_checkpoint_max_age_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexora_api.research import configured_checkpoint_max_age

    monkeypatch.delenv("NEXORA_RESEARCH_CHECKPOINT_MAX_AGE_SECONDS", raising=False)
    assert configured_checkpoint_max_age() is None
    monkeypatch.setenv("NEXORA_RESEARCH_CHECKPOINT_MAX_AGE_SECONDS", " 90 ")
    assert configured_checkpoint_max_age() == 90.0
    for bad in ("0", "-5", "abc", "inf", "nan"):
        monkeypatch.setenv("NEXORA_RESEARCH_CHECKPOINT_MAX_AGE_SECONDS", bad)
        with pytest.raises(ValueError, match="invalid_research_checkpoint_max_age"):
            configured_checkpoint_max_age()


# H5 -----------------------------------------------------------------------------------
def test_recovery_status_reports_known_facts_without_changing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    journal, runtime = aged(tmp_path, clock, max_age=None)
    try:
        for event in stream_events(12):
            runtime.ingest(event)
        clock.now = 100
        runtime.checkpoint()
        clock.now = 130.5
        before = state(runtime)
        status = runtime.recovery_status()
        assert state(runtime) == before
        assert status == {
            "checkpoints": "on",
            "recovered": True,
            "mode": "full",
            "reason": "no_checkpoint",
            "checkpoint_sequence": None,
            "replayed": 0,
            "duration_ms": status["duration_ms"],
            "uncovered_events": 0,
            "last_checkpoint": {
                "sequence": status["last_checkpoint"]["sequence"],
                "events": 12,
                "bytes": status["last_checkpoint"]["bytes"],
                "write_ms": status["last_checkpoint"]["write_ms"],
                "age_s": 30.5,
            },
        }
        assert status["last_checkpoint"]["bytes"] > 0
        warm = ResearchRuntime(runtime_config(), journal, checkpoints=store_for(tmp_path))
        restored = warm.recovery_status()
        assert (restored["mode"], restored["replayed"]) == ("checkpoint", 0)
        assert restored["checkpoint_sequence"] == status["last_checkpoint"]["sequence"]
        # Not written by this process, so no write facts are claimed.
        assert restored["last_checkpoint"] is None
        disabled = ResearchRuntime(runtime_config(), journal).recovery_status()
        assert (disabled["checkpoints"], disabled["reason"]) == ("off", "checkpoints_disabled")
        assert disabled["replayed"] == 12
    finally:
        journal.close()


def _readiness(events: int = 0) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from nexora_api.main import create_app

    app = create_app(start_worker=False)
    with TestClient(app, base_url="http://localhost") as client:
        runtime = app.state.runtime
        for event in stream_events(events):
            runtime.ingest(event)
        result: dict[str, Any] = client.get("/operations/readiness").json()
        return result


def test_readiness_exposes_recovery_without_affecting_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _api_environment(tmp_path, monkeypatch, "development")
    first = _readiness(events=12)
    assert first["recovery"]["mode"] == "full"
    assert first["recovery"]["reason"] == "no_checkpoint"
    assert first["recovery"]["uncovered_events"] == 12
    warm = _readiness()
    header = json.loads(next((root / "checkpoints").iterdir()).read_bytes().split(b"\n", 1)[0])
    assert warm["recovery"]["mode"] == "checkpoint"
    assert warm["recovery"]["checkpoint_sequence"] == header["last_sequence"]
    assert warm["recovery"]["replayed"] == 0
    monkeypatch.setenv("NEXORA_RESEARCH_CHECKPOINTS", "off")
    disabled = _readiness()
    assert disabled["recovery"]["reason"] == "checkpoints_disabled"
    # Informational only: identical status and reasons whichever recovery path ran.
    assert (warm["status"], warm["reasons"]) == (disabled["status"], disabled["reasons"])
    assert not any("recovery" in reason or "checkpoint" in reason for reason in warm["reasons"])


def test_readiness_recovery_is_null_without_research_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _api_environment(tmp_path, monkeypatch, "development")
    monkeypatch.delenv("NEXORA_RESEARCH_CONFIG")
    assert _readiness()["recovery"] is None
