"""REPLAY-MEM-1 synthetic memory harness for research replay and checkpoints.

Diagnostic only: never imported by production code. It builds synthetic journals in
an explicitly given work directory and never opens an existing runtime journal, so
production and development research databases are out of bounds by construction.

    .venv/bin/python scripts/replay_memory_harness.py build   --workdir W --events 10000
    .venv/bin/python scripts/replay_memory_harness.py replay  --workdir W --repeat 3
    .venv/bin/python scripts/replay_memory_harness.py checkpoint --workdir W
    .venv/bin/python scripts/replay_memory_harness.py engine  --events 100000

`build` ingests a seeded, PROD-like sparse random walk through the live
`ResearchRuntime.ingest` path. `replay` runs full replays (checkpoints off) in one
process and samples process memory, retained structure sizes and (with
`--tracemalloc`) the per-row transient peak. `checkpoint` decomposes checkpoint
write and restore into steps. `engine` measures retained pipeline state for large
event counts without a journal. Product methods are only observed, by wrapping them
in this process; no product behaviour changes.

A guard thread aborts the process (exit 3) when RSS exceeds `--abort-rss-gb` or
available system memory falls below `--min-available-gb`.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import gc
import json
import os
import platform
import random
import sqlite3
import sys
import threading
import time
import tracemalloc
import types
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psutil

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from nexora.artifacts import canonical_hash  # noqa: E402
from nexora.market_data.models import NormalizedPriceEvent  # noqa: E402
from nexora.research import ResearchPipeline, checkpoint_state  # noqa: E402
from nexora.research import checkpoint as ckpt  # noqa: E402
from nexora.research.runtime import ResearchRuntime  # noqa: E402
from nexora.storage import SQLiteJournal  # noqa: E402

from tests.test_recovery_checkpoint import runtime_config  # noqa: E402

GB = 1024**3
MB = 1024**2
SEED = 20260926
# Per-event Gaussian step (USD) and spacing. With the test config (fast box 0.5,
# reversal 2) this gives ~2 structure transitions per 100 events and ~20-45 pending
# Experiences, the same order as the PROD dry-run (94 transitions and 47 pending
# Experiences at 5,000 events). The test zig-zag creates a transition on most events.
SIGMA = 0.09
STEP_SECONDS = 3
ENVIRONMENT = "replay-memory-harness"
JOURNAL_NAME = "journal.sqlite"


# --- Synthetic data ---------------------------------------------------------------------


def synthetic_events(
    count: int, *, seed: int = SEED, sigma: float = SIGMA, step_seconds: int = STEP_SECONDS
) -> Iterator[NormalizedPriceEvent]:
    """Seeded small random walk; most quotes stay inside the current P&F box."""
    rng = random.Random(seed)
    start = datetime(2026, 3, 2, tzinfo=UTC)
    level = 2000.0
    for i in range(1, count + 1):
        level += rng.gauss(0.0, sigma)
        price = Decimal(f"{level:.1f}")
        at = start + timedelta(seconds=i * step_seconds)
        yield NormalizedPriceEvent(
            1,
            f"event:{i}",
            "synthetic",
            "XAUUSD",
            "bar",
            at,
            at,
            i,
            i,
            f"event:{i}",
            "close",
            "USD/oz",
            1,
            price,
            close=price,
        )


# --- Process memory ---------------------------------------------------------------------

_PROCESS = psutil.Process()


def rss() -> int:
    return int(_PROCESS.memory_info().rss)


def private() -> int | None:
    """Windows commit charge (private bytes); None where psutil does not report it."""
    value = getattr(_PROCESS.memory_info(), "private", None)
    return None if value is None else int(value)


def uss() -> int:
    return int(_PROCESS.memory_full_info().uss)


def available() -> int:
    return int(psutil.virtual_memory().available)


def reset_hwm() -> bool:
    """Reset the kernel's peak-RSS mark (Linux clear_refs 5); False when unsupported."""
    try:
        Path("/proc/self/clear_refs").write_text("5")
    except OSError:
        return False
    return True


def hwm() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


_LIBC = ctypes.CDLL(ctypes.util.find_library("c")) if sys.platform == "linux" else None


def trim() -> None:
    """Return free glibc heap pages to the OS, separating retention from allocator slack."""
    gc.collect()
    if _LIBC is not None:
        _LIBC.malloc_trim(0)


