"""Deterministic synthetic research recovery benchmark (ADR-029 Phase 2A).

Builds synthetic journals in a new, empty work directory and measures startup
recovery paths against them. It never opens an existing journal: production
and legacy research databases are out of bounds by construction.

    .venv/Scripts/python scripts/recovery_benchmark.py --workdir <new empty dir> \
        --sizes 500 1000 2000 5000 --runs 3 --warmup 1 --output results.json

Recovery components are timed by wrapping the existing methods in this process
only; no product code changes behavior. Evidence only: no targets are applied.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from nexora.artifacts import canonical_hash, decode
from nexora.experience import ExperienceService
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.research.checkpoint import CheckpointStore
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal

REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPOSITORY / "docs" / "examples" / "research-config.json"
ENVIRONMENT = "benchmark"
SEED = 20260925
# Largest one-event move in tenths. Calibrated so per-row growth of the recorded
# cumulative output (~55 B/event at 1,000 events with the example config) is the same
# order as the legacy development journal's average (~45 B/event, ADR-022). Larger
# steps create reversals every few events and an unrepresentative O(n^2) blow-up.
STEP_TENTHS = 3
DELTAS = (1, 10, 100)


def scenarios(deltas: tuple[int, ...] = DELTAS) -> tuple[str, ...]:
    return (
        "full_replay",
        "no_checkpoint",
        "warm_checkpoint",
        *(f"checkpoint_plus_{k}" for k in deltas),
        "fingerprint_mismatch",
    )


COMPONENTS = ("restore", "experience_observe", "engine_replay", "paper", "checkpoint_write")


def synthetic_events(count: int, *, seed: int = SEED) -> list[NormalizedPriceEvent]:
    """A seeded, mean-reverting one-minute close series with frequent reversals."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, tzinfo=UTC)
    price = Decimal("2000.0")
    result = []
    for i in range(1, count + 1):
        pull = int((Decimal("2000") - price) / 10)
        price += Decimal(rng.randint(-STEP_TENTHS, STEP_TENTHS) + pull) / 10
        at = start + timedelta(minutes=i)
        result.append(
            NormalizedPriceEvent(
                1,
                f"bench:{i}",
                "synthetic-benchmark",
                "XAUUSD",
                "bar",
                at,
                at,
                i,
                i,
                f"bench:{i}",
                "close",
                "USD/oz",
                1,
                price,
                close=price,
            )
        )
    return result


def example_config() -> RuntimeConfig:
    return decode(RuntimeConfig, json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8")))


class Timers:
    def __init__(self) -> None:
        self.ms: dict[str, float] = dict.fromkeys(COMPONENTS, 0.0)


@contextmanager
def instrumented() -> Iterator[Timers]:
    """Time recovery components by wrapping them; restored on exit."""
    timers = Timers()
    targets: list[tuple[type, str, str]] = [
        (ResearchRuntime, "_restore", "restore"),
        (ExperienceService, "observe", "experience_observe"),
        (ResearchPipeline, "replay", "engine_replay"),
        (ResearchRuntime, "_paper_event", "paper"),
        (ResearchRuntime, "_write_checkpoint", "checkpoint_write"),
    ]
    originals = [(owner, name, getattr(owner, name)) for owner, name, _ in targets]

    def timed(original: Callable[..., Any], key: str) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                timers.ms[key] += (time.perf_counter() - started) * 1000

        return wrapper

    for (owner, name, key), (_, _, original) in zip(targets, originals, strict=True):
        setattr(owner, name, timed(original, key))
    try:
        yield timers
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)


@dataclass
class Case:
    size: int
    directory: Path
    config: RuntimeConfig
    stream: str = ""
    build: dict[str, Any] = field(default_factory=dict)

    @property
    def journal_path(self) -> Path:
        return self.directory / "journal.sqlite"

    @property
    def store(self) -> CheckpointStore:
        return CheckpointStore(self.directory / "checkpoints", environment=ENVIRONMENT)

    def saved(self, label: str) -> Path:
        return self.directory / "saved" / f"{label}.checkpoint"


