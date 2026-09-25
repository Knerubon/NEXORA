"""The Experience replay benchmark is deterministic and safe (ADR-031 Phase 1B)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import nexora.experience.engine as experience_engine
import nexora.research.runtime as research_runtime
import nexora.storage as storage
import pytest
from nexora.artifacts import canonical_hash
from nexora.experience import ExperienceService
from nexora.experience.models import Experience
from nexora.research import ResearchPipeline
from nexora.storage import SQLiteJournal

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "experience_replay_benchmark.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("experience_replay_benchmark", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = load()

# Pinned definition of the synthetic series: any generator change must be deliberate.
GOLDEN_100 = {
    "calm": "aece8bbb762b6df3888c887acbe82119a36e24fc60b369fcd840cd8517525fef",
    "volatile": "d282d886cb8be4428e7293361b8e98786bf1fc804feb8d9d6b5557bef9798e89",
}


def patched_targets() -> list[Any]:
    owners: list[tuple[Any, str]] = [
        (ExperienceService, "observe"),
        (ExperienceService, "_persist_state"),
        (Experience, "context"),
        (SQLiteJournal, "append"),
        (ResearchPipeline, "replay"),
        (storage, "_verify"),
        (storage, "canonical_hash"),
        (storage, "canonical_serialize"),
        (experience_engine, "plan"),
        (experience_engine, "advance"),
        (experience_engine, "canonical_serialize"),
        (research_runtime, "decode"),
    ]
    return [getattr(owner, name) for owner, name in owners]


@pytest.mark.parametrize("profile", sorted(GOLDEN_100))
def test_profiles_are_deterministic_prefix_stable_and_pinned(profile: str) -> None:
    events = bench.synthetic_events(profile, 300)
    assert events == bench.synthetic_events(profile, 300)
    assert bench.synthetic_events(profile, 120) == events[:120]
    assert canonical_hash(events[:100]) == GOLDEN_100[profile]
    assert events != bench.synthetic_events(profile, 300, seed=1)
    times = [event.event_time for event in events]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert len({event.identity_key for event in events}) == 300
    assert all(e.received_at == e.event_time for e in events)
    step = bench.PROFILES[profile].step_tenths
    moves = [abs(b.price - a.price) * 10 for a, b in zip(events, events[1:], strict=False)]
    # A move is one random step plus the mean-reversion pull (bounded near the anchor).
    assert max(moves) <= step + 3


def test_profiles_differ_and_invalid_input_is_rejected() -> None:
    assert bench.synthetic_events("calm", 50) != bench.synthetic_events("volatile", 50)
    assert bench.PROFILES["calm"].step_tenths < bench.PROFILES["volatile"].step_tenths
    with pytest.raises(ValueError, match="unknown_profile"):
        bench.synthetic_events("wild", 10)
    with pytest.raises(ValueError, match="count_must_be_positive"):
        bench.synthetic_events("calm", 0)
    assert bench.parse_case("volatile:250") == ("volatile", 250)
    for text in ("calm", "calm:0", "calm:x", "wild:10", "calm:-5"):
        with pytest.raises(ValueError, match="invalid_case"):
            bench.parse_case(text)


def test_independent_builds_write_identical_journals(tmp_path: Path) -> None:
    config = bench.example_config()
    digests = []
    for name in ("a", "b"):
        case = bench.Case("calm", 40, bench.SEED, tmp_path / name / "calm-n40")
        build = bench.build_case(case, config)
        manifest = json.loads((case.directory / bench.MANIFEST).read_text(encoding="utf-8"))
        assert manifest["journal"] == build["journal"]
        digests.append((build["journal"], manifest))
    assert digests[0] == digests[1]
    assert digests[0][0]["rows"] > 40  # research rows plus Experience rows


def test_replay_reports_observe_share_and_never_changes_the_journal(tmp_path: Path) -> None:
    before = patched_targets()
    lines: list[str] = []
    results = bench.benchmark(
        tmp_path / "work",
        [("calm", 40), ("volatile", 30)],
        runs=2,
        warmup=1,
        breakdown=True,
        log=lines.append,
    )
    # Every wrapped function is restored, including after the attributed run.
    assert patched_targets() == before
    assert results["meta"]["harness"] == bench.HARNESS
    assert list(results["cases"]) == ["calm:40", "volatile:30"]
    for name, size in (("calm:40", 40), ("volatile:30", 30)):
        entry = results["cases"][name]
        summary = entry["summary"]
        assert summary["runs"] == 2 and len(entry["runs"]) == 2
        assert 0 < summary["median_observe_s"] <= summary["median_total_s"]
        assert 0 < summary["median_observe_share"] <= 1
        for run in entry["runs"]:
            assert run["journal_changed"] is False
            assert run["observe"]["calls"] == size
            assert run["state_hash"] == summary["state_hash"]
        detail = entry["breakdown"]
        assert detail["state_hash"] == summary["state_hash"]
        assert detail["journal_changed"] is False
        categories = detail["breakdown"]["categories"]
        assert set(bench.CATEGORIES) <= set(categories)
        assert all(value["ms"] >= 0 for value in categories.values())
        assert detail["breakdown"]["calls"]["observe"] == size
        assert detail["breakdown"]["calls"]["runtime.decode"] == size
        assert detail["breakdown"]["calls"]["engine.replay"] == size
    # The journal a case built is exactly the journal it still has.
    case_dir = tmp_path / "work" / "calm-n40"
    manifest = json.loads((case_dir / bench.MANIFEST).read_text(encoding="utf-8"))
    assert bench.journal_digest(case_dir / "journal.sqlite") == manifest["journal"]
    assert "| calm:40 |" in bench.table(results)


def test_reuse_opens_only_matching_benchmark_journals(tmp_path: Path) -> None:
    work = tmp_path / "work"
    first = bench.benchmark(work, [("calm", 30)], runs=1, log=lambda _: None)
    again = bench.benchmark(work, [("calm", 30)], runs=1, reuse=True, log=lambda _: None)
    assert again["cases"]["calm:30"]["build"]["reused"] is True
    assert (
        again["cases"]["calm:30"]["summary"]["state_hash"]
        == first["cases"]["calm:30"]["summary"]["state_hash"]
    )

    # A different seed is a different case: its recorded manifest does not match.
    with pytest.raises(bench.BenchmarkRefused, match="reuse_manifest_mismatch"):
        bench.benchmark(work, [("calm", 30)], runs=1, reuse=True, seed=7, log=lambda _: None)

    # Tampered journal content is refused.
    connection = sqlite3.connect(work / "calm-n30" / "journal.sqlite")
    connection.execute("UPDATE research_journal SET content_hash='x' WHERE sequence=1")
    connection.commit()
    connection.close()
    with pytest.raises(bench.BenchmarkRefused, match="reuse_manifest_mismatch"):
        bench.benchmark(work, [("calm", 30)], runs=1, reuse=True, log=lambda _: None)

    # A foreign journal without the harness manifest is never opened.
    foreign = tmp_path / "foreign"
    (foreign / "calm-n30").mkdir(parents=True)
    SQLiteJournal(foreign / "calm-n30" / "journal.sqlite").close()
    with pytest.raises(bench.BenchmarkRefused, match="reuse_requires_benchmark_manifest"):
        bench.benchmark(foreign, [("calm", 30)], runs=1, reuse=True, log=lambda _: None)


def test_workdir_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "journal.sqlite").write_text("not ours", encoding="utf-8")
    with pytest.raises(bench.BenchmarkRefused, match="workdir_must_be_new_or_empty"):
        bench.check_workdir(occupied)
    assert (occupied / "journal.sqlite").read_text(encoding="utf-8") == "not ours"

    a_file = tmp_path / "file"
    a_file.write_text("", encoding="utf-8")
    with pytest.raises(bench.BenchmarkRefused, match="workdir_not_a_directory"):
        bench.check_workdir(a_file, reuse=True)

    with pytest.raises(bench.BenchmarkRefused, match="workdir_overlaps_repository"):
        bench.check_workdir(bench.REPOSITORY / "benchmark-work")

    runtime = tmp_path / "runtime"  # NEXORA_RUNTIME_ROOT, set by conftest
    with pytest.raises(bench.BenchmarkRefused, match="workdir_overlaps_nexora_runtime_root"):
        bench.check_workdir(runtime / "bench")
    with pytest.raises(bench.BenchmarkRefused, match="workdir_overlaps_nexora_runtime_root"):
        bench.check_workdir(tmp_path)  # a parent of the runtime root

    monkeypatch.setenv("NEXORA_JOURNAL_PATH", str(tmp_path / "live" / "journal.sqlite"))
    with pytest.raises(bench.BenchmarkRefused, match="workdir_overlaps_nexora_journal_path"):
        bench.check_workdir(tmp_path / "live")

    monkeypatch.setenv("NEXORA_ENV", "production")
    with pytest.raises(bench.BenchmarkRefused, match="production_environment"):
        bench.check_workdir(tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()

    monkeypatch.setenv("NEXORA_ENV", "development")
    assert bench.check_workdir(tmp_path / "fresh") == (tmp_path / "fresh").resolve()


def test_benchmark_rejects_invalid_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid_runs"):
        bench.benchmark(tmp_path / "w", [("calm", 10)], runs=0, log=lambda _: None)
    with pytest.raises(ValueError, match="no_cases"):
        bench.benchmark(tmp_path / "w", [], log=lambda _: None)
    assert not (tmp_path / "w").exists()
