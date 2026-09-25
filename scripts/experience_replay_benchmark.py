"""Deterministic synthetic Experience replay benchmark (ADR-031 Phase 1B, PERF-2).

Builds synthetic SQLite research journals in a new, empty work directory and
measures full ResearchRuntime replay (checkpoints disabled), reporting total
replay time and the ExperienceService.observe contribution. It never opens a
journal it did not build: production, legacy and PostgreSQL journals are out of
bounds by construction.

    .venv/Scripts/python scripts/experience_replay_benchmark.py --workdir <new empty dir> \
        --cases calm:500 calm:1000 volatile:250 --runs 3 --warmup 0 --breakdown \
        --output results.json

Timing wraps existing functions in this process only and restores them afterwards;
no product code changes behavior. Evidence only: no targets are applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import sqlite3
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import nexora.experience.engine as experience_engine
import nexora.experience.service as experience_service
import nexora.research.runtime as research_runtime
import nexora.storage as storage
from nexora.artifacts import canonical_hash, decode
from nexora.experience.models import Experience
from nexora.experience.repository import ExperienceRepository
from nexora.experience.service import ExperienceService
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline, checkpoint_state
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal

REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPOSITORY / "docs" / "examples" / "research-config.json"
SEED = 20260925
MANIFEST = "benchmark-manifest.json"
HARNESS = "experience-replay-benchmark-v1"
# Runtime locations of a real environment (docs/environment-isolation.md). The work
# directory may be neither inside nor a parent of any of them.
RUNTIME_PATH_VARIABLES = (
    "NEXORA_RUNTIME_ROOT",
    "NEXORA_JOURNAL_PATH",
    "NEXORA_STATE_PATH",
    "NEXORA_CHECKPOINT_PATH",
    "NEXORA_CACHE_PATH",
    "NEXORA_LOG_PATH",
)


@dataclass(frozen=True)
class Profile:
    """A seeded, mean-reverting one-minute close series; only the step differs."""

    name: str
    step_tenths: int
    purpose: str


# calm: PERF-1 calibration (ADR-029): recorded output grows ~45-55 B/event, the same
# order as the legacy development journal (ADR-022). volatile: frequent reversals,
# close to ADR-027's measurement profile; a stress case, not a calibration.
PROFILES: dict[str, Profile] = {
    "calm": Profile("calm", 3, "legacy-calibrated output growth"),
    "volatile": Profile("volatile", 20, "stress: frequent reversals and snapshots"),
}


class BenchmarkRefused(ValueError):
    """The harness refuses to run somewhere it could touch non-benchmark data."""


def synthetic_events(profile: str, count: int, *, seed: int = SEED) -> list[NormalizedPriceEvent]:
    if profile not in PROFILES:
        raise ValueError("unknown_profile")
    if count < 1:
        raise ValueError("count_must_be_positive")
    step = PROFILES[profile].step_tenths
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, tzinfo=UTC)
    price = Decimal("2000.0")
    result = []
    for i in range(1, count + 1):
        pull = int((Decimal("2000") - price) / 10)
        price += Decimal(rng.randint(-step, step) + pull) / 10
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


# --- Safety ------------------------------------------------------------------------------


def _related(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def check_workdir(workdir: Path, *, reuse: bool = False) -> Path:
    """Refuse production, runtime locations, the repository and foreign contents."""
    if os.environ.get("NEXORA_ENV", "").strip().lower() == "production":
        raise BenchmarkRefused("production_environment")
    workdir = workdir.resolve()
    for name in RUNTIME_PATH_VARIABLES:
        value = os.environ.get(name)
        if value and _related(workdir, Path(value).resolve()):
            raise BenchmarkRefused(f"workdir_overlaps_{name.lower()}")
    if _related(workdir, REPOSITORY):
        raise BenchmarkRefused("workdir_overlaps_repository")
    if workdir.exists() and not workdir.is_dir():
        raise BenchmarkRefused("workdir_not_a_directory")
    if workdir.exists() and any(workdir.iterdir()) and not reuse:
        raise BenchmarkRefused("workdir_must_be_new_or_empty")
    return workdir


def journal_digest(path: Path) -> dict[str, Any]:
    """Ordered identity of every row; replay must leave it unchanged."""
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        digest = hashlib.sha256()
        rows = 0
        for stream, key, content_hash in connection.execute(
            "SELECT stream,event_key,content_hash FROM research_journal ORDER BY sequence"
        ):
            digest.update(f"{stream}\t{key}\t{content_hash}\n".encode())
            rows += 1
        return {"rows": rows, "sha256": digest.hexdigest()}
    finally:
        connection.close()


def journal_facts(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        kinds: dict[str, dict[str, int]] = {}
        for stream, count, size in connection.execute(
            "SELECT stream, COUNT(*), SUM(length(payload)) FROM research_journal GROUP BY stream"
        ):
            entry = kinds.setdefault(stream_kind(stream), {"streams": 0, "rows": 0, "bytes": 0})
            entry["streams"] += 1
            entry["rows"] += count
            entry["bytes"] += size or 0
        return {"streams_by_kind": kinds, "db_bytes": path.stat().st_size}
    finally:
        connection.close()


def stream_kind(stream: str) -> str:
    if stream == "experience:v1:snapshots":
        return "snapshot"
    for suffix in ("observations", "lifecycle", "outcomes"):
        if stream.startswith("experience:v1:") and stream.endswith(":" + suffix):
            return suffix
    if stream.startswith("research:"):
        return "research_config" if stream.endswith(":config") else "research"
    return "other"


# --- Timing ------------------------------------------------------------------------------


def latency(values: list[float]) -> dict[str, Any]:
    ms = sorted(v * 1000 for v in values)
    if not ms:
        return {"calls": 0}
    tenth = max(1, len(values) // 10)
    ordered = [v * 1000 for v in values]
    return {
        "calls": len(ms),
        "total_ms": round(sum(ms), 1),
        "mean_ms": round(statistics.fmean(ms), 3),
        "median_ms": round(statistics.median(ms), 3),
        "p95_ms": round(ms[min(len(ms) - 1, int(0.95 * len(ms)))], 3),
        "max_ms": round(ms[-1], 3),
        # Mean per decile of replay position: growth with accumulated history.
        "decile_mean_ms": [
            round(statistics.fmean(ordered[i : i + tenth]), 3)
            for i in range(0, min(len(ordered), tenth * 10), tenth)
        ],
    }


@contextmanager
def patched(owner: type | ModuleType, name: str, replacement: Any) -> Iterator[None]:
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


@contextmanager
def observe_timer() -> Iterator[list[float]]:
    """Per-call wall time of ExperienceService.observe, restored on exit."""
    calls: list[float] = []
    original = ExperienceService.observe

    def observe(self: ExperienceService, *args: Any, **kwargs: Any) -> None:
        started = time.perf_counter()
        try:
            original(self, *args, **kwargs)
        finally:
            calls.append(time.perf_counter() - started)

    with patched(ExperienceService, "observe", observe):
        yield calls


class Breakdown:
    """Exclusive time per wrapped function: a parent's time excludes wrapped children."""

    def __init__(self) -> None:
        self._stack: list[list[float]] = []
        self.stats: dict[str, list[float]] = {}

    def enter(self) -> None:
        self._stack.append([0.0])

    def leave(self, key: str, duration: float) -> None:
        (child,) = self._stack.pop()
        entry = self.stats.setdefault(key, [0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += duration
        entry[2] += duration - child
        if self._stack:
            self._stack[-1][0] += duration

    def wrap(self, function: Callable[..., Any], key: str) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.enter()
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.leave(key, time.perf_counter() - started)

        return wrapper


class _TimedConnection:
    """Times SQL statements and commits of one benchmark-owned journal connection."""

    def __init__(self, connection: sqlite3.Connection, breakdown: Breakdown) -> None:
        self._connection, self._breakdown, self.kind = connection, breakdown, "read"

    def _timed(self, key: str, function: Callable[[], Any]) -> Any:
        self._breakdown.enter()
        started = time.perf_counter()
        try:
            return function()
        finally:
            self._breakdown.leave(key, time.perf_counter() - started)

    def execute(self, sql: str, *args: Any) -> Any:
        return self._timed(f"sql:{self.kind}", lambda: self._connection.execute(sql, *args))

    def __enter__(self) -> Any:
        return self._connection.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._timed(f"commit:{self.kind}", lambda: self._connection.__exit__(*exc))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


# Exclusive-time keys -> reported category (ADR-031 section 6.2).
CATEGORIES: dict[str, tuple[str, ...]] = {
    "experience_freeze_serialize_hash": (
        "freeze",
        "freeze.canonical_serialize",
        "freeze.canonical_hash",
        "freeze.frozen_json",
        "freeze.fingerprint",
        # ADR-031 Phase 2 names for the same work.
        "freeze.recorded",
        "observe.recorded",
        "observe.fingerprint",
        "materialize",
    ),
    "experience_context_reparse": ("Experience.context", "plan", "plan_of"),
    "experience_digest": ("observe.digest",),
    "experience_lifecycle_measure": ("observe.advance", "measure", "measure.advance"),
    "experience_append_encode": (
        "append.canonical_serialize",
        "append.canonical_hash",
        "append:experience",
        "persist_state",
        "repository.save",
    ),
    "experience_sql": ("sql:experience",),
    "experience_commit": ("commit:experience",),
    "experience_observe_residual": ("observe",),
    "journal_read_verify": ("journal.verify", "sql:read", "commit:read"),
    "runtime_decode_event": ("runtime.decode",),
    "engine_replay": ("engine.replay",),
    "research_config_append": ("append:research", "sql:research", "commit:research"),
}


@contextmanager
def breakdown_instrumentation(journal: SQLiteJournal) -> Iterator[Breakdown]:
    """Wrap Experience internals, journal access and runtime steps; restore on exit."""
    b = Breakdown()
    timed = _TimedConnection(journal.connection, b)
    original_append = SQLiteJournal.append

    def append(self: SQLiteJournal, stream: str, key: str, payload: Any, **kw: Any) -> bool:
        kind = "experience" if stream.startswith("experience:") else "research"
        previous, timed.kind = timed.kind, kind
        b.enter()
        started = time.perf_counter()
        try:
            return original_append(self, stream, key, payload, **kw)
        finally:
            b.leave(f"append:{kind}", time.perf_counter() - started)
            timed.kind = previous

    # (owner, attribute, key): module globals are patched where the caller looks them up.
    wrapped: list[tuple[type | ModuleType, str, str]] = [
        (storage, "canonical_serialize", "append.canonical_serialize"),
        (storage, "canonical_hash", "append.canonical_hash"),
        (storage, "_verify", "journal.verify"),
        (experience_engine, "canonical_serialize", "freeze.canonical_serialize"),
        (experience_engine, "canonical_hash", "freeze.canonical_hash"),
        (experience_engine, "frozen_json", "freeze.frozen_json"),
        (experience_engine, "fingerprint", "freeze.fingerprint"),
        (experience_engine, "plan", "plan"),
        (experience_engine, "advance", "measure.advance"),
        (Experience, "context", "Experience.context"),
        (experience_service, "freeze", "freeze"),
        (experience_service, "canonical_hash", "observe.digest"),
        # ADR-031 Phase 2: present only in the optimized implementation.
        (experience_engine, "recorded", "freeze.recorded"),
        (experience_service, "recorded", "observe.recorded"),
        (experience_service, "fingerprint", "observe.fingerprint"),
        (experience_service, "materialize", "materialize"),
        (experience_service, "observation_digest", "observe.digest"),
        (experience_service, "plan_of", "plan_of"),
        (experience_service, "advance", "observe.advance"),
        (experience_service, "measure", "measure"),
        (ExperienceService, "_persist_state", "persist_state"),
        (ExperienceRepository, "save", "repository.save"),
        (ExperienceService, "observe", "observe"),
        (ResearchPipeline, "replay", "engine.replay"),
        (research_runtime, "decode", "runtime.decode"),
    ]
    targets: list[tuple[type | ModuleType, str, Any]] = [(SQLiteJournal, "append", append)]
    # Wrap what the loaded implementation has, so one harness measures before and after.
    targets += [
        (owner, name, b.wrap(getattr(owner, name), key))
        for owner, name, key in wrapped
        if hasattr(owner, name)
    ]
    originals = [(owner, name, getattr(owner, name)) for owner, name, _ in targets]
    connection = journal.connection
    try:
        journal.connection = timed  # type: ignore[assignment]
        for owner, name, replacement in targets:
            setattr(owner, name, replacement)
        yield b
    finally:
        journal.connection = connection
        for owner, name, original in reversed(originals):
            setattr(owner, name, original)


def categorize(b: Breakdown, total_s: float) -> dict[str, Any]:
    lookup = {key: category for category, keys in CATEGORIES.items() for key in keys}
    totals: dict[str, float] = dict.fromkeys(CATEGORIES, 0.0)
    for key, (_, _, exclusive) in b.stats.items():
        category = lookup.get(key, "other")
        totals[category] = totals.get(category, 0.0) + exclusive
    attributed = sum(totals.values())
    return {
        "categories": {
            name: {"ms": round(value * 1000, 1), "share": round(value / total_s, 4)}
            for name, value in sorted(totals.items(), key=lambda kv: -kv[1])
        },
        "unattributed_ms": round((total_s - attributed) * 1000, 1),
        "calls": {key: int(v[0]) for key, v in sorted(b.stats.items())},
    }


# --- Cases -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    profile: str
    size: int
    seed: int
    directory: Path

    @property
    def journal_path(self) -> Path:
        return self.directory / "journal.sqlite"

    def manifest(self, events_sha256: str, journal: dict[str, Any]) -> dict[str, Any]:
        return {
            "harness": HARNESS,
            "profile": asdict(PROFILES[self.profile]),
            "size": self.size,
            "seed": self.seed,
            "events_sha256": events_sha256,
            "journal": journal,
        }


def parse_case(text: str) -> tuple[str, int]:
    profile, _, size = text.partition(":")
    if profile not in PROFILES or not size.isdigit() or int(size) < 1:
        raise ValueError(f"invalid_case:{text}")
    return profile, int(size)


def build_case(case: Case, config: RuntimeConfig) -> dict[str, Any]:
    """Create the journal by live ingest, recording live observe cost as well."""
    events = synthetic_events(case.profile, case.size, seed=case.seed)
    case.directory.mkdir(parents=True)
    journal = SQLiteJournal(case.journal_path)
    try:
        runtime = ResearchRuntime(config, journal)
        ingest: list[float] = []
        with observe_timer() as live:
            started = time.perf_counter()
            for event in events:
                began = time.perf_counter()
                runtime.ingest(event)
                ingest.append(time.perf_counter() - began)
            total = time.perf_counter() - started
    finally:
        journal.close()
    digest = journal_digest(case.journal_path)
    manifest = case.manifest(canonical_hash(events), digest)
    (case.directory / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {
        "reused": False,
        "total_s": round(total, 3),
        "live_ingest": latency(ingest),
        "live_observe": latency(live),
        "live_observe_share": round(sum(live) / sum(ingest), 4),
        "journal": digest,
        "facts": journal_facts(case.journal_path),
    }


def reuse_case(case: Case) -> dict[str, Any]:
    """Only a journal this harness built, for exactly this case, is opened again."""
    path = case.directory / MANIFEST
    if not path.is_file() or not case.journal_path.is_file():
        raise BenchmarkRefused("reuse_requires_benchmark_manifest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    events = synthetic_events(case.profile, case.size, seed=case.seed)
    expected = case.manifest(canonical_hash(events), manifest.get("journal"))
    if manifest != expected or journal_digest(case.journal_path) != manifest["journal"]:
        raise BenchmarkRefused("reuse_manifest_mismatch")
    return {
        "reused": True,
        "journal": manifest["journal"],
        "facts": journal_facts(case.journal_path),
    }


def replay_once(case: Case, config: RuntimeConfig, *, breakdown: bool) -> dict[str, Any]:
    """One full replay (checkpoints disabled) of the case journal."""
    before = journal_digest(case.journal_path)
    journal = SQLiteJournal(case.journal_path)
    try:
        with observe_timer() as observe:
            if breakdown:
                with breakdown_instrumentation(journal) as b:
                    started = time.perf_counter()
                    runtime = ResearchRuntime(config, journal)
                    total = time.perf_counter() - started
            else:
                started = time.perf_counter()
                runtime = ResearchRuntime(config, journal)
                total = time.perf_counter() - started
        recovery = runtime.last_recovery
        if recovery.get("mode") != "full" or recovery.get("replayed") != case.size:
            raise RuntimeError("unexpected_recovery_path")
        state = canonical_hash(
            checkpoint_state.encode_state(
                runtime.engine, list(runtime.events()), runtime.experience.checkpoint_state()
            )
        )
    finally:
        journal.close()
    after = journal_digest(case.journal_path)
    result: dict[str, Any] = {
        "total_s": round(total, 3),
        "observe_s": round(sum(observe), 3),
        "observe_share": round(sum(observe) / total, 4),
        "observe": latency(observe),
        "state_hash": state,
        "journal_changed": before != after,
    }
    if breakdown:
        result["breakdown"] = categorize(b, total)
    return result


def summarize(runs: list[dict[str, Any]], size: int) -> dict[str, Any]:
    if len({r["state_hash"] for r in runs}) != 1:
        raise RuntimeError("nondeterministic_replay_state")
    if any(r["journal_changed"] for r in runs):
        raise RuntimeError("replay_changed_journal")
    totals = [r["total_s"] for r in runs]
    shares = [r["observe_share"] for r in runs]
    return {
        "runs": len(runs),
        "total_s": totals,
        "median_total_s": round(statistics.median(totals), 3),
        "median_ms_per_event": round(statistics.median(totals) * 1000 / size, 3),
        "median_observe_s": round(statistics.median(r["observe_s"] for r in runs), 3),
        "median_observe_share": round(statistics.median(shares), 4),
        "state_hash": runs[0]["state_hash"],
    }


def benchmark(
    workdir: Path,
    cases: list[tuple[str, int]],
    *,
    runs: int = 3,
    warmup: int = 0,
    breakdown: bool = False,
    seed: int = SEED,
    reuse: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    if runs < 1 or warmup < 0:
        raise ValueError("invalid_runs")
    if not cases:
        raise ValueError("no_cases")
    workdir = check_workdir(workdir, reuse=reuse)
    workdir.mkdir(parents=True, exist_ok=True)
    config = example_config()
    results: dict[str, Any] = {"meta": metadata(runs, warmup, breakdown, seed), "cases": {}}
    for profile, size in cases:
        case = Case(profile, size, seed, workdir / f"{profile}-n{size}")
        name = f"{profile}:{size}"
        if reuse and case.directory.exists():
            log(f"reuse {name}")
            build = reuse_case(case)
        else:
            log(f"build {name}")
            build = build_case(case, config)
        for i in range(warmup):
            log(f"warmup {name} {i + 1}/{warmup}")
            replay_once(case, config, breakdown=False)
        measured = []
        for i in range(runs):
            log(f"replay {name} {i + 1}/{runs}")
            measured.append(replay_once(case, config, breakdown=False))
        entry: dict[str, Any] = {
            "build": build,
            "runs": measured,
            "summary": summarize(measured, size),
        }
        if breakdown:
            log(f"breakdown {name}")
            detail = replay_once(case, config, breakdown=True)
            if detail["state_hash"] != entry["summary"]["state_hash"]:
                raise RuntimeError("nondeterministic_replay_state")
            entry["breakdown"] = detail
        results["cases"][name] = entry
    return results


def metadata(runs: int, warmup: int, breakdown: bool, seed: int) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {
        "harness": HARNESS,
        "commit": commit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "runs": runs,
        "warmup": warmup,
        "breakdown": breakdown,
        "seed": seed,
        "profiles": {name: asdict(p) for name, p in PROFILES.items()},
        "config": str(EXAMPLE_CONFIG.relative_to(REPOSITORY)),
        "journal_backend": "sqlite (synthetic, benchmark-owned)",
        "created_at": datetime.now(UTC).isoformat(),
    }


def table(results: dict[str, Any]) -> str:
    lines = [
        "| case | median replay s | ms/event | observe s | observe share | runs |",
        "|---|---|---|---|---|---|",
    ]
    for name, entry in results["cases"].items():
        s = entry["summary"]
        lines.append(
            f"| {name} | {s['median_total_s']} | {s['median_ms_per_event']} | "
            f"{s['median_observe_s']} | {s['median_observe_share']:.1%} | {s['runs']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", required=True, help="profile:N, e.g. calm:500")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--breakdown", action="store_true", help="one extra attributed replay")
    parser.add_argument("--reuse", action="store_true", help="reuse journals this harness built")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        cases = [parse_case(text) for text in args.cases]
        results = benchmark(
            args.workdir,
            cases,
            runs=args.runs,
            warmup=args.warmup,
            breakdown=args.breakdown,
            seed=args.seed,
            reuse=args.reuse,
        )
    except (BenchmarkRefused, ValueError) as error:
        raise SystemExit(f"refused: {error}") from None
    if args.output:
        args.output.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(table(results))


if __name__ == "__main__":
    main()
