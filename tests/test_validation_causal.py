"""Mandatory causal acceptance properties for replay validation (ADR-030, Rin 2026-09-25)."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from nexora import validation
from nexora.market_data.models import NormalizedPriceEvent
from nexora.structure import StructureEngine, StructureSnapshot
from nexora.validation import capture as capture_module
from nexora.validation import causal as causal_module
from nexora.validation import runner as runner_module
from nexora.validation.capture import CaptureResult, capture
from nexora.validation.causal import MUTATIONS
from nexora.validation.labeling import label
from nexora.validation.models import SUBJECT_KINDS, ObservationRecord
from nexora.validation.sealing import observation_chain, record_hash

from tests.validation_fixtures import (
    events_definition,
    pipeline_config,
    run,
    spec,
    tick,
    tick_events,
)

PACKAGE = Path(validation.__file__).parent


def observe(events: Iterable[NormalizedPriceEvent]) -> tuple[ObservationRecord, ...]:
    return capture(
        iter(tuple(events)),
        pipeline_config=pipeline_config(),
        subject_kinds=tuple(k for k in SUBJECT_KINDS if k != "baseline.every_nth_event"),
    ).observations


def test_prefix_invariance_holds_at_every_cut_point() -> None:
    events = tick_events(90)
    full = observe(events)
    groups = {o.subject_kind for o in full}
    # Every non-baseline subject kind is exercised, so the property covers every capturer.
    assert groups == set(SUBJECT_KINDS) - {"baseline.every_nth_event"}
    for cut in range(1, len(events)):
        expected = tuple(o for o in full if o.anchor_index <= cut)
        assert observe(events[:cut]) == expected, cut


@pytest.mark.parametrize("cut", [15, 41, 60, 85])
@pytest.mark.parametrize("name", [name for name, _ in MUTATIONS])
def test_future_mutation_invariance_and_mutation_is_effective(cut: int, name: str) -> None:
    events = tick_events(120)
    mutate = dict(MUTATIONS)[name]
    tail = tuple(mutate(events[cut - 1], e) for e in events[cut:])
    assert tail != events[cut:]
    full = observe(events)
    mutated = observe((*events[:cut], *tail))
    before = tuple(o for o in full if o.anchor_index <= cut)
    assert tuple(o for o in mutated if o.anchor_index <= cut) == before
    # The mutation really changed what happened afterwards (the check has teeth).
    assert tuple(o for o in mutated if o.anchor_index > cut) != tuple(
        o for o in full if o.anchor_index > cut
    )


def test_runner_records_passed_causal_report_for_every_cut_and_mutation() -> None:
    result = run(tick_events(80), spec((20, 50)))
    assert result.status == "VALID" and result.causal is not None
    assert result.causal.status == "PASSED"
    checks = {(c.cut_index, c.property, c.mutation) for c in result.causal.checks}
    for cut in (20, 50):
        assert (cut, "prefix_invariance", "truncate") in checks
        for name, _ in MUTATIONS:
            assert (cut, "future_mutation_invariance", name) in checks
    assert all(c.passed and c.mutation_changed_tail for c in result.causal.checks)
    assert all(c.observations_compared > 0 for c in result.causal.checks)


def _leaky_capture(events: Iterable[NormalizedPriceEvent], **kwargs: Any) -> CaptureResult:
    """A deliberately defective Stage A that peeks at the last event of its input."""
    materialized = tuple(events)
    result = capture(iter(materialized), **kwargs)
    future = format(materialized[-1].price, "f")
    leaked = []
    for observation in result.observations:
        unsealed = replace(observation, as_of=observation.as_of + future, record_hash="")
        leaked.append(replace(unsealed, record_hash=record_hash(unsealed)))
    return CaptureResult(tuple(leaked), observation_chain(leaked), result.event_count)


def test_look_ahead_leak_fails_acceptance_and_invalidates_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "capture", _leaky_capture)
    monkeypatch.setattr(causal_module, "capture", _leaky_capture)
    result = run(tick_events(80), spec((20, 50)))
    assert result.status == "INVALID"
    assert result.invalid_reason == "causal_acceptance_failed"
    assert result.causal is not None and result.causal.status == "FAILED"
    failed = {c.property for c in result.causal.checks if not c.passed}
    assert failed == {"prefix_invariance", "future_mutation_invariance"}
    assert result.outcomes == () and result.metrics is None
    assert result.qualification == "DEVELOPMENT_ONLY"


def test_evidence_confirmed_after_decision_point_invalidates_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = StructureEngine.snapshot

    def future_pivot(self: StructureEngine) -> StructureSnapshot:
        snapshot = original(self)
        if not snapshot.pivots:
            return snapshot
        last = snapshot.pivots[-1]
        future = replace(
            last,
            confirmation_time=last.confirmation_time + timedelta(hours=1),
            source_transition_id=last.source_transition_id + ":future",
        )
        return replace(snapshot, pivots=(*snapshot.pivots, future))

    monkeypatch.setattr(StructureEngine, "snapshot", future_pivot)
    result = run(tick_events(60), spec((20,), baseline=False))
    assert result.status == "INVALID"
    assert result.invalid_reason == "evidence_after_decision_point"
    assert result.outcomes == () and result.metrics is None


def test_pivots_and_patterns_are_anchored_at_confirmation_not_occurrence() -> None:
    events = tick_events(90)
    by_id = {e.identity_key: e for e in events}
    observations = observe(events)
    pivots = [o for o in observations if o.subject_kind == "structure.pivot_confirmed"]
    patterns = [o for o in observations if o.subject_kind == "pattern.legacy"]
    assert pivots and patterns
    earlier_extremes = 0
    for observation in pivots:
        pivot = json.loads(observation.as_of)["pivot"]
        (item,) = observation.evidence
        assert item.knowledge_time == datetime.fromisoformat(pivot["confirmation_time"])
        assert item.knowledge_time <= by_id[observation.anchor_event_id].event_time
        occurred = datetime.fromisoformat(pivot["occurrence_time"])
        earlier_extremes += occurred < observation.anchor_event_time
    # Anchoring at the extreme would have placed these observations before they were known.
    assert earlier_extremes > 0
    for observation in patterns:
        pattern = json.loads(observation.as_of)["pattern"]
        (item,) = observation.evidence
        assert item.knowledge_time == datetime.fromisoformat(pattern["confirmation_time"])
        assert item.knowledge_time <= by_id[observation.anchor_event_id].event_time
        assert datetime.fromisoformat(pattern["start_time"]) < observation.anchor_event_time


def test_outcome_samples_start_strictly_after_decision_knowledge_time() -> None:
    anchor = tick(1, "100", seconds=0, latency=timedelta(seconds=5))
    # Market time before t0 (received later): happened before the decision was known.
    early = tick(2, "150", seconds=3, latency=timedelta(seconds=3))
    later = tick(3, "101", seconds=10)
    closing = tick(4, "102", seconds=900)
    events = (anchor, early, later, closing)
    observation = next(o for o in observe(events) if o.subject_kind == "signal.decision")
    assert observation.anchor_index == 1 and observation.t0 == anchor.received_at
    definition = replace(events_definition(3), window_length=3)
    (outcome,) = label((observation,), events, (definition,), pnf_captured=False)
    assert outcome.sample_first_index == 3 and outcome.sample_count == 2
    assert outcome.up_excursion == D(2)


def test_decision_stage_never_imports_outcome_or_metrics_code() -> None:
    forbidden = {
        "capture.py": {
            "nexora.validation.labeling",
            "nexora.validation.metrics",
            "nexora.validation.runner",
            "nexora.validation.artifact",
            "nexora.experience",
            "nexora.backtest",
        },
        "causal.py": {
            "nexora.validation.labeling",
            "nexora.validation.metrics",
            "nexora.validation.runner",
        },
        "labeling.py": {
            "nexora.validation.capture",
            "nexora.research",
            "nexora.pnf",
            "nexora.signals",
            "nexora.structure",
            "nexora.trendline",
        },
        "metrics.py": {"nexora.validation.capture", "nexora.research"},
        "sealing.py": {"nexora.research", "nexora.validation.capture"},
    }
    for filename, banned in forbidden.items():
        tree = ast.parse((PACKAGE / filename).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not {m for m in imported for b in banned if m == b or m.startswith(b + ".")}, (
            filename
        )


def test_core_reads_no_wall_clock_environment_or_randomness() -> None:
    banned_modules = {"random", "secrets", "uuid", "time"}
    banned_attributes = {"now", "utcnow", "today", "environ", "getenv", "monotonic"}
    for path in PACKAGE.glob("*.py"):
        if path.name == "revision.py":  # shell-side git probe, never called by the core
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not modules & banned_modules, path.name
        assert not attributes & banned_attributes, path.name


def test_capture_consumes_its_input_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    events = tick_events(30)
    pulled: list[int] = []

    def stream() -> Iterable[NormalizedPriceEvent]:
        for event in events:
            pulled.append(event.source_sequence)
            yield event

    ahead: list[int] = []
    original = capture_module._capture_step

    def spy(state: Any, step: Any, history: Any, resolution: str) -> None:
        # When e_k is captured, no later event has been pulled from the source.
        ahead.append(len(pulled) - step.index)
        original(state, step, history, resolution)

    monkeypatch.setattr(capture_module, "_capture_step", spy)
    capture(stream(), pipeline_config=pipeline_config(), subject_kinds=("signal.decision",))
    assert ahead == [0] * len(events)


def test_every_subject_kind_passes_prefix_invariance_through_the_runner() -> None:
    result = run(tick_events(70), spec((10, 30, 55)))
    assert result.status == "VALID"
    assert {o.subject_kind for o in result.observations} == set(SUBJECT_KINDS)
    assert result.causal is not None and all(c.passed for c in result.causal.checks)


def test_cut_points_are_mandatory_and_must_leave_a_future_tail() -> None:
    from nexora.validation import ValidationInputError, ValidationSpec

    with pytest.raises(ValidationInputError, match="causal_cut_points_required"):
        ValidationSpec(("signal.decision",), (events_definition(),), ())
    with pytest.raises(ValidationInputError, match="causal_cut_points_required"):
        ValidationSpec(("signal.decision",), (events_definition(),), (5, 3))
    with pytest.raises(ValidationInputError, match="causal_cut_point_out_of_range"):
        run(tick_events(20), spec((20,), baseline=False))