def build_case(
    size: int, directory: Path, config: RuntimeConfig, deltas: tuple[int, ...] = DELTAS
) -> Case:
    """Ingest `size` events live; keep checkpoints at size-k for every delta and at size."""
    if size <= max(deltas) or size < 100:
        raise ValueError("size_must_exceed_largest_delta_and_100")
    case = Case(size, directory, config)
    (directory / "saved").mkdir(parents=True)
    journal = SQLiteJournal(case.journal_path)
    try:
        runtime = ResearchRuntime(config, journal, checkpoints=case.store, checkpoint_interval=0)
        case.stream = runtime.stream
        marks = {size - k: f"at_minus_{k}" for k in deltas} | {size: "at_size"}
        started = time.perf_counter()
        tail_started = 0.0
        for index, event in enumerate(synthetic_events(size), start=1):
            if index == size - 99:
                tail_started = time.perf_counter()
            runtime.ingest(event, completeness="complete")
            if index in marks:
                runtime.checkpoint()
                shutil.copyfile(case.store.path_for(case.stream), case.saved(marks[index]))
        finished = time.perf_counter()
        case.build = {
            "ingest_ms": round((finished - started) * 1000),
            "last_100_ingest_mean_ms": round((finished - tail_started) * 1000 / 100, 2),
            "journal_bytes": case.journal_path.stat().st_size,
            "checkpoint_bytes": case.saved("at_size").stat().st_size,
        }
    finally:
        journal.close()
    return case


def install(case: Case, scenario: str) -> CheckpointStore | None:
    """Put exactly the checkpoint a scenario needs in place; nothing else survives."""
    if scenario == "full_replay":
        return None
    directory = case.store.directory
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    target = case.store.path_for(case.stream)
    if scenario == "no_checkpoint":
        return case.store
    if scenario.startswith("checkpoint_plus_"):
        shutil.copyfile(case.saved(f"at_minus_{scenario.rsplit('_', 1)[1]}"), target)
        return case.store
    shutil.copyfile(case.saved("at_size"), target)
    if scenario == "fingerprint_mismatch":
        # Equivalent to any code change: the header no longer matches this build.
        raw = target.read_bytes()
        end = raw.index(b"\n")
        header = json.loads(raw[:end])
        header["code_fingerprint"] = "0" * 64
        target.write_bytes(json.dumps(header, sort_keys=True).encode() + b"\n" + raw[end + 1 :])
    return case.store