@dataclass
class Guard:
    """Sample RSS in the background, track the peak, abort on the configured limits."""

    abort_rss: int
    min_available: int
    interval: float = 0.02
    peak: int = 0
    _stop: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        checks = 0
        while not self._stop.is_set():
            current = rss()
            self.peak = max(self.peak, current)
            checks += 1
            low = checks % 25 == 0 and available() < self.min_available
            if current > self.abort_rss or low:
                reason = "rss_limit" if current > self.abort_rss else "low_available_memory"
                emit({"abort": reason, "rss": current, "available": available()})
                sys.stdout.flush()
                os._exit(3)
            time.sleep(self.interval)

    def reset(self) -> None:
        self.peak = rss()
        reset_hwm()

    def window_peak(self) -> int:
        mark = hwm()
        return max(self.peak, mark or 0)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


# --- Retained structure accounting ------------------------------------------------------

_OPAQUE = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType)


def deep_size(root: Any, seen: set[int]) -> int:
    """Bytes reachable from `root` not already in `seen` (gc.get_referents walk)."""
    total, stack = 0, [root]
    while stack:
        item = stack.pop()
        if id(item) in seen or isinstance(item, _OPAQUE):
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        stack.extend(gc.get_referents(item))
    return total


def structures(runtime: ResearchRuntime) -> dict[str, Any]:
    """Runtime-held state, in attribution order: shared objects count once, first here."""
    pipeline, experience = runtime.engine, runtime.experience
    result: dict[str, Any] = {
        "runtime._events": runtime._events,
        "pipeline._seen": pipeline._seen,
        "pipeline._last": pipeline._last,
    }
    for name, runner in pipeline.matrix.runners.items():
        for state in runner.pnf_engine._states.values():
            result[f"pnf[{name}].seen_identity_keys"] = state.seen_identity_keys
            result[f"pnf[{name}].transitions"] = state.transitions
            result[f"pnf[{name}].columns"] = state.columns
        result[f"runner[{name}]._decisions"] = runner._decisions
    result.update(
        {
            "matrix._latest": pipeline.matrix._latest,
            "structure._transitions": pipeline.structure._transitions,
            "structure._pivots": pipeline.structure._pivots,
            "structure._levels": pipeline.structure._levels,
            "trendline._column_by_transition": pipeline.trendline._column_by_transition,
            "trendline._lows": pipeline.trendline._lows,
            "trendline._highs": pipeline.trendline._highs,
            "trendline._history": pipeline.trendline._history,
            "signals._history": pipeline.signals._history,
            "pattern_engine._state": pipeline.pattern_engine._state,
            "pipeline._output": pipeline._output,
            "experience._seen": experience._seen,
            "experience._last": experience._last,
            "experience._pending": experience._pending,
            "experience._samples": experience._samples,
            "experience._states": experience._states,
            "experience._completed": experience._completed,
            "experience._derived": experience._derived,
        }
    )
    return result


def structure_report(runtime: ResearchRuntime) -> dict[str, Any]:
    seen: set[int] = set()
    sizes: dict[str, dict[str, int]] = {}
    for name, value in structures(runtime).items():
        count = len(value) if hasattr(value, "__len__") else 1
        sizes[name] = {"items": count, "bytes": deep_size(value, seen)}
    return {"total_bytes": sum(v["bytes"] for v in sizes.values()), "structures": sizes}


def state_digest(runtime: ResearchRuntime) -> str:
    """Byte-exact encoded recovered state (ADR-022 parity) plus the output state hash."""
    _, blob_hash = ckpt.encode(
        checkpoint_state.encode_state(
            runtime.engine, runtime._events, runtime.experience.checkpoint_state()
        )
    )
    return blob_hash + ":" + ckpt.state_hash(runtime.engine.snapshot())


def live_instances() -> dict[str, int]:
    """Counts of heavyweight runtime objects still reachable after a run is released."""
    names = {"ResearchRuntime", "ResearchPipeline", "ExperienceService", "PnfEngine"}
    counts = dict.fromkeys(sorted(names), 0)
    for item in gc.get_objects():
        name = type(item).__name__
        if name in names:
            counts[name] += 1
    return counts


# Allocations made by product code (not this harness or the standard library).
_PRODUCT_TRACES = [tracemalloc.Filter(True, str(REPOSITORY / "packages" / "*"))]


# --- Replay probes ----------------------------------------------------------------------


