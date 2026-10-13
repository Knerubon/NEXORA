"""ENGINEERING regression tests for PR #79 Hardening Round 4 (re-review R-1 .. R-5).

R-1: the holdout could still be read by building a SECOND manifest over the same (or
overlapping) events with no plan or a different plan. The split registry is the independent
record that closes it. R-3 / R-4 / R-5 harden declarations the re-review flagged as INFO.
Inputs are synthetic; nothing here is evidence about trading profitability.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from nexora.edge import (
    KNOWN_ZERO,
    CostModel,
    EdgeDatasetError,
    ExitInputError,
    ExitPolicy,
    HoldoutAccessLog,
    HoldoutUnlock,
    IntegrityReceipt,
    Provenance,
    RegistrationError,
    SplitPlan,
    SplitRegistration,
    SplitRegistry,
    TradePlan,
    audit_events,
    build_edge_manifest,
    evaluate_gates,
    run_exit_mode,
    verify_edge_dataset,
)
from nexora.edge.evidence import EvidenceGateError
from nexora.market_data.models import NormalizedPriceEvent

from tests.edge_fixtures import BAR, T0, make_bar, registry_args, synthetic_manifest

D = Decimal
LAT = BAR + timedelta(seconds=30)
ZERO = CostModel("t", "SYNTH-UNIT", KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO)
OVERLAP = ExitPolicy("r4", "allow_overlap", BAR)
PROV = Provenance("synthetic_fixture", "synthetic-test", T0, "UTC", "v", ohlc_basis="bid")


def flat(count: int) -> tuple[NormalizedPriceEvent, ...]:
    return tuple(make_bar(i, "100", "100.5", "99.8", "100") for i in range(count))


def plan60() -> SplitPlan:
    return SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 40, T0 + BAR * 60)


def holdout_market() -> tuple[NormalizedPriceEvent, ...]:
    bars = list(flat(60))
    bars[46] = make_bar(46, "100", "104.5", "99.8", "100")  # target hit inside the holdout
    return tuple(bars)


HOLDOUT_PLAN = TradePlan("h", "long", T0 + BAR * 45, D("98"), D("104"))


def registry(*plans: SplitPlan, source: str = "synthetic-test") -> SplitRegistry:
    return SplitRegistry(
        tuple(SplitRegistration(source, "SYNTH", p, "rin", "pre-registered") for p in plans)
    )


def run(
    plans: Any,
    bars: tuple[NormalizedPriceEvent, ...],
    *,
    manifest_plan: SplitPlan | None,
    reg: SplitRegistry,
    expected_registry_hash: str | None = None,
    policy: ExitPolicy = OVERLAP,
    **kwargs: Any,
) -> Any:
    """A run whose MANIFEST is built by the caller (registering `manifest_plan`) while the
    independent registry `reg` says which plan governs the stream."""
    manifest, digest = synthetic_manifest(bars, split_plan=manifest_plan)
    return run_exit_mode(
        plans,
        bars,
        manifest=manifest,
        expected_manifest_hash=digest,
        registry=reg,
        expected_registry_hash=(
            reg.registry_hash if expected_registry_hash is None else expected_registry_hash
        ),
        policy=policy,
        cost_model=ZERO,
        unit_size=D("1"),
        **kwargs,
    )


# ---- R-1: second-manifest bypass ---------------------------------------------------------------


def test_r1_second_manifest_without_plan_cannot_run_unsplit_over_a_registered_holdout() -> None:
    # Previously (a48e5f7, probe P1b): ('unsplit', 1 trade, 'target', holdout_access=None).
    with pytest.raises(ExitInputError, match="registered_split_requires_split_run"):
        run([HOLDOUT_PLAN], holdout_market(), manifest_plan=None, reg=registry(plan60()))


def test_r1_second_manifest_with_another_plan_cannot_read_the_holdout_as_validation() -> None:
    # Previously (probe P9b): validation_end moved to bar 58, holdout read as validation.
    moved = SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 58, T0 + BAR * 60)
    with pytest.raises(ExitInputError, match="split_plan_not_registered_for_dataset"):
        run(
            [HOLDOUT_PLAN],
            holdout_market(),
            manifest_plan=moved,
            reg=registry(plan60()),
            split=moved,
            segment="validation",
        )


def test_r1_a_trimmed_or_shifted_copy_of_the_events_is_still_governed() -> None:
    # Dropping the first 30 bars changes the dataset hash, but the bars still overlap the
    # registered range of the same stream, so the registered plan still governs them.
    tail = holdout_market()[30:]
    with pytest.raises(ExitInputError, match="registered_split_requires_split_run"):
        run([HOLDOUT_PLAN], tail, manifest_plan=None, reg=registry(plan60()))
    only_holdout = holdout_market()[41:]
    with pytest.raises(ExitInputError, match="registered_split_requires_split_run"):
        run([HOLDOUT_PLAN], only_holdout, manifest_plan=None, reg=registry(plan60()))


def test_r1_registry_must_match_the_independent_record() -> None:
    pinned = registry(plan60()).registry_hash
    # A caller who swaps in an empty registry must also forge the pinned hash.
    with pytest.raises(ExitInputError, match="registry_hash_mismatch"):
        run(
            [HOLDOUT_PLAN],
            holdout_market(),
            manifest_plan=None,
            reg=SplitRegistry(),
            expected_registry_hash=pinned,
        )


def test_r1_manifest_plan_without_a_registry_entry_cannot_be_split() -> None:
    p = plan60()
    with pytest.raises(ExitInputError, match="split_not_in_registry"):
        run(
            [HOLDOUT_PLAN],
            holdout_market(),
            manifest_plan=p,
            reg=SplitRegistry(),
            split=p,
            segment="validation",
        )


def test_r1_unregistered_stream_or_range_runs_unsplit_and_is_labelled() -> None:
    other_stream = registry(plan60(), source="another-feed")
    result = run([HOLDOUT_PLAN], holdout_market(), manifest_plan=None, reg=other_stream)
    assert result.segment == "unsplit"
    assert "unsplit_run_not_valid_out_of_sample" in result.notes
    later = SplitPlan(T0 + BAR * 100, T0 + BAR * 120, T0 + BAR * 140, T0 + BAR * 160)
    assert run([HOLDOUT_PLAN], holdout_market(), manifest_plan=None, reg=registry(later)).trades


def test_r1_registered_holdout_still_needs_unlock_and_is_recorded() -> None:
    p = plan60()
    log = HoldoutAccessLog()
    result = run(
        [HOLDOUT_PLAN],
        holdout_market(),
        manifest_plan=p,
        reg=registry(p),
        split=p,
        segment="holdout",
        unlock=HoldoutUnlock(p.plan_hash, "rin", "final check"),
        access_log=log,
    )
    assert result.segment == "holdout" and len(log.entries) == 1
    assert not result.statistical_evidence_eligible


def test_r1_run_records_and_hashes_the_registry() -> None:
    p = plan60()
    later = SplitPlan(T0 + BAR * 100, T0 + BAR * 120, T0 + BAR * 140, T0 + BAR * 160)
    one = registry(p)
    two = registry(p, later)
    plan = TradePlan("v", "long", T0 + BAR * 25, D("98"), D("104"))
    r1 = run([plan], holdout_market(), manifest_plan=p, reg=one, split=p, segment="validation")
    r2 = run([plan], holdout_market(), manifest_plan=p, reg=two, split=p, segment="validation")
    assert r1.registry_hash == one.registry_hash and r2.registry_hash == two.registry_hash
    assert r1.run_id != r2.run_id


def test_r1_registrations_for_one_stream_cannot_overlap_or_be_replaced() -> None:
    p = plan60()
    moved = SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 58, T0 + BAR * 60)
    with pytest.raises(RegistrationError, match="overlapping_registrations"):
        registry(p).register(SplitRegistration("synthetic-test", "SYNTH", moved, "x", "y"))
    # Adjacent ranges and other streams are fine.
    after = SplitPlan(T0 + BAR * 60, T0 + BAR * 70, T0 + BAR * 80, T0 + BAR * 90)
    registry(p, after)
    registry(p).register(SplitRegistration("another-feed", "SYNTH", p, "x", "y"))


def test_r1_a_dataset_spanning_two_registrations_is_refused() -> None:
    first = SplitPlan(T0, T0 + BAR * 10, T0 + BAR * 20, T0 + BAR * 30)
    second = SplitPlan(T0 + BAR * 30, T0 + BAR * 40, T0 + BAR * 50, T0 + BAR * 60)
    with pytest.raises(ExitInputError, match="dataset_spans_multiple_registrations"):
        run([HOLDOUT_PLAN], holdout_market(), manifest_plan=None, reg=registry(first, second))


@pytest.mark.parametrize(
    ("source", "symbol", "by", "reason"),
    [("", "S", "a", "b"), ("s", " ", "a", "b"), ("s", "S", "", "b"), ("s", "S", "a", "")],
)
def test_r1_registration_fields_are_required(
    source: str, symbol: str, by: str, reason: str
) -> None:
    with pytest.raises(RegistrationError, match="invalid_registration_field"):
        SplitRegistration(source, symbol, plan60(), by, reason)


def test_r1_registry_round_trips_through_reviewable_json() -> None:
    p = plan60()
    later = SplitPlan(T0 + BAR * 100, T0 + BAR * 120, T0 + BAR * 140, T0 + BAR * 160, BAR)
    reg = registry(later, p)
    text = reg.to_json()
    back = SplitRegistry.from_json(text)
    assert back.registry_hash == reg.registry_hash
    assert registry(p, later).registry_hash == reg.registry_hash  # order-independent
    assert back.to_json() == text


def test_r1_edited_json_boundary_is_detected() -> None:
    text = registry(plan60()).to_json()
    edited = text.replace("2026-01-05T03:20:00+00:00", "2026-01-05T04:50:00+00:00")
    assert edited != text
    with pytest.raises(RegistrationError, match="registry_plan_hash_mismatch"):
        SplitRegistry.from_json(edited)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"schema": "other", "entries": []}',
        '{"schema": "nexora.edge.split-registry/v1"}',
        '{"schema": "nexora.edge.split-registry/v1", "entries": [{"source": "s"}]}',
    ],
)
def test_r1_malformed_registry_json_fails_closed(text: str) -> None:
    with pytest.raises(RegistrationError):
        SplitRegistry.from_json(text)


def test_r1_registry_rejects_non_registration_entries() -> None:
    bad: Any = (plan60(),)
    with pytest.raises(RegistrationError, match="invalid_registry"):
        SplitRegistry(bad)
    with pytest.raises(ExitInputError, match="invalid_split_registry"):
        manifest, digest = synthetic_manifest(holdout_market())
        fake: Any = {"entries": ()}
        run_exit_mode(
            [HOLDOUT_PLAN],
            holdout_market(),
            manifest=manifest,
            expected_manifest_hash=digest,
            registry=fake,
            expected_registry_hash="0" * 64,
            policy=OVERLAP,
            cost_model=ZERO,
            unit_size=D("1"),
        )


# ---- R-3: ohlc_basis is validated at build time ------------------------------------------------


def test_r3_invalid_ohlc_basis_is_refused_when_building_the_manifest() -> None:
    # Previously (probe P13f): "xyz" stored and hashed; refused only when a run started.
    bad: Any = "xyz"
    with pytest.raises(EdgeDatasetError, match="invalid_ohlc_basis"):
        build_edge_manifest(flat(5), replace(PROV, ohlc_basis=bad), max_latency=LAT)


def test_r3_forged_basis_in_a_built_manifest_fails_verification() -> None:
    manifest, _ = synthetic_manifest(flat(5))
    bad: Any = "BID"
    forged = replace(manifest, provenance=replace(manifest.provenance, ohlc_basis=bad))
    with pytest.raises(EdgeDatasetError, match="invalid_ohlc_basis"):
        verify_edge_dataset(forged, flat(5), expected_manifest_hash=forged.manifest_hash)


# ---- R-4: overlapping bars in the audit ----------------------------------------------------


def overlapping() -> tuple[NormalizedPriceEvent, ...]:
    half = timedelta(seconds=150)
    return tuple(
        replace(b, event_time=T0 + half * i, received_at=T0 + half * i + BAR)
        for i, b in enumerate(flat(4))
    )


def test_r4_audit_flags_bars_closer_than_the_declared_interval() -> None:
    # Previously (probes P4/P4b): empty quality report, "complete" manifest.
    assert audit_events(overlapping(), max_latency=LAT).count("bar_overlap") == 0
    report = audit_events(overlapping(), max_latency=LAT, bar_interval=BAR)
    assert report.count("bar_overlap") == 3
    with pytest.raises(EdgeDatasetError, match="bar_overlap"):
        build_edge_manifest(overlapping(), PROV, max_latency=LAT, bar_interval=BAR)
    with pytest.raises(EdgeDatasetError, match="issue_not_acceptable"):
        build_edge_manifest(
            overlapping(),
            PROV,
            max_latency=LAT,
            bar_interval=BAR,
            accepted_issues=frozenset({"bar_overlap"}),
        )


def test_r4_bar_interval_is_hashed_and_must_match_the_run_policy() -> None:
    bars = flat(5)
    checked = build_edge_manifest(bars, PROV, max_latency=LAT, bar_interval=BAR)
    unchecked = build_edge_manifest(bars, PROV, max_latency=LAT)
    assert checked.manifest_hash != unchecked.manifest_hash
    verify_edge_dataset(checked, bars, expected_manifest_hash=checked.manifest_hash)
    with pytest.raises(EdgeDatasetError, match="invalid_policy_duration"):
        build_edge_manifest(bars, PROV, max_latency=LAT, bar_interval=timedelta(0))
    plan = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    with pytest.raises(ExitInputError, match="bar_interval_mismatch"):
        run_exit_mode(
            [plan],
            bars,
            manifest=checked,
            expected_manifest_hash=checked.manifest_hash,
            **registry_args(),
            policy=ExitPolicy("r4", "allow_overlap", BAR * 2),
            cost_model=ZERO,
            unit_size=D("1"),
        )


# ---- R-5: integrity can no longer be asserted -------------------------------------------------


def test_r5_integrity_gate_needs_a_receipt_from_verification() -> None:
    call: Any = evaluate_gates
    with pytest.raises(TypeError):
        call(dataset_integrity_verified=True)  # previously: integrity True without checking
    with pytest.raises(EvidenceGateError, match="integrity_receipt_not_issued_by_verification"):
        IntegrityReceipt("0" * 64)
    with pytest.raises(EvidenceGateError, match="invalid_integrity_receipt"):
        call(integrity="0" * 64)
    assert not evaluate_gates().dataset_integrity_verified
    bars = flat(3)
    manifest, digest = synthetic_manifest(bars)
    receipt = verify_edge_dataset(manifest, bars, expected_manifest_hash=digest)
    assert receipt.manifest_hash == digest
    gates = evaluate_gates(integrity=receipt)
    assert gates.dataset_integrity_verified and not gates.statistical_evidence_eligible
