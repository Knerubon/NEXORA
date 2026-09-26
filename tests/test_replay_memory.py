"""Replay memory regressions and the REPLAY-MEM-1 harness, on small synthetic journals."""

from __future__ import annotations

import gc
import importlib.util
import json
import sys
import weakref
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from nexora.research import ResearchPipeline
from nexora.research.runtime import ResearchRuntime
from nexora.storage import SQLiteJournal

from tests.test_recovery_checkpoint import runtime_config

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "replay_memory_harness.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("replay_memory_harness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


harness = load()


def records(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def test_synthetic_stream_is_deterministic_and_prod_like_sparse() -> None:
    first = list(harness.synthetic_events(1500))
    assert first == list(harness.synthetic_events(1500))
    assert first != list(harness.synthetic_events(1500, seed=1))
    pipeline = ResearchPipeline(runtime_config().pipeline)
    for event in first:
        pipeline.replay(event)
    transitions = pipeline.structure._sequence
    # PROD dry-run: ~1.5-1.9 structure transitions per 100 events; the test zig-zag
    # stream (tests.test_recovery_checkpoint.stream_events) transitions on most events.
    assert 0.5 <= transitions * 100 / len(first) <= 4


def test_repeated_full_replays_release_the_previous_runtime(tmp_path: Path) -> None:
    config, journal = runtime_config(), SQLiteJournal(tmp_path / "r.sqlite")
    try:
        live = ResearchRuntime(config, journal)
        for event in harness.synthetic_events(120):
            live.ingest(event)
        del live
        references: list[weakref.ref[Any]] = []
        for _ in range(3):
            runtime = ResearchRuntime(config, journal)
            assert runtime.last_recovery["replayed"] == 120
            references += [weakref.ref(runtime.engine), weakref.ref(runtime.experience)]
            del runtime
            gc.collect()
        assert [reference() for reference in references] == [None] * len(references)
    finally:
        journal.close()


def test_retained_replay_state_per_event_stays_small(tmp_path: Path) -> None:
    """Retention is O(events) today (REPLAY-MEM-1); this bounds its slope, not its shape.

    Holding a per-event copy of the recorded output, as the journal row carries it,
    would exceed this bound immediately.
    """
    config, journal = runtime_config(), SQLiteJournal(tmp_path / "r.sqlite")
    try:
        runtime = ResearchRuntime(config, journal)
        events = list(harness.synthetic_events(400))
        for event in events[:200]:
            runtime.ingest(event)
        before = harness.structure_report(runtime)["total_bytes"]
        for event in events[200:]:
            runtime.ingest(event)
        after = harness.structure_report(runtime)["total_bytes"]
        assert 0 < (after - before) / 200 < 16 * 1024
    finally:
        journal.close()


def test_harness_measures_replay_and_checkpoint_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workdir = tmp_path / "work"
    assert harness.main(["build", "--workdir", str(workdir), "--events", "90"]) == 0
    built = records(capsys.readouterr().out)[-1]
    assert built["research_rows"] == 90 and built["research_max_row_bytes"] > 0
    arguments = ["--workdir", str(workdir), "--sample-every", "30"]
    assert harness.main(["replay", *arguments, "--repeat", "2", "--deep", "--tracemalloc"]) == 0
    runs = [r["run"] for r in records(capsys.readouterr().out) if "run" in r]
    assert [run["rows"] for run in runs] == [90, 90]
    for run in runs:
        assert run["after_release"]["runtime_collected"] is True
        assert set(run["after_release"]["live_over_start"].values()) == {0}
        assert run["retained"]["total_bytes"] > 0
        assert run["transient"]["worst"]["window_peak_over_start"] > 0
    # Tracking is scoped to the harness call.
    assert ResearchRuntime._rebuild.__qualname__ == "ResearchRuntime._rebuild"
    assert harness.main(["checkpoint", *arguments]) == 0
    output = records(capsys.readouterr().out)
    steps = [r["checkpoint_step"]["step"] for r in output if "checkpoint_step" in r]
    assert "encode_state" in steps and "verify(sha256+json.loads)" in steps
    assert output[-1]["restore_mode"] == "checkpoint" and output[-1]["restore_replayed"] == 0
    composition = next(r["composition"] for r in output if "composition" in r)
    assert composition["events"] > 0 and set(composition["runners"]) == {"fast", "medium", "slow"}
