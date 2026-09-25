"""Optimized Experience observe is observably identical to the ec0aad5 baseline (ADR-031).

Every scenario runs twice, once with the verbatim baseline service
(`tests/experience_baseline`) and once with the optimized one, through the real
ResearchRuntime, and compares journal rows (sequence, stream, key, content hash,
payload), checkpoint state bytes after every prefix, replay results and failure
behavior. The mutation tests at the end prove that these comparisons catch the
kinds of bugs the optimization could introduce.

Live state is compared with live state and replayed state with replayed state. The
checkpoint blob of a live runtime differs from a replayed one even in the baseline:
live events keep their Decimal exponent (`2000.0`) while journal-decoded events do
not (`2000`). Values and canonical hashes are equal; that is not an Experience
behavior and is outside ADR-031.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import nexora.experience.engine as optimized_engine
import nexora.experience.service as optimized_service
import nexora.research.runtime as research_runtime
import pytest
from nexora.artifacts import canonical_hash, decode
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import checkpoint as ckpt
from nexora.research import checkpoint_state
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal

import tests.experience_baseline.engine as baseline_engine
import tests.experience_baseline.service as baseline_service
from tests.experience_compat_fixture import Row, dump_rows, load_fixture, load_rows

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "experience_replay_benchmark.py"
IMPLEMENTATIONS = ("baseline", "optimized")
SERVICES: dict[str, Any] = {
    "baseline": baseline_service.ExperienceService,
    "optimized": optimized_service.ExperienceService,
}


def _load_benchmark() -> ModuleType:
    spec = importlib.util.spec_from_file_location("experience_replay_benchmark_eq", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load_benchmark()
CONFIG: RuntimeConfig = bench.example_config()
# Calm is WAIT-only; volatile has BUY/SELL plans, entries, invalidations and expiries.
SCENARIOS = {"calm": ("calm", 160), "volatile": ("volatile", 140)}


@contextmanager
def implementation(name: str) -> Iterator[None]:
    original = getattr(research_runtime, "ExperienceService")  # noqa: B009
    setattr(research_runtime, "ExperienceService", SERVICES[name])  # noqa: B010
    try:
        yield
    finally:
        setattr(research_runtime, "ExperienceService", original)  # noqa: B010


def state_blob_hash(runtime: ResearchRuntime) -> str:
    """Hash of the exact checkpoint blob bytes the runtime would write now."""
    state = checkpoint_state.encode_state(
        runtime.engine, list(runtime.events()), runtime.experience.checkpoint_state()
    )
    _, blob_hash = ckpt.encode(state)
    return str(blob_hash)


class FailOnce(SQLiteJournal):
    """Raises once, before writing, at the n-th Experience append."""

    def __init__(self, path: Path, fail_at: int | None) -> None:
        super().__init__(path)
        self.fail_at, self.count = fail_at, 0

    def append(self, stream: str, key: str, payload: Any, **kwargs: Any) -> bool:
        if stream.startswith("experience:"):
            self.count += 1
            if self.count == self.fail_at:
                raise OSError("injected_experience_append_failure")
        return super().append(stream, key, payload, **kwargs)


def run_live(
    name: str,
    events: list[NormalizedPriceEvent],
    path: Path,
    *,
    config: RuntimeConfig = CONFIG,
    fail_at: int | None = None,
) -> tuple[list[Row], list[str]]:
    """Live ingest; returns journal rows and the checkpoint-state hash after every event."""
    journal = FailOnce(path, fail_at)
    prefixes = []
    try:
        with implementation(name):
            runtime = ResearchRuntime(config, journal)
            for event in events:
                try:
                    runtime.ingest(event, completeness="complete")
                except OSError:
                    # ingest rebuilt from the journal before re-raising; the research row
                    # is committed, so the event is not ingested again (it would be a no-op).
                    pass
                prefixes.append(state_blob_hash(runtime))
    finally:
        journal.close()
    return dump_rows(path), prefixes


def run_replay(name: str, path: Path, config: RuntimeConfig = CONFIG) -> tuple[str, list[Row]]:
    journal = SQLiteJournal(path)
    try:
        with implementation(name):
            runtime = ResearchRuntime(config, journal)
            assert runtime.last_recovery["mode"] == "full"
            result = state_blob_hash(runtime)
    finally:
        journal.close()
    return result, dump_rows(path)


def recovered_state(runtime: ResearchRuntime) -> dict[str, Any]:
    """Canonical observable state, compared exactly as the recovery suite does (`state()`)."""
    from tests.test_recovery_checkpoint import state

    return state(runtime)


def experience_rows(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r[1].startswith("experience:")]


def scenario_events(scenario: str) -> list[NormalizedPriceEvent]:
    profile, size = SCENARIOS[scenario]
    return list(bench.synthetic_events(profile, size))


# --- End-to-end equivalence ----------------------------------------------------------------


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_live_writes_and_every_prefix_state_are_identical(tmp_path: Path, scenario: str) -> None:
    """Rows, write order, IDs, fingerprints, contexts, outcomes, lifecycle, checkpoint state."""
    events = scenario_events(scenario)
    results = {n: run_live(n, events, tmp_path / f"{n}.sqlite") for n in IMPLEMENTATIONS}
    (base_rows, base_states), (opt_rows, opt_states) = results["baseline"], results["optimized"]
    assert opt_rows == base_rows  # sequence order, stream, key, content hash, exact payload
    assert opt_states == base_states  # checkpoint blob bytes after every prefix
    kinds = Counter(r[1].rsplit(":", 1)[-1] for r in experience_rows(base_rows))
    assert kinds["observations"] == len(events)
    assert kinds["snapshots"] > 1 and kinds["outcomes"] > 0 and kinds["lifecycle"] > 0
    if scenario == "volatile":
        # The plan cache is exercised: directional plans, entries and invalidations exist.
        payloads = [json.loads(r[4]) for r in experience_rows(base_rows)]
        actions = {p.get("action") for p in payloads if "context_json" in p}
        states = {p.get("state") for p in payloads if "hypothetical_only" in p and "state" in p}
        assert {"BUY", "SELL"} <= actions
        assert {"ENTRY_TRIGGERED", "ACTIVE", "INVALIDATED"} <= states
        assert any(p.get("reference_kind") == "entry_zone_midpoint" for p in payloads)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_replay_matches_baseline_without_writing(tmp_path: Path, scenario: str) -> None:
    """Replay of the same journal: identical state, and no row is added or changed."""
    events = scenario_events(scenario)
    path = tmp_path / "live.sqlite"
    rows, _ = run_live("baseline", events, path)
    replayed = {n: run_replay(n, path) for n in IMPLEMENTATIONS}
    assert replayed["optimized"][0] == replayed["baseline"][0]
    assert replayed["optimized"][1] == replayed["baseline"][1] == rows  # replay writes nothing
    # Replay converges to what live ingest wrote: an optimized live journal replays the same.
    live_rows, _ = run_live("optimized", events, tmp_path / "optimized.sqlite")
    assert live_rows == rows
    assert run_replay("optimized", tmp_path / "optimized.sqlite")[0] == replayed["baseline"][0]


def test_checkpoint_restore_plus_delta_equals_cold_replay(tmp_path: Path) -> None:
    """ADR-022 invariant under both implementations: checkpoint + delta == full replay."""
    events = scenario_events("volatile")
    path = tmp_path / "j.sqlite"
    store_dir = tmp_path / "checkpoints"
    journal = SQLiteJournal(path)
    try:
        with implementation("optimized"):
            store = ckpt.CheckpointStore(store_dir, environment="test")
            runtime = ResearchRuntime(CONFIG, journal, checkpoints=store, checkpoint_interval=0)
            for event in events[:90]:
                runtime.ingest(event, completeness="complete")
            runtime.checkpoint()
            for event in events[90:]:
                runtime.ingest(event, completeness="complete")
    finally:
        journal.close()
    rows = dump_rows(path)
    reference_rows, _ = run_live("baseline", events, tmp_path / "ref.sqlite")
    assert rows == reference_rows
    saved = tmp_path / "saved-checkpoints"
    shutil.copytree(store_dir, saved)
    warm_bytes, cold_bytes = {}, {}
    for name in IMPLEMENTATIONS:
        copy = tmp_path / f"checkpoints-{name}"
        shutil.copytree(saved, copy)
        journal = SQLiteJournal(path)
        try:
            with implementation(name):
                warm = ResearchRuntime(
                    CONFIG, journal, checkpoints=ckpt.CheckpointStore(copy, environment="test")
                )
                assert warm.last_recovery["mode"] == "checkpoint"
                assert warm.last_recovery["replayed"] == len(events) - 90
                warm_bytes[name], warm_values = state_blob_hash(warm), recovered_state(warm)
        finally:
            journal.close()
        cold = SQLiteJournal(path)
        try:
            with implementation(name):
                replayed = ResearchRuntime(CONFIG, cold)
                assert replayed.last_recovery["mode"] == "full"
                cold_bytes[name], cold_values = state_blob_hash(replayed), recovered_state(replayed)
        finally:
            cold.close()
        # ADR-022 invariant, value level: the checkpoint was written from live objects
        # (Decimal exponents kept), so only canonical values are comparable with replay.
        assert warm_values == cold_values
    # Byte level across implementations, for both recovery paths.
    assert warm_bytes["optimized"] == warm_bytes["baseline"]
    assert cold_bytes["optimized"] == cold_bytes["baseline"]
    assert dump_rows(path) == rows


def test_restore_discards_derived_caches(tmp_path: Path) -> None:
    """Caches are never checkpointed; a restored service rebuilds them on demand."""
    journal = SQLiteJournal(tmp_path / "j.sqlite")
    try:
        with implementation("optimized"):
            runtime = ResearchRuntime(CONFIG, journal)
            for event in scenario_events("volatile")[:40]:
                runtime.ingest(event, completeness="complete")
        service = runtime.experience
        assert service._derived.config is not None and service._derived.scopes
        memory = service.checkpoint_state()
        assert "_derived" not in memory and set(memory) == {
            "last",
            "pending",
            "states",
            "samples",
            "completed",
            "seen",
        }
        fresh = optimized_service.ExperienceService(journal, CONFIG, runtime.stream)
        fresh.restore_state(memory)
        assert fresh._derived == optimized_service._Derived()
    finally:
        journal.close()


@pytest.mark.parametrize("fail_at", [3, 40, 170])
def test_failed_experience_append_recovers_to_identical_rows(tmp_path: Path, fail_at: int) -> None:
    """A failure at the n-th Experience write: same recovery, no missing or duplicate rows."""
    events = scenario_events("volatile")[:100]
    faulty = {
        n: run_live(n, events, tmp_path / f"{n}.sqlite", fail_at=fail_at) for n in IMPLEMENTATIONS
    }
    assert faulty["optimized"] == faulty["baseline"]
    clean_rows, _ = run_live("optimized", events, tmp_path / "clean.sqlite")
    rows = faulty["optimized"][0]
    identities = [(r[1], r[2], r[3]) for r in rows]
    assert len(identities) == len(set(identities))  # no duplicates
    assert sorted(identities) == sorted((r[1], r[2], r[3]) for r in clean_rows)  # none missing
    # Both journals replay to the same state (a rebuild mid-run replaced live objects).
    assert (
        run_replay("optimized", tmp_path / "optimized.sqlite")[0]
        == run_replay("optimized", tmp_path / "clean.sqlite")[0]
    )


def test_exc1_old_writer_journal_replays_identically(tmp_path: Path) -> None:
    """ADR-028: the 9016004 journal replays unchanged and to the baseline state."""
    from tests.test_recovery_checkpoint import runtime_config

    rows = load_fixture()
    results = {}
    for name in IMPLEMENTATIONS:
        path = tmp_path / f"{name}.sqlite"
        load_rows(path, rows)
        results[name] = run_replay(name, path, runtime_config())
    assert results["optimized"] == results["baseline"]
    assert results["optimized"][1] == rows


def test_identity_conflicts_still_raise(tmp_path: Path) -> None:
    events = scenario_events("calm")[:30]
    path = tmp_path / "j.sqlite"
    rows, _ = run_live("optimized", events, path)
    journal = SQLiteJournal(path)
    try:
        runtime = ResearchRuntime(CONFIG, journal)
        row = json.loads(next(r[4] for r in rows if r[1] == runtime.stream and r[2] == "bench:5"))
        event = decode(NormalizedPriceEvent, row["event"])
        # Same event identity, different observation content.
        with pytest.raises(ValueError, match="experience_event_identity_conflict"):
            runtime.experience.observe(event, row["output"], completeness="partial")
    finally:
        journal.close()

    # A committed snapshot that differs from its re-freeze fails replay under both.
    target = next(i for i, r in enumerate(rows) if r[1] == "experience:v1:snapshots")
    sequence, stream, key, _, payload = rows[target]
    snapshot = json.loads(payload)
    snapshot["action"] = "BUY"
    tampered = list(rows)
    tampered[target] = (sequence, stream, key, canonical_hash(snapshot), json.dumps(snapshot))
    for name in IMPLEMENTATIONS:
        bad = tmp_path / f"tampered-{name}.sqlite"
        load_rows(bad, tampered)
        with pytest.raises(ValueError, match="journal_identity_conflict"):
            run_replay(name, bad)
        assert dump_rows(bad) == tampered


# --- Pure-function equivalence ---------------------------------------------------------


def recorded_rows(path: Path) -> list[dict[str, Any]]:
    rows = dump_rows(path)
    stream = next(r[1] for r in rows if r[1].endswith(":config"))[: -len(":config")]
    return [json.loads(r[4]) for r in rows if r[1] == stream]


METADATA: tuple[dict[str, Any] | None, ...] = (
    None,
    {},
    {"quote": Decimal("2001.50"), "at": datetime(2026, 1, 5, 1, 2, tzinfo=UTC), "ok": True},
)


def test_digest_output_hash_and_freeze_match_baseline(tmp_path: Path) -> None:
    """C3: reused encodings equal `canonical_hash`; freeze() equals the baseline freeze()."""
    path = tmp_path / "j.sqlite"
    run_live("baseline", scenario_events("volatile")[:80], path)
    stream = "research:" + canonical_hash(CONFIG)
    checked = 0
    for row in recorded_rows(path):
        event, output = decode(NormalizedPriceEvent, row["event"]), row["output"]
        frame = optimized_engine.recorded(output)
        assert frame.hash == canonical_hash(output)
        for metadata in METADATA:
            for completeness in ("complete", "unknown", "ünïcode"):
                assert optimized_engine.observation_digest(
                    event, frame, completeness, metadata
                ) == canonical_hash((event, output, completeness, metadata))
        # ADR-028 presence: absent, explicit null and present values of additive fields.
        variants = [
            {k: v for k, v in output.items() if k not in {"trendline", "entry_readiness"}},
            {**output, "trendline": None, "entry_readiness": None},
            output,
        ]
        for variant in variants:
            arguments = (CONFIG, stream, event, variant, "complete", METADATA[2])
            assert optimized_engine.freeze(*arguments) == baseline_engine.freeze(*arguments)
        checked += 1
    assert checked == 80


def test_freeze_of_uncanonical_live_output_matches_baseline(tmp_path: Path) -> None:
    """Raw engine output (dataclasses, Decimal, datetime) canonicalizes identically."""
    journal = SQLiteJournal(tmp_path / "j.sqlite")
    try:
        runtime = ResearchRuntime(CONFIG, journal)
        for event in scenario_events("volatile")[:40]:
            runtime.ingest(event)
            raw = runtime.engine._output  # not canonicalized
            arguments = (CONFIG, runtime.stream, event, raw, "unknown", None)
            assert optimized_engine.freeze(*arguments) == baseline_engine.freeze(*arguments)
            assert optimized_engine.recorded(raw).hash == canonical_hash(raw)
    finally:
        journal.close()


def test_plan_measure_and_advance_match_baseline(tmp_path: Path) -> None:
    """C1: prepared plans give the baseline plan, lifecycle and outcome for every horizon."""
    path = tmp_path / "j.sqlite"
    run_live("baseline", scenario_events("volatile"), path)
    rows = recorded_rows(path)
    events = [decode(NormalizedPriceEvent, r["event"]) for r in rows]
    stream = "research:" + canonical_hash(CONFIG)
    directional = 0
    for index in range(0, len(rows), 3):
        experience = baseline_engine.freeze(
            CONFIG, stream, events[index], rows[index]["output"], "complete", None
        )
        prepared = optimized_engine.plan_of(experience)
        assert prepared[:3] == baseline_engine.plan(experience)
        directional += experience.action in {"BUY", "SELL"} and prepared.risk is not None
        samples: list[dict[str, Any]] = [
            {"event": e, "completeness": "complete"}
            for e in events[index + 1 :]
            if baseline_engine.eligible(experience, e)
        ]
        lifecycle = baseline_engine.initial_lifecycle(experience)
        for sample in samples[:70]:
            expected = baseline_engine.advance(experience, lifecycle, sample["event"])
            assert optimized_engine.advance(experience, lifecycle, sample["event"]) == expected
            got = optimized_engine.advance(
                experience, lifecycle, sample["event"], prepared=prepared
            )
            assert got == expected
            for state in expected:
                lifecycle = state
        for minutes in (5, 15, 30, 60):
            endpoint = samples[-1]["event"] if samples else events[index]
            expected_outcome = baseline_engine.measure(experience, minutes, samples, endpoint)
            assert optimized_engine.measure(experience, minutes, samples, endpoint) == (
                expected_outcome
            )
            assert (
                optimized_engine.measure(experience, minutes, samples, endpoint, prepared=prepared)
                == expected_outcome
            )
    assert directional > 0


# --- The comparisons detect optimization bugs ------------------------------------------


def _differs(tmp_path: Path, scenario: str, events: int = 120) -> bool:
    chosen = scenario_events(scenario)[:events]
    base = run_live("baseline", chosen, tmp_path / "base.sqlite")
    mutated = run_live("optimized", chosen, tmp_path / "mutant.sqlite")
    return base != mutated


MUTATIONS: dict[str, tuple[str, Callable[[pytest.MonkeyPatch], None]]] = {
    # C2: a new fingerprint no longer starts a new Experience.
    "stale_fingerprint": (
        "calm",
        lambda mp: mp.setattr(optimized_service, "fingerprint", lambda scope, output: scope),
    ),
    # C3: the idempotency digest drops completeness and metadata (only `_seen` changes).
    "weak_digest": (
        "calm",
        lambda mp: mp.setattr(
            optimized_service,
            "observation_digest",
            lambda event, frame, completeness, metadata: canonical_hash((event, frame.hash)),
        ),
    ),
    # ADR-028: additive fields lost in the materialized snapshot.
    "additive_fields_dropped": (
        "calm",
        lambda mp: mp.setattr(optimized_engine, "ADDITIVE_OUTPUT_CONTEXT", ()),
    ),
    # C1: one cached plan served to every Experience.
    "shared_plan_cache": (
        "volatile",
        lambda mp: mp.setattr(
            optimized_service.ExperienceService,
            "_plan",
            lambda self, experience: self._derived.plans.setdefault(
                "one", optimized_engine.plan_of(experience)
            ),
        ),
    ),
    # C1: a cached plan with a wrong T0 price.
    "wrong_cached_t0_price": (
        "calm",
        lambda mp: mp.setattr(
            optimized_service,
            "plan_of",
            lambda experience: optimized_engine.plan_of(experience)._replace(
                t0_price=optimized_engine.plan_of(experience).t0_price + Decimal("0.1")
            ),
        ),
    ),
    # C3: output_hash taken from a different encoding than canonical_hash uses.
    "output_hash_encoding": (
        "calm",
        lambda mp: mp.setattr(
            optimized_engine.RecordedOutput,
            "hash",
            property(lambda self: canonical_hash(json.dumps(self.canonical))),
        ),
    ),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_equivalence_checks_detect_optimization_bugs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    scenario, apply = MUTATIONS[mutation]
    apply(monkeypatch)
    assert _differs(tmp_path, scenario)
