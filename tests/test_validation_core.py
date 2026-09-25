"""Replay validation core: input boundary, determinism, outcomes, metrics, artifacts (ADR-030)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import nexora
import pytest
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.backtest.datasets import save_dataset
from nexora.entry_readiness import evaluate_entry_readiness
from nexora.experience.engine import freeze, measure
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.research.runtime import RuntimeConfig
from nexora.storage import SQLiteJournal
from nexora.validation import (
    BaselineRule,
    OutcomeDefinition,
    SealBroken,
    SourceRevision,
    ValidationError,
    ValidationInputError,
    ValidationSpec,
    verify_bundle,
    write_bundle,
)
from nexora.validation.artifact import HASHED_FILES, bundle_files
from nexora.validation.inputs import load_input
from nexora.validation.labeling import label
from nexora.validation.metrics import nearest_rank, summarize
from nexora.validation.models import Direction, ObservationRecord
from nexora.validation.revision import read_source_revision, revision_from_git_output
from nexora.validation.sealing import as_of_json, record_hash

from tests.validation_fixtures import (
    CLEAN_REVISION,
    dataset_for,
    events_definition,
    pipeline_config,
    run,
    spec,
    tick,
    tick_events,
    time_definition,
)

# --- Input boundary: fail closed -------------------------------------------------------


def test_dataset_hash_mismatch_is_rejected() -> None:
    events = tick_events(30)
    dataset = dataset_for(events)
    from nexora.validation import run_validation

    with pytest.raises(ValidationInputError, match="dataset_hash_mismatch"):
        run_validation(
            dataset=dataset,
            events=events,
            expected_dataset_hash="0" * 64,
            pipeline=pipeline_config(),
            spec=spec((10,)),
            source_revision=CLEAN_REVISION,
        )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda ev: (*ev[:5], ev[4], *ev[5:]), "invalid_dataset_event"),  # duplicate identity
        (lambda ev: (ev[1], ev[0], *ev[2:]), "dataset_order_violation"),  # out of order
        (lambda ev: (*ev[:3], replace(ev[3], is_duplicate=True), *ev[4:]), "invalid_dataset_event"),
        (
            lambda ev: (*ev[:3], replace(ev[3], is_out_of_order=True), *ev[4:]),
            "invalid_dataset_event",
        ),
        (
            lambda ev: (
                *ev[:3],
                replace(ev[3], received_at=ev[3].event_time - timedelta(seconds=1)),
                *ev[4:],
            ),
            "invalid_dataset_event",
        ),
        (lambda ev: (*ev[:3], replace(ev[3], price=D("-1")), *ev[4:]), "invalid_dataset_event"),
        (lambda ev: (*ev[:3], replace(ev[3], symbol="EURUSD"), *ev[4:]), "invalid_dataset_event"),
        (
            lambda ev: (
                *ev[:3],
                replace(
                    ev[3],
                    event_time=ev[2].event_time,  # received_at regresses
                    received_at=ev[2].event_time + timedelta(milliseconds=100),
                ),
                *ev[4:],
            ),
            "dataset_order_violation",
        ),
    ],
)
def test_malformed_duplicate_and_misordered_events_fail_closed(mutate: object, code: str) -> None:
    events = tick_events(30)
    broken = mutate(events)  # type: ignore[operator]
    from nexora.validation import run_validation

    # Tampering behind an existing manifest is caught by the partition hash first.
    original = dataset_for(events)
    with pytest.raises(ValidationInputError, match="partition_hash_mismatch"):
        run_validation(
            dataset=original,
            events=broken,
            expected_dataset_hash=canonical_hash(original),
            pipeline=pipeline_config(),
            spec=spec((10,)),
            source_revision=CLEAN_REVISION,
        )
    # A manifest honestly built over bad events still fails on the events themselves.
    manifest = replace(
        original,
        partitions=(
            replace(original.partitions[0], content_hash=canonical_hash(broken), rows=len(broken)),
        ),
    )
    with pytest.raises(ValidationInputError, match=code):
        run_validation(
            dataset=manifest,
            events=broken,
            expected_dataset_hash=canonical_hash(manifest),
            pipeline=pipeline_config(),
            spec=spec((10,)),
            source_revision=CLEAN_REVISION,
        )


def test_non_finite_prices_cannot_form_a_dataset() -> None:
    events = tick_events(10)
    with pytest.raises(ValueError, match="non_finite_decimal"):
        dataset_for((*events[:3], replace(events[3], price=D("NaN")), *events[4:]))


def test_bars_symbol_source_precision_and_quality_are_rejected() -> None:
    events = tick_events(30)
    bars = tuple(replace(e, kind="bar", price_source="bid") for e in events)
    with pytest.raises(ValidationInputError, match="bar_knowledge_time_undefined"):
        run(bars, spec((10,)))
    config = pipeline_config()
    other_symbol = replace(
        config,
        resolutions=tuple(
            replace(r, pnf=replace(r.pnf, symbol="EURUSD")) for r in config.resolutions
        ),
        regime=replace(config.regime, symbol="EURUSD"),
        signals=replace(config.signals, symbol="EURUSD"),
    )
    with pytest.raises(ValidationInputError, match="pipeline_symbol_mismatch"):
        run(events, spec((10,)), pipeline=other_symbol)
    ask_source = tuple(replace(e, price_source="ask", price=e.ask or e.price) for e in events)
    with pytest.raises(ValidationInputError, match="pipeline_price_source_mismatch"):
        run(ask_source, spec((10,)))
    precise = tuple(replace(e, precision=2) for e in events)
    with pytest.raises(ValidationInputError, match="pipeline_precision_mismatch"):
        run(precise, spec((10,)))
    dataset = replace(dataset_for(events), quality_status="unknown")
    from nexora.validation import run_validation

    with pytest.raises(ValidationInputError, match="dataset_quality_unknown"):
        run_validation(
            dataset=dataset,
            events=events,
            expected_dataset_hash=canonical_hash(dataset),
            pipeline=config,
            spec=spec((10,)),
            source_revision=CLEAN_REVISION,
        )


def test_stored_dataset_is_loaded_read_only_and_corruption_fails_closed(tmp_path: Path) -> None:
    events = tick_events(30)
    dataset = dataset_for(events)
    directory = tmp_path / "dataset"
    save_dataset(directory, dataset, events)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    loaded, loaded_events = load_input(
        directory, expected_dataset_hash=canonical_hash(dataset), pipeline=pipeline_config()
    )
    assert loaded == dataset and loaded_events == events
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    rows = json.loads((directory / "normalized.json").read_text(encoding="utf-8"))
    rows[3]["price"] = "999"
    rows[3]["bid"] = "999"
    (directory / "normalized.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValidationInputError, match="partition_hash_mismatch"):
        load_input(
            directory, expected_dataset_hash=canonical_hash(dataset), pipeline=pipeline_config()
        )


def test_equal_event_times_keep_canonical_order_and_gaps_propagate() -> None:
    events = list(tick_events(40))
    # Equal market timestamps are legal; order is the canonical index only (E4).
    events[10] = replace(events[10], event_time=events[9].event_time)
    events[20] = replace(events[20], is_gap=True, gap_from_sequence=19)
    result = run(tuple(events), spec((15, 30)))
    assert result.status == "VALID"
    gapped = [o for o in result.outcomes if o.gap_observed]
    censoring = [o for o in gapped if o.definition_id.startswith("fixture-time")]
    measured = [o for o in gapped if o.definition_id.startswith("fixture-events")]
    assert censoring and measured
    assert {o.status for o in censoring} <= {"CENSORED_GAP", "CENSORED_END_OF_DATA"}
    assert "CENSORED_GAP" in {o.status for o in censoring}
    assert {o.status for o in measured} <= {"COMPLETE", "CENSORED_END_OF_DATA"}
    anchored = [o for o in result.observations if o.anchor_index == 21]
    assert anchored and all(o.gap_at_anchor for o in anchored)


# --- Determinism -----------------------------------------------------------------------


def test_same_input_gives_byte_identical_results_and_bundles(tmp_path: Path) -> None:
    events = tick_events(60)
    first, second = run(events, spec((25,))), run(events, spec((25,)))
    assert first == second
    assert bundle_files(first) == bundle_files(second)
    a = write_bundle(tmp_path / "a", first, envelope={"created_at": "first"})
    b = write_bundle(tmp_path / "b", second, envelope={"created_at": "second"})
    for name in (*HASHED_FILES, "integrity.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_run_identity_changes_with_every_provenance_input() -> None:
    events = tick_events(50)
    base = run(events, spec((25,))).run_id
    assert run(events[:-1], spec((25,))).run_id != base
    assert run(events, spec((24,))).run_id != base
    other_definition = replace(spec((25,)), outcome_definitions=(time_definition(600),))
    assert run(events, other_definition).run_id != base
    config = pipeline_config()
    assert (
        run(events, spec((25,)), pipeline=replace(config, version="valid-test-v2")).run_id != base
    )
    dirty = SourceRevision(commit="a" * 40, dirty=True)
    assert run(events, spec((25,)), revision=dirty).run_id != base


def test_fresh_process_reproduces_run_id_and_bundle_hashes(tmp_path: Path) -> None:
    events = tick_events(50)
    result = run(events, spec((25,)))
    expected = {name: canonical_hash(data.decode()) for name, data in bundle_files(result).items()}
    script = (
        "import json\n"
        "from nexora.artifacts import canonical_hash\n"
        "from nexora.validation.artifact import bundle_files\n"
        "from tests.validation_fixtures import run, spec, tick_events\n"
        "r = run(tick_events(50), spec((25,)))\n"
        "print(json.dumps({'run_id': r.run_id, 'files': {n: canonical_hash(d.decode()) "
        "for n, d in bundle_files(r).items()}}))\n"
    )
    repository = Path(nexora.__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "PYTHONHASHSEED"}}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repository / "packages"), str(repository / "apps" / "api"), str(repository)]
    )
    env["PYTHONHASHSEED"] = "12345"
    output = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout
    fresh = json.loads(output)
    assert fresh == {"run_id": result.run_id, "files": expected}


def test_entry_readiness_capture_matches_pipeline_output_contract() -> None:
    events = tick_events(80)
    pipeline = ResearchPipeline(pipeline_config())
    states = set()
    for event in events:
        output = pipeline.process(event)
        recomputed = evaluate_entry_readiness(
            decision=pipeline.signals.snapshot().decision,
            trendline=pipeline.trendline.snapshot(),
            config_version=pipeline.config.version,
        )
        assert output["entry_readiness"] == canonical_serialize(recomputed)
        states.add(recomputed.state)
    assert {"READY", "NOT_READY", "BLOCKED"} <= states


# --- Outcome labeling --------------------------------------------------------------------


def _observation(
    events: tuple[NormalizedPriceEvent, ...],
    index: int,
    direction: Direction,
    *,
    invalidation: str | None = None,
) -> ObservationRecord:
    event = events[index - 1]
    unsealed = ObservationRecord(
        schema_version=1,
        generation_mode="RECOMPUTED",
        subject_kind="signal.decision",
        subject_group=f"test:{direction}",
        subject_id=f"subject:{index}",
        anchor_index=index,
        anchor_event_id=event.identity_key,
        anchor_event_time=event.event_time,
        t0=event.received_at,
        direction=direction,
        reference_price=event.price,
        observed_effective_box_size=D("0.5"),
        invalidation_price=D(invalidation) if invalidation else None,
        gap_at_anchor=False,
        as_of=as_of_json({}),
        evidence=(),
        record_hash="",
    )
    return replace(unsealed, record_hash=record_hash(unsealed))


def _golden_events() -> tuple[NormalizedPriceEvent, ...]:
    return (
        tick(1, "100", seconds=0),
        tick(2, "101", seconds=10),
        tick(3, "98", seconds=20),
        tick(4, "104", seconds=30),
        tick(5, "99", seconds=40),
        tick(6, "105", seconds=400),
    )


def _price_definition(**changes: object) -> OutcomeDefinition:
    base = OutcomeDefinition(
        definition_id="golden",
        version="fixture-v1",
        window_kind="time",
        window_length=60,
        reference="anchor_price",
        box_unit="fixed_price_unit",
        gap_policy="label_censored",
        fixed_price_unit=D("1"),
        barrier_unit="price",
        favorable_barrier=D("3"),
        adverse_barrier=D("1.5"),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_long_outcome_golden_vector() -> None:
    events = _golden_events()
    observation = _observation(events, 1, "long", invalidation="98.5")
    (outcome,) = label(
        (observation,),
        events,
        (_price_definition(),),
        pnf_captured=False,
    )
    assert outcome.status == "COMPLETE" and outcome.sample_count == 4
    assert (outcome.up_excursion, outcome.down_excursion) == (D(4), D(2))
    assert (outcome.mfe, outcome.mae, outcome.mfe_boxes, outcome.mae_boxes) == (
        D(4),
        D(2),
        D(4),
        D(2),
    )
    assert outcome.time_to_mfe_seconds == D("29.85")
    assert outcome.time_to_mae_seconds == D("19.85")
    assert (outcome.endpoint_index, outcome.endpoint_change) == (5, D(-1))
    assert (outcome.first_barrier, outcome.first_barrier_index) == ("adverse", 3)
    assert outcome.barrier_overshoot == D("0.5")
    assert (outcome.invalidation_status, outcome.invalidation_index) == ("evaluated", 3)
    assert outcome.adverse_reversal_status == "not_captured"
    assert outcome.max_sample_gap_seconds == D(10)


def test_short_and_neutral_outcome_golden_vectors() -> None:
    events = _golden_events()
    short = _observation(events, 1, "short", invalidation="103")
    neutral = _observation(events, 1, "neutral")
    short_outcome, neutral_outcome = label(
        (short, neutral),
        events,
        (_price_definition(),),
        pnf_captured=True,
    )
    assert (short_outcome.mfe, short_outcome.mae) == (D(2), D(4))
    assert (short_outcome.first_barrier, short_outcome.first_barrier_index) == ("adverse", 4)
    assert short_outcome.invalidation_index == 4
    assert short_outcome.endpoint_change == D(1)
    assert short_outcome.adverse_reversal_status == "evaluated"
    assert neutral_outcome.first_barrier == "not_applicable"
    assert neutral_outcome.mfe is None and neutral_outcome.mae is None
    assert (neutral_outcome.up_excursion, neutral_outcome.down_excursion) == (D(4), D(2))
    assert neutral_outcome.invalidation_status == "not_applicable"
    assert neutral_outcome.adverse_reversal_status == "not_applicable"


def test_censoring_is_labelled_never_dropped() -> None:
    events = _golden_events()
    late = _observation(events, 5, "long")
    (censored,) = label(
        (late,),
        events,
        (_price_definition(window_length=600),),
        pnf_captured=False,
    )
    assert censored.status == "CENSORED_END_OF_DATA"
    gapped = (*events[:2], replace(events[2], is_gap=True), *events[3:])
    observation = _observation(gapped, 1, "long")
    (label_censored,) = label((observation,), gapped, (_price_definition(),), pnf_captured=False)
    (measured,) = label(
        (observation,),
        gapped,
        (_price_definition(gap_policy="measure_through"),),
        pnf_captured=False,
    )
    assert label_censored.status == "CENSORED_GAP" and measured.status == "COMPLETE"
    assert label_censored.mfe == measured.mfe and measured.gap_observed
    empty = (tick(1, "100", seconds=0), tick(2, "101", seconds=500))
    (none,) = label(
        (_observation(empty, 1, "long"),),
        empty,
        (_price_definition(),),
        pnf_captured=False,
    )
    assert none.status == "NO_SAMPLES" and none.sample_count == 0 and none.mfe is None


def test_box_units_are_explicit_and_unavailable_boxes_disable_box_barriers() -> None:
    events = _golden_events()
    observation = replace(_observation(events, 1, "long"), observed_effective_box_size=None)
    observation = replace(observation, record_hash=record_hash(observation))
    definition = _price_definition(
        box_unit="observed_effective_box_size",
        fixed_price_unit=None,
        barrier_unit="boxes",
        favorable_barrier=D(2),
        adverse_barrier=D(2),
    )
    (outcome,) = label((observation,), events, (definition,), pnf_captured=False)
    assert outcome.box_unit_status == "unavailable" and outcome.mfe_boxes is None
    assert outcome.first_barrier == "not_applicable"
    for bad in (
        {"box_unit": "fixed_price_unit", "fixed_price_unit": None},
        {"box_unit": "none", "fixed_price_unit": None, "barrier_unit": "boxes"},
        {"barrier_unit": None},
        {"favorable_barrier": D(0)},
        {"window_length": 0},
    ):
        with pytest.raises(ValidationInputError):
            _price_definition(**bad)


def test_time_window_parity_with_experience_measure_semantics() -> None:
    """Overlapping semantics with EX1 measure(): eligible samples and market excursions."""
    events = tick_events(80)
    result = run(events, replace(spec((25,)), outcome_definitions=(time_definition(300),)))
    runtime_config = RuntimeConfig(pipeline_config(), "USD/oz")
    compared = 0
    for observation in (
        o for o in result.observations if o.subject_kind == "baseline.every_nth_event"
    ):
        outcome = next(o for o in result.outcomes if o.observation_hash == observation.record_hash)
        if outcome.status != "COMPLETE":
            continue
        k = observation.anchor_index
        pipeline = ResearchPipeline(pipeline_config())
        output: dict[str, object] = {}
        for event in events[:k]:
            output = pipeline.process(event)
        experience = freeze(runtime_config, "valid-parity", events[k - 1], output, "complete", None)
        due = experience.t0 + timedelta(minutes=5)
        endpoint = next(e for e in events[k:] if e.event_time >= due)
        samples = [{"event": e, "completeness": "complete"} for e in events[k:]]
        measured = measure(experience, 5, samples, endpoint)
        assert outcome.sample_count == measured["sample_count"]
        assert outcome.up_excursion == measured["market_upward_excursion"]
        assert outcome.down_excursion == measured["market_downward_excursion"]
        sample_ids = [
            events[j - 1].identity_key
            for j in range(outcome.sample_first_index or 0, (outcome.sample_last_index or -1) + 1)
        ]
        assert sample_ids == measured["sample_event_ids"]
        compared += 1
    assert compared >= 3


# --- Metrics -------------------------------------------------------------------------------


def test_metrics_are_descriptive_with_baseline_overlap_and_no_rates() -> None:
    result = run(tick_events(80), spec((30,)))
    assert result.metrics is not None
    metrics = result.metrics
    assert metrics.baseline_status == "configured" and metrics.quantile_method == "nearest_rank"
    assert metrics.groups_evaluated == len(metrics.groups) > 0
    encoded = json.dumps(canonical_serialize(metrics))
    for forbidden in ("rate", "probability", "score", "win"):
        assert f'"{forbidden}' not in encoded
    for group in metrics.groups:
        assert group.n_observations == sum(c.count for c in group.status_counts)
        assert group.complete_count <= group.n_observations
        if group.subject_group != "baseline.every_nth_event":
            assert group.baseline_group == "baseline.every_nth_event"
        for summary in group.distributions:
            assert summary.minimum <= summary.p25 <= summary.median <= summary.p75
            assert summary.p75 <= summary.maximum and summary.count <= group.complete_count
    assert any(g.overlapping_pairs > 0 for g in metrics.groups)
    unconfigured = run(tick_events(60), spec((30,), baseline=False))
    assert unconfigured.metrics is not None
    assert unconfigured.metrics.baseline_status == "not_configured"
    assert all(g.baseline_group is None for g in unconfigured.metrics.groups)


def test_nearest_rank_quantiles() -> None:
    values = [D(v) for v in (1, 2, 3, 4)]
    assert nearest_rank(values, D("0.25")) == 1
    assert nearest_rank(values, D("0.5")) == 2
    assert nearest_rank(values, D("0.75")) == 3
    assert nearest_rank([D(7)], D("0.5")) == 7


def test_tampered_observation_or_outcome_breaks_the_seal() -> None:
    result = run(tick_events(50), spec((25,)))
    definitions = result.manifest.spec.outcome_definitions
    tampered = replace(result.observations[0], reference_price=D("1"))
    with pytest.raises(SealBroken, match="observation_seal_broken"):
        summarize((tampered, *result.observations[1:]), result.outcomes, definitions, baseline=None)
    foreign = replace(result.outcomes[0], observation_hash="0" * 64)
    with pytest.raises(SealBroken, match="outcome_reference_broken"):
        summarize(result.observations, (foreign, *result.outcomes[1:]), definitions, baseline=None)


# --- Artifact, provenance, isolation ------------------------------------------------------


def test_bundle_is_write_once_verified_and_tamper_evident(tmp_path: Path) -> None:
    result = run(tick_events(50), spec((25,)))
    root = tmp_path / "validation"
    path = write_bundle(root, result, envelope={"created_at": "wall-clock-not-hashed"})
    assert path.name == result.run_id and verify_bundle(path) == result.run_id
    with pytest.raises(ValidationError, match="validation_result_exists"):
        write_bundle(root, result)
    assert sorted(p.name for p in root.iterdir()) == [result.run_id]
    (path / "envelope.json").write_text("{}", encoding="utf-8")
    assert verify_bundle(path) == result.run_id
    lines = (path / "outcomes.jsonl").read_bytes().replace(b'"COMPLETE"', b'"NO_SAMPLES"', 1)
    (path / "outcomes.jsonl").write_bytes(lines)
    with pytest.raises(SealBroken, match="bundle_file_hash_mismatch"):
        verify_bundle(path)


def test_interrupted_write_leaves_only_partial_and_rerun_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tick_events(50), spec((25,)))
    root = tmp_path / "validation"

    def crash(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "rename", crash)
    with pytest.raises(KeyboardInterrupt):
        write_bundle(root, result)
    monkeypatch.undo()
    partials = [p.name for p in root.iterdir()]
    assert len(partials) == 1 and partials[0].startswith(result.run_id + ".partial-")
    path = write_bundle(root, run(tick_events(50), spec((25,))))
    assert verify_bundle(path) == result.run_id
    assert (root / partials[0]).exists()  # never deleted by the framework


def test_invalid_replay_bundle_keeps_reason_without_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexora.validation import causal as causal_module
    from nexora.validation import runner as runner_module

    from tests.test_validation_causal import _leaky_capture

    monkeypatch.setattr(runner_module, "capture", _leaky_capture)
    monkeypatch.setattr(causal_module, "capture", _leaky_capture)
    result = run(tick_events(40), spec((20,)))
    path = write_bundle(tmp_path, result)
    stored = json.loads((path / "result.json").read_text(encoding="utf-8"))
    assert stored["status"] == "INVALID"
    assert stored["invalid_reason"] == "causal_acceptance_failed"
    assert (path / "outcomes.jsonl").read_bytes() == b""
    assert json.loads((path / "metrics.json").read_text(encoding="utf-8")) is None


def test_revision_qualification_follows_q_v7() -> None:
    head = "0123456789abcdef0123456789abcdef01234567\n"
    assert revision_from_git_output(head, "").qualified
    assert not revision_from_git_output(head, " M packages/x.py\n").qualified
    assert revision_from_git_output("not-a-sha", "") == SourceRevision(None, None)
    assert not SourceRevision(None, None).qualified
    events = tick_events(40)
    assert run(events, spec((20,))).qualification == "REVISION_QUALIFIED"
    dirty = SourceRevision(commit="a" * 40, dirty=True)
    assert run(events, spec((20,)), revision=dirty).qualification == "DEVELOPMENT_ONLY"
    assert run(events, spec((20,)), revision=SourceRevision(None, None)).qualification == (
        "DEVELOPMENT_ONLY"
    )
    probed = read_source_revision(Path(nexora.__file__).resolve().parents[2])
    assert probed.commit is None or len(probed.commit) == 40


def test_manifest_records_reproducibility_provenance() -> None:
    events = tick_events(40)
    result = run(events, spec((20,)))
    manifest = result.manifest
    assert result.run_id == "valid1-" + canonical_hash(manifest)
    assert manifest.generation_mode == "RECOMPUTED"
    assert manifest.dataset.dataset_hash == canonical_hash(dataset_for(events))
    assert manifest.dataset.event_count == 40 and manifest.dataset.first_event_id == "t:1"
    assert manifest.engines.pipeline_config_hash == canonical_hash(pipeline_config())
    assert [r.name for r in manifest.engines.resolutions] == ["fast", "medium", "slow"]
    assert len(manifest.engines.code_fingerprint) == 64
    assert {d.definition_id for d in manifest.definition_hashes} == {
        d.definition_id for d in manifest.spec.outcome_definitions
    }
    assert manifest.source_revision == CLEAN_REVISION


def test_validation_writes_nothing_and_never_touches_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_: object, **__: object) -> bool:
        raise AssertionError("validation must not write a journal")

    monkeypatch.setattr(SQLiteJournal, "append", forbidden)
    monkeypatch.chdir(tmp_path)
    result = run(tick_events(40), spec((20,)))
    assert result.status == "VALID"
    assert list(tmp_path.iterdir()) == []
    assert not Path(os.environ["NEXORA_RUNTIME_ROOT"]).exists()


def test_spec_validation_fails_closed() -> None:
    definition = events_definition()
    with pytest.raises(ValidationInputError, match="unknown_subject_kind"):
        ValidationSpec(("pattern.engine",), (definition,), (5,))  # type: ignore[arg-type]
    with pytest.raises(ValidationInputError, match="baseline_rule_mismatch"):
        ValidationSpec(("baseline.every_nth_event",), (definition,), (5,))
    with pytest.raises(ValidationInputError, match="invalid_outcome_definitions"):
        ValidationSpec(("signal.decision",), (definition, definition), (5,))
    with pytest.raises(ValidationInputError, match="invalid_baseline_rule"):
        BaselineRule(every_n_events=0)


def test_replay_engine_rejection_fails_closed() -> None:
    from nexora.validation.capture import capture

    events = tick_events(10)
    # Stage A re-applies the engines' own guards even to inputs that bypassed verification.
    noncanonical = (*events[:4], replace(events[4], is_duplicate=True), *events[5:])
    with pytest.raises(ValidationError, match="replay_failed:noncanonical_event"):
        capture(
            iter(noncanonical),
            pipeline_config=pipeline_config(),
            subject_kinds=("signal.decision",),
        )
    backwards = (*events[:4], replace(events[4], source_sequence=1), *events[5:])
    with pytest.raises(ValidationError, match="replay_failed:out_of_order_event"):
        capture(
            iter(backwards), pipeline_config=pipeline_config(), subject_kinds=("signal.decision",)
        )
