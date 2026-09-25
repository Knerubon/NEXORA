"""The synthetic recovery benchmark measures the paths it claims (ADR-029 Phase 2A)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from nexora.experience import ExperienceService

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recovery_benchmark.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("recovery_benchmark", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = load()


def test_synthetic_events_are_deterministic_and_ordered() -> None:
    first, second = bench.synthetic_events(300), bench.synthetic_events(300)
    assert first == second
    assert first != bench.synthetic_events(300, seed=1)
    times = [event.event_time for event in first]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert len({event.identity_key for event in first}) == 300
    steps = {abs(b.price - a.price) for a, b in zip(first, first[1:], strict=False)}
    assert max(steps) * 10 <= bench.STEP_TENTHS + 1


def test_benchmark_measures_each_recovery_path(tmp_path: Path) -> None:
    lines: list[str] = []
    results: dict[str, Any] = bench.benchmark(
        tmp_path / "work", [100], runs=1, warmup=0, log=lines.append, deltas=(1, 5)
    )
    assert results["meta"]["runs"] == 1 and results["meta"]["deltas"] == [1, 5]
    entry = results["sizes"]["100"]
    assert entry["build"]["journal_bytes"] > 0 and entry["build"]["checkpoint_bytes"] > 0
    s = entry["scenarios"]
    expected = {
        "full_replay": ("full", "checkpoints_disabled", 100),
        "no_checkpoint": ("full", "no_checkpoint", 100),
        "warm_checkpoint": ("checkpoint", None, 0),
        "checkpoint_plus_1": ("checkpoint", None, 1),
        "checkpoint_plus_5": ("checkpoint", None, 5),
        "fingerprint_mismatch": ("full", "code_fingerprint_mismatch", 100),
    }
    assert {name: (v["mode"], v["reason"], v["replayed"]) for name, v in s.items()} == expected
    # Every path reconstructs the same state, and recovery never adds journal rows.
    assert len({v["state_hash"] for v in s.values()}) == 1
    assert all(v["events"] == 100 and v["journal_rows_changed"] == 0 for v in s.values())
    assert s["full_replay"]["experience_observe_ms"] > 0
    assert s["full_replay"]["restore_ms"] == 0
    assert s["warm_checkpoint"]["restore_ms"] > 0
    assert s["warm_checkpoint"]["experience_observe_ms"] == 0
    assert s["no_checkpoint"]["checkpoint_bytes"] > 0
    assert s["no_checkpoint"]["checkpoint_write_ms"] > 0
    assert s["full_replay"]["checkpoint_bytes"] is None
    assert "| 100 | warm_checkpoint | 0 |" in bench.table(results)


def test_instrumentation_is_removed_after_measurement() -> None:
    original = ExperienceService.observe
    with bench.instrumented() as timers:
        assert ExperienceService.observe is not original
        assert set(timers.ms) == set(bench.COMPONENTS)
    assert ExperienceService.observe is original


def test_workdir_must_be_new_or_empty(tmp_path: Path) -> None:
    (tmp_path / "journal.sqlite").write_bytes(b"not ours")
    with pytest.raises(ValueError, match="workdir_must_be_new_or_empty"):
        bench.benchmark(tmp_path, [100], runs=1, warmup=0, log=lambda _: None)
    assert (tmp_path / "journal.sqlite").read_bytes() == b"not ours"


def test_sizes_must_exceed_the_largest_delta(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="size_must_exceed"):
        bench.build_case(100, tmp_path / "case", bench.example_config(), (1, 100))