def emit(record: dict[str, Any]) -> None:
    print(json.dumps(record, sort_keys=True, default=str), flush=True)


@dataclass
class Probe:
    """Observe a replay from inside the journal reader without changing what it yields."""

    guard: Guard
    sample_every: int
    deep: bool
    lengths: dict[int, int]
    runtime: ResearchRuntime | None = None
    rows: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    transient: list[dict[str, Any]] = field(default_factory=list)

    def rows_of(
        self, rows: Iterator[tuple[int, dict[str, Any]]]
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        tracing = tracemalloc.is_tracing()
        previous: int | None = None
        before = 0
        if tracing:
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
        for sequence, row in rows:
            # The window closing here covered processing of `previous` and fetching this row.
            if tracing and previous is not None:
                current, peak = tracemalloc.get_traced_memory()
                self.transient.append(
                    {
                        "row": self.rows,
                        "payload_bytes": self.lengths.get(previous, 0),
                        "window_peak_over_start": peak - before,
                        "retained_delta": current - before,
                    }
                )
            self.rows += 1
            if self.rows % self.sample_every == 0:
                self.sample(sequence)
            if tracing:
                tracemalloc.reset_peak()
                before = tracemalloc.get_traced_memory()[0]
            previous = sequence
            yield sequence, row

    def sample(self, sequence: int) -> None:
        record: dict[str, Any] = {
            "rows": self.rows,
            "payload_bytes": self.lengths.get(sequence, 0),
            "rss": rss(),
            "private": private(),
            "peak_rss_so_far": self.guard.window_peak(),
            "available": available(),
        }
        if self.deep and self.runtime is not None:
            report = structure_report(self.runtime)
            record["retained_total_bytes"] = report["total_bytes"]
            record["structures"] = report["structures"]
        self.samples.append(record)
        emit({"sample": record})


class ProbedJournal(SQLiteJournal):
    """SQLiteJournal whose `iter_rows` results pass through a probe."""

    probe: Probe | None

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.probe = None

    def iter_rows(self, stream: str, *, after: int = 0) -> Iterator[tuple[int, dict[str, Any]]]:
        rows = super().iter_rows(stream, after=after)
        return self.probe.rows_of(rows) if self.probe is not None else rows


@contextmanager
def _tracking(probe_of: Callable[[], Probe | None]) -> Iterator[None]:
    """Expose the runtime under construction to the probe (its replay runs in __init__)."""
    original = ResearchRuntime._rebuild

    def rebuild(self: ResearchRuntime) -> None:
        probe = probe_of()
        if probe is not None:
            probe.runtime = self
        original(self)

    setattr(ResearchRuntime, "_rebuild", rebuild)  # noqa: B010
    try:
        yield
    finally:
        setattr(ResearchRuntime, "_rebuild", original)  # noqa: B010


def payload_lengths(path: Path, stream: str) -> dict[int, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            int(sequence): int(size)
            for sequence, size in connection.execute(
                "SELECT sequence, length(payload) FROM research_journal WHERE stream=?",
                (stream,),
            )
        }
    finally:
        connection.close()


# --- Commands ---------------------------------------------------------------------------


def machine() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpus": os.cpu_count(),
        "ram_bytes": int(psutil.virtual_memory().total),
    }


def journal_stats(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        research = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(payload)),0), COALESCE(MAX(length(payload)),0)"
            " FROM research_journal WHERE stream LIKE 'research:%' AND stream NOT LIKE '%:config'"
        ).fetchone()
        other = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(payload)),0), COALESCE(MAX(length(payload)),0)"
            " FROM research_journal WHERE stream NOT LIKE 'research:%'"
        ).fetchone()
    finally:
        connection.close()
    return {
        "research_rows": research[0],
        "research_payload_bytes": research[1],
        "research_max_row_bytes": research[2],
        "other_rows": other[0],
        "other_payload_bytes": other[1],
        "other_max_row_bytes": other[2],
        "file_bytes": path.stat().st_size,
    }