def journal_rows(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM research_journal").fetchone()[0])
    finally:
        connection.close()


def run_scenario(case: Case, scenario: str) -> dict[str, Any]:
    store = install(case, scenario)
    rows_before = journal_rows(case.journal_path)
    journal = SQLiteJournal(case.journal_path)
    gc.collect()
    try:
        with instrumented() as timers:
            started = time.perf_counter()
            runtime = ResearchRuntime(
                case.config, journal, checkpoints=store, checkpoint_interval=0
            )
            startup_ms = (time.perf_counter() - started) * 1000
        status = runtime.recovery_status()
        result = {
            "mode": status["mode"],
            "reason": status["reason"],
            "replayed": status["replayed"],
            "events": len(runtime.events()),
            "startup_ms": startup_ms,
            "recovery_ms": float(status["duration_ms"]),
            **{f"{key}_ms": value for key, value in timers.ms.items()},
            "checkpoint_bytes": (status["last_checkpoint"] or {}).get("bytes"),
            "state_hash": canonical_hash(runtime.snapshot()),
        }
    finally:
        journal.close()
    result["journal_rows_changed"] = journal_rows(case.journal_path) - rows_before
    return result


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = [
        "startup_ms",
        "recovery_ms",
        *(f"{key}_ms" for key in COMPONENTS),
    ]
    summary: dict[str, Any] = {
        key: round(statistics.median(run[key] for run in runs), 1) for key in numeric
    }
    for key in ("mode", "reason", "replayed", "events", "checkpoint_bytes", "state_hash"):
        values = {json.dumps(run[key]) for run in runs}
        if len(values) != 1:
            raise RuntimeError(f"nondeterministic_{key}")
        summary[key] = runs[0][key]
    summary["experience_share_of_recovery"] = (
        round(summary["experience_observe_ms"] / summary["recovery_ms"], 3)
        if summary["recovery_ms"]
        else None
    )
    summary["runs"] = [round(run["recovery_ms"], 1) for run in runs]
    summary["journal_rows_changed"] = max(run["journal_rows_changed"] for run in runs)
    return summary


def benchmark(
    workdir: Path,
    sizes: list[int],
    *,
    runs: int,
    warmup: int,
    log: Callable[[str], None],
    deltas: tuple[int, ...] = DELTAS,
) -> dict[str, Any]:
    if workdir.exists() and any(workdir.iterdir()):
        raise ValueError("workdir_must_be_new_or_empty")
    workdir.mkdir(parents=True, exist_ok=True)
    config = example_config()
    results: dict[str, Any] = {"meta": metadata(runs, warmup, deltas), "sizes": {}}
    for size in sizes:
        log(f"N={size}: building synthetic journal")
        case = build_case(size, workdir / f"n{size}", config, deltas)
        measured: dict[str, list[dict[str, Any]]] = {name: [] for name in scenarios(deltas)}
        # Round-robin keeps slow machine drift from favouring one scenario.
        for iteration in range(warmup + runs):
            for scenario in scenarios(deltas):
                result = run_scenario(case, scenario)
                log(
                    f"N={size} {'warmup' if iteration < warmup else 'run'} "
                    f"{scenario}: {result['recovery_ms']:.0f} ms replayed={result['replayed']}"
                )
                if iteration >= warmup:
                    measured[scenario].append(result)
        summaries = {name: summarize(values) for name, values in measured.items()}
        if len({summary["state_hash"] for summary in summaries.values()}) != 1:
            raise RuntimeError("checkpoint_state_differs_from_full_replay")
        results["sizes"][str(size)] = {"build": case.build, "scenarios": summaries}
    return results


def metadata(runs: int, warmup: int, deltas: tuple[int, ...]) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=REPOSITORY,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True
    return {
        "commit": commit,
        "worktree_dirty": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "config": "docs/examples/research-config.json",
        "seed": SEED,
        "deltas": list(deltas),
        "runs": runs,
        "warmup": warmup,
        "statistic": "median",
        "timer": "time.perf_counter",
        "started_at": datetime.now(UTC).isoformat(),
    }


def table(results: dict[str, Any]) -> str:
    lines = [
        "| N | scenario | replayed | recovery ms | Experience.observe ms (share) "
        "| engine ms | restore ms | checkpoint write ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for size, entry in results["sizes"].items():
        for name, s in entry["scenarios"].items():
            share = s["experience_share_of_recovery"]
            lines.append(
                f"| {size} | {name} | {s['replayed']} | {s['recovery_ms']:,.0f} "
                f"| {s['experience_observe_ms']:,.0f} ({'-' if share is None else f'{share:.0%}'}) "
                f"| {s['engine_replay_ms']:,.0f} | {s['restore_ms']:,.0f} "
                f"| {s['checkpoint_write_ms']:,.0f} |"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic research recovery benchmark.")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[500, 1000, 2000, 5000])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--deltas", type=int, nargs="+", default=list(DELTAS))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.warmup < 0:
        parser.error("runs must be >= 1 and warmup >= 0")
    results = benchmark(
        args.workdir.resolve(),
        args.sizes,
        runs=args.runs,
        warmup=args.warmup,
        log=lambda line: print(line, file=sys.stderr, flush=True),
        deltas=tuple(args.deltas),
    )
    if args.output:
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(table(results))


if __name__ == "__main__":
    main()