def build(args: argparse.Namespace) -> None:
    workdir: Path = args.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / JOURNAL_NAME
    if path.exists():
        raise SystemExit(f"refusing to overwrite existing journal {path}")
    journal = SQLiteJournal(path)
    runtime = ResearchRuntime(runtime_config(), journal, checkpoints=None)
    started = time.monotonic()
    for index, event in enumerate(synthetic_events(args.events, sigma=args.sigma), 1):
        runtime.ingest(event)
        if index % max(1, args.events // 10) == 0:
            fast = runtime.engine.matrix.runners["fast"].pnf_engine._states["XAUUSD"]
            emit(
                {
                    "build": index,
                    "seconds": round(time.monotonic() - started, 1),
                    "transitions_fast": len(fast.transitions),
                    "columns_fast": len(fast.columns),
                    "pivots": len(runtime.engine.structure._pivots),
                    "signals": len(runtime.engine.signals._history),
                    "pending_experiences": len(runtime.experience._pending),
                    "experiences": len(runtime.experience._states),
                    "rss": rss(),
                }
            )
    journal.close()
    emit({"built": str(path), "machine": machine(), **journal_stats(path)})


def replay(args: argparse.Namespace, guard: Guard) -> None:
    path = args.workdir / JOURNAL_NAME
    if not path.exists():
        raise SystemExit(f"no synthetic journal at {path}; run build first")
    stream = ckpt_stream()
    lengths = payload_lengths(path, stream)
    holder: dict[str, Probe | None] = {"probe": None}
    with _tracking(lambda: holder["probe"]):
        _replay_runs(args, guard, path, lengths, holder)


def _replay_runs(
    args: argparse.Namespace,
    guard: Guard,
    path: Path,
    lengths: dict[int, int],
    holder: dict[str, Probe | None],
) -> None:
    config = runtime_config()
    if args.tracemalloc:
        tracemalloc.start()
    journal = ProbedJournal(path)
    trim()
    baseline = {"rss": rss(), "uss": uss()}
    # Other code in the process (e.g. a test session) may hold its own runtimes.
    live_before = live_instances()
    emit({"replay_start": baseline, "machine": machine(), "events": len(lengths)})
    runs = []
    previous_snapshot: tracemalloc.Snapshot | None = None
    for run in range(1, args.repeat + 1):
        guard.reset()
        probe = Probe(guard, args.sample_every, args.deep, lengths)
        holder["probe"] = journal.probe = probe
        started = time.monotonic()
        runtime = ResearchRuntime(config, journal, checkpoints=None)
        seconds = time.monotonic() - started
        journal.probe = holder["probe"] = None
        probe.runtime = None
        end = {
            "rss": rss(),
            "uss": uss(),
            "private": private(),
            "peak_rss": guard.window_peak(),
        }
        digest = state_digest(runtime) if args.digest else None
        retained = structure_report(runtime) if args.deep else None
        references: list[weakref.ref[Any]] = [
            weakref.ref(runtime),
            weakref.ref(runtime.engine),
            weakref.ref(runtime.experience),
        ]
        del runtime
        gc.collect()
        live = live_instances()
        released = {
            "rss": rss(),
            "uss": uss(),
            "runtime_collected": all(reference() is None for reference in references),
            "live_over_start": {name: live[name] - live_before[name] for name in live},
        }
        trim()
        trimmed = {"rss": rss(), "uss": uss()}
        record: dict[str, Any] = {
            "run": run,
            "seconds": round(seconds, 2),
            "rows": probe.rows,
            "state_digest": digest,
            "end": end,
            "after_release": released,
            "after_trim": trimmed,
            "retained": retained,
        }
        if args.tracemalloc:
            record["tracemalloc_current_after_release"] = tracemalloc.get_traced_memory()[0]
            snapshot = tracemalloc.take_snapshot().filter_traces(_PRODUCT_TRACES)
            if previous_snapshot is not None:
                growth = snapshot.compare_to(previous_snapshot, "lineno")
                record["product_growth_since_previous_run"] = {
                    "total_bytes": sum(stat.size_diff for stat in growth),
                    "top": [
                        {"where": str(stat.traceback), "bytes": stat.size_diff}
                        for stat in growth[:5]
                        if stat.size_diff
                    ],
                }
            previous_snapshot = snapshot
        if probe.transient:
            record["transient"] = transient_summary(probe.transient)
        runs.append(record)
        emit({"run": record})
    journal.close()
    if args.tracemalloc:
        tracemalloc.stop()
    emit({"replay_done": len(runs), "baseline": baseline})


def transient_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    tail = windows[-max(1, len(windows) // 10) :]
    worst = max(windows, key=lambda w: w["window_peak_over_start"])
    ratios = [w["window_peak_over_start"] / w["payload_bytes"] for w in tail if w["payload_bytes"]]
    return {
        "worst": worst,
        "last": windows[-1],
        "tail_mean_ratio_peak_to_row": round(sum(ratios) / len(ratios), 2) if ratios else None,
        "tail_max_ratio_peak_to_row": round(max(ratios), 2) if ratios else None,
    }


def measured(label: str, guard: Guard, step: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    """Run one step; Python allocation figures are reported only while tracemalloc traces."""
    gc.collect()
    tracing = tracemalloc.is_tracing()
    if tracing:
        tracemalloc.reset_peak()
    start_traced = tracemalloc.get_traced_memory()[0]
    guard.reset()
    start_rss = rss()
    started = time.monotonic()
    result = step()
    seconds = time.monotonic() - started
    current, peak = tracemalloc.get_traced_memory()
    record = {
        "step": label,
        "seconds": round(seconds, 3),
        "py_peak_over_start": peak - start_traced if tracing else None,
        "py_retained_delta": current - start_traced if tracing else None,
        "rss_start": start_rss,
        "rss_window_peak": guard.window_peak(),
        "rss_end": rss(),
    }
    emit({"checkpoint_step": record})
    return result, record


def _write_steps(
    guard: Guard, config: Any, journal: SQLiteJournal, store: ckpt.CheckpointStore
) -> int:
    """Checkpoint write, step by step, then the production method whole; returns blob size."""
    # Values live in `held` so each step can release its input exactly when production does.
    held: dict[str, Any] = {}
    held["runtime"], _ = measured(
        "full_replay", guard, lambda: ResearchRuntime(config, journal, checkpoints=None)
    )
    runtime: ResearchRuntime = held["runtime"]
    # Traced only after the replay: tracing a large replay costs several times its runtime.
    tracemalloc.start()
    held["state"], _ = measured(
        "encode_state",
        guard,
        lambda: checkpoint_state.encode_state(
            runtime.engine, runtime._events, runtime.experience.checkpoint_state()
        ),
    )
    emit(composition(held["state"]))
    held["text"], _ = measured(
        "json_dumps",
        guard,
        lambda: json.dumps(
            held["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
    )
    held.pop("text")
    held["encoded"], _ = measured(
        "encode(json.dumps+utf8+sha256)", guard, lambda: ckpt.encode(held.pop("state"))
    )
    blob, blob_hash = held.pop("encoded")
    digest, _ = measured(
        "state_hash(engine.snapshot())",
        guard,
        lambda: ckpt.state_hash(runtime.engine.snapshot()),
    )
    identity = journal.row_identity(runtime.stream, runtime._last_sequence)
    assert identity is not None
    header = ckpt.CheckpointHeader(
        format=ckpt.FORMAT,
        schema_version=ckpt.SCHEMA_VERSION,
        environment=store.environment,
        stream=runtime.stream,
        code_fingerprint=ckpt.code_fingerprint(),
        last_sequence=runtime._last_sequence,
        last_event_key=identity[0],
        last_content_hash=identity[1],
        event_count=len(runtime._events),
        state_hash=digest,
        blob_hash=blob_hash,
        blob_size=len(blob),
        created_at=datetime.now(UTC).isoformat(),
    )
    measured("store.save", guard, lambda: store.save(header, blob))
    # The production path end to end (encode_state -> encode -> state_hash -> save).
    runtime.checkpoints = store
    measured("runtime._write_checkpoint (whole)", guard, runtime._write_checkpoint)
    return len(blob)


def checkpoint(args: argparse.Namespace, guard: Guard) -> None:
    path = args.workdir / JOURNAL_NAME
    if not path.exists():
        raise SystemExit(f"no synthetic journal at {path}; run build first")
    store = ckpt.CheckpointStore(args.workdir / "checkpoints", environment=ENVIRONMENT)
    config = runtime_config()
    journal = SQLiteJournal(path)
    blob_size = _write_steps(guard, config, journal, store)
    held: dict[str, Any] = {}
    trim()
    emit({"after_write_released": {"rss": rss(), "traced": tracemalloc.get_traced_memory()[0]}})

    held["loaded"], _ = measured(
        "store.load(read_bytes+slice)", guard, lambda: store.load(ckpt_stream())
    )
    loaded_header, held["blob"] = held.pop("loaded")
    held["payload"], _ = measured(
        "verify(sha256+json.loads)",
        guard,
        lambda: ckpt.verify(
            loaded_header,
            held.pop("blob"),
            environment=store.environment,
            stream=loaded_header.stream,
            journal=journal,
        ),
    )
    held["restored"], _ = measured(
        "restore_state(decode objects)",
        guard,
        lambda: checkpoint_state.restore_state(held.pop("payload"), config.pipeline),
    )
    measured(
        "state_hash(restored.snapshot())",
        guard,
        lambda: ckpt.state_hash(held["restored"][0].snapshot()),
    )
    held.clear()
    trim()
    recovered, _ = measured(
        "ResearchRuntime restore (whole)",
        guard,
        lambda: ResearchRuntime(config, journal, checkpoints=store),
    )
    emit(
        {
            "checkpoint_bytes": blob_size,
            "restore_mode": recovered.last_recovery.get("mode"),
            "restore_replayed": recovered.last_recovery.get("replayed"),
            "machine": machine(),
        }
    )
    tracemalloc.stop()
    journal.close()


def composition(state: dict[str, Any]) -> dict[str, Any]:
    """Serialized bytes per checkpoint section (compact JSON, as `encode` writes it)."""

    def size(value: Any) -> int:
        return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))

    runners = state["pipeline"]["matrix"]["runners"]
    return {
        "composition": {
            "events": size(state["events"]),
            "experience": {k: size(v) for k, v in state["experience"].items()},
            "pipeline": {k: size(v) for k, v in state["pipeline"].items()},
            "runners": {
                name: {
                    "decisions": size(runner["decisions"]),
                    "sizer_states": size(runner["sizer_states"]),
                    **{f"pnf.{k}": size(v) for k, v in runner["pnf"][0].items() if k != "symbol"},
                }
                for name, runner in runners
            },
        }
    }


def ckpt_stream() -> str:
    return "research:" + str(canonical_hash(runtime_config()))


def engine_only(args: argparse.Namespace, guard: Guard) -> None:
    """Retained pipeline + event growth for large N, without journal rows or Experience."""
    pipeline = ResearchPipeline(runtime_config().pipeline)
    events: list[NormalizedPriceEvent] = []
    trim()
    start = rss()
    emit({"engine_start": {"rss": start}, "machine": machine()})
    started = time.monotonic()
    for index, event in enumerate(synthetic_events(args.events, sigma=args.sigma), 1):
        pipeline.replay(event)
        events.append(event)
        if index % args.sample_every == 0:
            seen: set[int] = set()
            parts = {"events": deep_size(events, seen)}
            parts["pipeline._seen"] = deep_size(pipeline._seen, seen)
            for name, runner in pipeline.matrix.runners.items():
                for state in runner.pnf_engine._states.values():
                    parts[f"pnf[{name}].seen_identity_keys"] = deep_size(
                        state.seen_identity_keys, seen
                    )
                parts[f"runner[{name}]._decisions"] = deep_size(runner._decisions, seen)
            parts["rest_of_pipeline"] = deep_size(pipeline, seen)
            emit(
                {
                    "engine": index,
                    "seconds": round(time.monotonic() - started, 1),
                    "rss": rss(),
                    "rss_over_start": rss() - start,
                    "transitions_fast": len(
                        pipeline.matrix.runners["fast"].pnf_engine._states["XAUUSD"].transitions
                    ),
                    "retained_bytes": sum(parts.values()),
                    "parts": parts,
                }
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--abort-rss-gb", type=float, default=3.5)
    parser.add_argument("--min-available-gb", type=float, default=1.0)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "replay", "checkpoint", "engine"):
        command = commands.add_parser(name)
        if name != "engine":
            command.add_argument("--workdir", type=Path, required=True)
        command.add_argument("--events", type=int, default=1000)
        command.add_argument("--sigma", type=float, default=SIGMA)
        command.add_argument("--sample-every", type=int, default=1000)
        command.add_argument("--repeat", type=int, default=1)
        command.add_argument("--deep", action="store_true", help="retained structure walk")
        command.add_argument("--tracemalloc", action="store_true", help="per-row transient")
        command.add_argument("--digest", action="store_true", help="hash of recovered state")
    args = parser.parse_args(argv)
    guard = Guard(int(args.abort_rss_gb * GB), int(args.min_available_gb * GB))
    try:
        if args.command == "build":
            build(args)
        elif args.command == "replay":
            replay(args, guard)
        elif args.command == "checkpoint":
            checkpoint(args, guard)
        else:
            engine_only(args, guard)
    finally:
        guard.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
