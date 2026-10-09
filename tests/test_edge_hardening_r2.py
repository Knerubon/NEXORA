"""ENGINEERING regression tests for PR #79 Hardening Round 2.

Each group reproduces a defect from the independent review and asserts it is now prevented.
Inputs are synthetic; nothing here is evidence about trading profitability.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from nexora.artifacts import canonical_hash
from nexora.backtest.datasets import manifest_for
from nexora.edge import (
    KNOWN_ZERO,
    SEGMENTS,
    CostComponent,
    CostModel,
    EdgeDatasetError,
    EdgeDatasetManifest,
    EvidenceGates,
    ExitInputError,
    ExitPolicy,
    HoldoutAccessLog,
    HoldoutUnlock,
    Provenance,
    SplitError,
    SplitPlan,
    TradePlan,
    audit_events,
    build_edge_manifest,
    evaluate_gates,
    plan_from_signal,
    run_exit_mode,
    select_completed_segment,
    select_segment,
    simulate_exit,
    validate_segment,
    verify_edge_dataset,
    walk_forward_windows,
)
from nexora.edge.evidence import EvidenceGateError
from nexora.market_data.models import NormalizedPriceEvent
from nexora.signals import ResearchSignal
from nexora.signals.models import SignalDecision, SignalTarget

from tests.edge_fixtures import BAR, T0, flat_bars, make_bar, synthetic_manifest

D = Decimal
LAT = BAR + timedelta(seconds=30)
ZERO = CostModel("t", "XXX", KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO)
ONE = ExitPolicy("r2", "one_position", BAR)
OVERLAP = ExitPolicy("r2", "allow_overlap", BAR)
ENTRY = ("100", "101", "99.5", "100.5")
PLAN = TradePlan("a", "long", T0 + BAR, D("98"), D("104"))
SYN = Provenance("synthetic_fixture", "synthetic-test", T0, "UTC", "v")


def split() -> SplitPlan:
    return SplitPlan(T0, T0 + BAR * 10, T0 + BAR * 20, T0 + BAR * 30)


def bars30() -> tuple[NormalizedPriceEvent, ...]:
    return tuple(make_bar(i, "100", "100.5", "99.8", "100") for i in range(30))


def bars_after_quiet(*specs: tuple[str, str, str, str]) -> tuple[NormalizedPriceEvent, ...]:
    out = [make_bar(0, "100", "100", "100", "100")]
    out.extend(make_bar(i, *s) for i, s in enumerate(specs, start=1))
    return tuple(out)


def run(
    plans: Any,
    bars: tuple[NormalizedPriceEvent, ...],
    policy: ExitPolicy,
    *,
    max_latency: timedelta = LAT,
    **kwargs: Any,
) -> Any:
    manifest, digest = synthetic_manifest(bars, max_latency=max_latency)
    return run_exit_mode(
        plans,
        bars,
        manifest=manifest,
        expected_manifest_hash=digest,
        policy=policy,
        cost_model=ZERO,
        unit_size=D("1"),
        **kwargs,
    )


# ======================= P0: holdout segment bypass ====================================


class SneakyStr(str):
    """A str subclass that claims to equal anything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return 0


def malformed_segments() -> list[Any]:
    names = [
        variant
        for base in SEGMENTS
        for variant in (
            base.upper(),
            base.title(),
            f" {base}",
            f"{base} ",
            f"{base}\n",
            f"{base}\x00",
            base[:-1],
            f"{base}2",
        )
    ]
    return [*names, "", " ", "oos", "test", "out_of_sample", "all", None, 0, b"holdout"] + [
        ("holdout",),
        SneakyStr("holdout"),
    ]


@pytest.mark.parametrize("bad", malformed_segments(), ids=repr)
def test_p0_every_entry_point_rejects_a_segment_outside_the_closed_vocabulary(bad: Any) -> None:
    p = split()
    items = list(range(30))
    times = [T0 + BAR * i for i in items]
    log = HoldoutAccessLog()
    unlock = HoldoutUnlock(p.plan_hash, "a", "r")
    with pytest.raises(SplitError, match="invalid_segment"):
        validate_segment(bad)
    with pytest.raises(SplitError, match="invalid_segment"):
        p.bounds(bad)
    with pytest.raises(SplitError, match="invalid_segment"):
        select_segment(p, items, times, bad, unlock=unlock, access_log=log)
    with pytest.raises(SplitError, match="invalid_segment"):
        select_completed_segment(p, items, times, times, bad, unlock=unlock, access_log=log)
    bars = bars30()
    with pytest.raises((SplitError, ExitInputError)):
        run([PLAN], bars, OVERLAP, split=p, segment=bad, unlock=unlock, access_log=log)
    assert log.entries == ()  # nothing was read, nothing was recorded


def test_p0_original_bypass_values_no_longer_return_holdout_data() -> None:
    """Before the fix 'Holdout', 'oos', '' and 'holdout ' all returned the holdout rows with
    no unlock and no log, because bounds() treated every unknown name as the holdout."""
    p = split()
    items = list(range(30))
    times = [T0 + BAR * i for i in items]
    for bad in ("Holdout", "oos", "", "holdout "):
        with pytest.raises(SplitError, match="invalid_segment"):
            select_segment(p, items, times, bad)


def test_p0_empty_string_segment_is_not_treated_as_unsplit() -> None:
    with pytest.raises(ExitInputError, match="split_and_segment"):
        run([PLAN], bars30(), OVERLAP, segment="")
    with pytest.raises(SplitError, match="invalid_segment"):
        run([PLAN], bars30(), OVERLAP, split=split(), segment="")


def test_p0_exactly_the_three_names_are_valid_and_holdout_stays_locked() -> None:
    assert [validate_segment(s) for s in SEGMENTS] == list(SEGMENTS)
    p = split()
    assert p.bounds("train")[0] == T0
    with pytest.raises(Exception, match="holdout_locked"):
        select_segment(p, [1], [T0 + BAR * 25], "holdout")


# ======================= P0: dataset policy verification ===============================


def prov_real() -> Provenance:
    return Provenance("real_market", "adapter-feed", T0, "UTC", "v", capability_profile_ref="p:1")


def forge(
    events: tuple[NormalizedPriceEvent, ...],
    *,
    accepted: tuple[str, ...] = (),
    status: str = "complete",
    provenance: Provenance = SYN,
    max_latency: timedelta = LAT,
    gap: timedelta | None = BAR,
    **overrides: Any,
) -> EdgeDatasetManifest:
    """What an attacker (or a bug) could write by hand: a manifest whose hashes and quality
    report are all internally consistent with the data, but whose policy fields are wrong."""
    base = manifest_for(events, quality=status)
    manifest = EdgeDatasetManifest(
        base=base,
        base_hash=canonical_hash(base),
        provenance=provenance,
        quality=audit_events(events, max_latency=max_latency, expected_gap=gap),
        max_latency_us=max_latency // timedelta(microseconds=1),
        expected_gap_us=None if gap is None else gap // timedelta(microseconds=1),
        accepted_issues=accepted,
    )
    return replace(manifest, **overrides) if overrides else manifest


def gappy() -> tuple[NormalizedPriceEvent, ...]:
    return (*flat_bars(3), make_bar(7, "100", "100", "100", "100", seq=3))


def check(manifest: EdgeDatasetManifest, events: tuple[NormalizedPriceEvent, ...]) -> None:
    verify_edge_dataset(manifest, events, expected_manifest_hash=manifest.manifest_hash)


def test_p0_forged_manifest_over_dirty_data_with_no_acceptance_now_fails() -> None:
    """Before the fix this verified: quality == audit(events) was the only policy check, so a
    consistent manifest over data with a gap and NO accepted-issue declaration passed."""
    events = gappy()
    with pytest.raises(EdgeDatasetError, match="dataset_quality_blocking"):
        check(forge(events), events)


def test_p0_missing_acceptance_declaration_fails_but_a_declared_one_passes() -> None:
    events = gappy()
    check(forge(events, accepted=("gap",), status="partial"), events)  # properly declared
    with pytest.raises(EdgeDatasetError, match="dataset_quality_blocking"):
        check(forge(events, accepted=(), status="complete"), events)


def test_p0_acceptance_of_an_absent_issue_is_rejected_at_build_and_verify() -> None:
    clean = flat_bars(4)
    with pytest.raises(EdgeDatasetError, match="accepted_issue_not_present"):
        build_edge_manifest(clean, SYN, max_latency=LAT, accepted_issues=frozenset({"gap"}))
    with pytest.raises(EdgeDatasetError, match="accepted_issue_not_present"):
        check(forge(clean, accepted=("gap",), status="partial"), clean)


@pytest.mark.parametrize(
    "code",
    ["duplicate", "out_of_order", "crossed_quote", "non_finite_value", "invalid_ohlc"],
)
def test_p0_corruption_can_never_be_accepted(code: str) -> None:
    with pytest.raises(EdgeDatasetError, match="issue_not_acceptable"):
        build_edge_manifest(flat_bars(4), SYN, max_latency=LAT, accepted_issues=frozenset({code}))
    events = flat_bars(4)
    with pytest.raises(EdgeDatasetError, match="issue_not_acceptable"):
        check(forge(events, accepted=(code,), status="partial"), events)


def test_p0_invalid_accepted_issue_state_is_rejected() -> None:
    events = gappy()
    with pytest.raises(EdgeDatasetError, match="invalid_accepted_issues_state"):
        check(forge(events, accepted=("gap", "gap"), status="partial"), events)
    with pytest.raises(EdgeDatasetError, match="invalid_accepted_issues_state"):
        check(forge(events, accepted=("missing_sequence", "gap"), status="partial"), events)


def test_p0_quality_status_must_match_the_acceptance_state() -> None:
    events = gappy()
    with pytest.raises(EdgeDatasetError, match="quality_status_mismatch"):
        check(forge(events, accepted=("gap",), status="complete"), events)
    clean = flat_bars(4)
    with pytest.raises(EdgeDatasetError, match="quality_status_mismatch"):
        check(forge(clean, accepted=(), status="partial"), clean)


def test_p0_policy_durations_must_be_positive() -> None:
    clean = flat_bars(4)
    with pytest.raises(EdgeDatasetError, match="invalid_policy_duration"):
        build_edge_manifest(clean, SYN, max_latency=timedelta(0))
    with pytest.raises(EdgeDatasetError, match="invalid_policy_duration"):
        check(forge(clean, max_latency_us=-1), clean)
    with pytest.raises(EdgeDatasetError, match="invalid_policy_duration"):
        check(forge(clean, expected_gap_us=0), clean)


def test_p0_policy_is_reapplied_to_provenance() -> None:
    clean = flat_bars(4)
    with pytest.raises(EdgeDatasetError, match="non_utc_timezone"):
        check(forge(clean, provenance=replace(SYN, timezone="Asia/Bangkok")), clean)
    # real_market without an adapter reference, or over synthetic events, cannot verify either
    with pytest.raises(EdgeDatasetError, match="real_market_requires_capability_ref"):
        check(
            forge(clean, provenance=replace(prov_real(), capability_profile_ref=None)),
            clean,
        )
    with pytest.raises(EdgeDatasetError, match="synthetic_marker_in_real_market_data"):
        check(forge(clean, provenance=prov_real()), clean)


def test_p0_tampered_quality_report_still_fails() -> None:
    events = gappy()
    clean_report = audit_events(flat_bars(4), max_latency=LAT, expected_gap=BAR)
    with pytest.raises(EdgeDatasetError):
        check(forge(events, accepted=("gap",), status="partial", quality=clean_report), events)


def test_p0_subsecond_policy_durations_round_trip_exactly() -> None:
    """Whole-second truncation used to re-audit with a stricter limit than the build used."""
    events = tuple(
        make_bar(i, "100", "100", "100", "100", latency=timedelta(seconds=30, milliseconds=200))
        for i in range(4)
    )
    lat = BAR + timedelta(seconds=30, milliseconds=500)
    manifest = build_edge_manifest(events, SYN, max_latency=lat)
    assert manifest.max_latency_us == lat // timedelta(microseconds=1)
    check(manifest, events)


@pytest.mark.parametrize(
    "bar,code",
    [
        (make_bar(1, "100", "99", "98", "99"), "invalid_ohlc"),  # open above high
        (make_bar(1, "100", "101", "100.5", "100.6"), "invalid_ohlc"),  # open below low
        (make_bar(1, "100", "101", "99", "102"), "invalid_ohlc"),  # close above high
        (make_bar(1, "100", "99", "101", "100"), "invalid_ohlc"),  # high below low
        (make_bar(1, "0", "100", "0", "100"), "non_positive_price"),
        (make_bar(1, "100", "100", "100", "100", bid="-1", ask="100"), "non_positive_price"),
        (make_bar(1, "100", "100", "100", "100", bid="101", ask="100"), "crossed_quote"),
    ],
)
def test_p1_inconsistent_ohlc_and_bad_quotes_block_the_dataset(
    bar: NormalizedPriceEvent, code: str
) -> None:
    """Before the fix the first four built a manifest and only failed later inside a run."""
    events = (make_bar(0, "100", "100", "100", "100"), bar)
    assert code in audit_events(events, max_latency=LAT).blocking()
    with pytest.raises(EdgeDatasetError, match="dataset_quality_blocking"):
        build_edge_manifest(events, SYN, max_latency=LAT)


def test_p1_bar_missing_ohlc_fields_is_invalid() -> None:
    bad = replace(make_bar(1, "100", "100", "100", "100"), high=None)
    assert "invalid_ohlc" in audit_events((flat_bars(1)[0], bad), max_latency=LAT).blocking()


# ======================= P0: evidence authorization ====================================


def test_p0_gates_are_four_separate_facts_and_only_integrity_can_be_true() -> None:
    gates = evaluate_gates(dataset_integrity_verified=True)
    assert gates.dataset_integrity_verified
    assert not gates.provenance_independently_verified
    assert not gates.holdout_evaluation_authorized
    assert not gates.statistical_evidence_eligible
    assert "no_independent_provenance_verification_mechanism" in gates.blockers
    assert "no_independent_holdout_authorization_mechanism" in gates.blockers
    unverified = evaluate_gates(dataset_integrity_verified=False)
    assert "dataset_integrity_not_verified" in unverified.blockers


def test_p0_gate_function_takes_no_caller_supplied_proof() -> None:
    call: Any = evaluate_gates
    for kwarg in ("provenance_independently_verified", "holdout_evaluation_authorized"):
        with pytest.raises(TypeError):
            call(dataset_integrity_verified=True, **{kwarg: True})


def test_p0_inconsistent_gate_objects_cannot_exist() -> None:
    with pytest.raises(EvidenceGateError):
        EvidenceGates(True, False, False, True, ())  # eligible without the other gates
    with pytest.raises(EvidenceGateError):
        EvidenceGates(True, True, True, False, ())  # all gates but not eligible
    with pytest.raises(EvidenceGateError):
        EvidenceGates(True, True, True, True, ("x",))  # eligible while blockers remain


def test_p0_real_market_label_adapter_ref_unlock_and_log_cannot_make_a_run_eligible() -> None:
    bars = tuple(
        replace(b, source="adapter-feed", identity_key=f"feed:{i}", source_event_id=f"feed:{i}")
        for i, b in enumerate(bars30())
    )
    manifest = build_edge_manifest(bars, prov_real(), max_latency=LAT)
    p = split()
    log = HoldoutAccessLog()
    plan = TradePlan("h", "long", T0 + BAR * 21, D("98"), D("104"))
    result = run_exit_mode(
        [plan],
        bars,
        manifest=manifest,
        expected_manifest_hash=manifest.manifest_hash,
        policy=OVERLAP,
        cost_model=ZERO,
        unit_size=D("1"),
        split=p,
        segment="holdout",
        unlock=HoldoutUnlock(p.plan_hash, "someone", "a reason"),
        access_log=log,
    )
    assert manifest.declared_real_market and result.gates.dataset_integrity_verified
    assert not result.gates.provenance_independently_verified
    assert not result.gates.holdout_evaluation_authorized
    assert not result.statistical_evidence_eligible and not manifest.statistical_evidence_eligible
    assert result.holdout_access is not None
    assert result.holdout_access.authorization_verified is False
    assert log.entries[0].authorization_verified is False
    assert "statistical_evidence_fail_closed" in result.notes


@pytest.mark.parametrize("provenance", [SYN, prov_real()])
def test_p0_eligibility_is_false_for_every_data_class(provenance: Provenance) -> None:
    events = flat_bars(4)
    if provenance.data_class == "real_market":
        events = tuple(
            replace(e, source="adapter-feed", identity_key=f"f:{i}", source_event_id=f"f:{i}")
            for i, e in enumerate(events)
        )
    manifest = build_edge_manifest(events, provenance, max_latency=LAT)
    assert manifest.statistical_evidence_eligible is False


# ======================= P1 ============================================================


def test_p1_subsecond_purge_values_no_longer_collide_in_the_plan_hash() -> None:
    def h(purge: timedelta) -> str:
        return SplitPlan(T0, T0 + BAR * 10, T0 + BAR * 20, T0 + BAR * 30, purge).plan_hash

    assert h(timedelta(milliseconds=100)) != h(timedelta(milliseconds=900))
    assert h(timedelta(microseconds=1)) != h(timedelta(microseconds=2))
    assert h(timedelta(0)) != h(timedelta(microseconds=1))
    assert h(timedelta(seconds=1)) == h(timedelta(milliseconds=1000))


def test_p1_unlock_for_a_different_subsecond_purge_is_rejected() -> None:
    a = SplitPlan(T0, T0 + BAR * 10, T0 + BAR * 20, T0 + BAR * 30, timedelta(milliseconds=100))
    b = SplitPlan(T0, T0 + BAR * 10, T0 + BAR * 20, T0 + BAR * 30, timedelta(milliseconds=900))
    with pytest.raises(Exception, match="holdout_locked"):
        select_segment(
            b,
            [1],
            [T0 + BAR * 25],
            "holdout",
            unlock=HoldoutUnlock(a.plan_hash, "a", "r"),
            access_log=HoldoutAccessLog(),
        )


def test_p1_exit_policy_subsecond_fields_change_the_identity() -> None:
    a = ExitPolicy("v", "allow_overlap", BAR, entry_delay=timedelta(milliseconds=100))
    b = ExitPolicy("v", "allow_overlap", BAR, entry_delay=timedelta(milliseconds=900))
    assert a.hash_payload() != b.hash_payload()
    c = ExitPolicy("v", "allow_overlap", BAR + timedelta(microseconds=1))
    assert c.hash_payload() != ExitPolicy("v", "allow_overlap", BAR).hash_payload()


def test_p1_a_bar_straddling_a_boundary_belongs_to_neither_segment() -> None:
    p = SplitPlan(
        T0, T0 + BAR * 10 - timedelta(minutes=2, seconds=30), T0 + BAR * 20, T0 + BAR * 30
    )
    straddler = make_bar(9, "100", "100", "100", "100")  # opens in train, closes in validation
    starts, ends = [straddler.event_time], [straddler.received_at]
    assert p.bounds("train")[1] < straddler.received_at
    assert select_completed_segment(p, ["s"], starts, ends, "train") == ()
    assert select_completed_segment(p, ["s"], starts, ends, "validation") == ()
    # The point-in-time API, by contrast, files it under train by its start alone.
    assert select_segment(p, ["s"], starts, "train") == ("s",)


def test_p1_a_late_arriving_bar_does_not_belong_to_the_segment_it_opened_in() -> None:
    p = split()
    late = make_bar(9, "100", "100", "100", "100", latency=timedelta(seconds=30))
    on_time = make_bar(8, "100", "100", "100", "100")
    got = select_completed_segment(
        p,
        ["late", "on_time"],
        [late.event_time, on_time.event_time],
        [late.received_at, on_time.received_at],
        "train",
    )
    assert got == ("on_time",)


def test_p1_run_never_uses_a_bar_that_completes_after_the_segment() -> None:
    p = SplitPlan(
        T0, T0 + BAR * 10 - timedelta(minutes=2, seconds=30), T0 + BAR * 20, T0 + BAR * 30
    )
    base = [make_bar(i, "100", "100.4", "99.8", "100") for i in range(30)]
    plan = TradePlan("t", "long", T0 + BAR * 1, D("98"), D("104"))
    quiet = tuple(base)
    base[9] = make_bar(9, "100", "110", "99.8", "109")  # straddler hits the target
    spiked = tuple(base)
    a = run([plan], quiet, OVERLAP, split=p, segment="train")
    b = run([plan], spiked, OVERLAP, split=p, segment="train")
    assert a.trades == b.trades == () and b.unresolved[0].status == "open_at_end"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": "2026-01-01"},
        {"start": None},
        {"start": 5},
        {"start": date(2026, 1, 5)},
        {"train_end": "x"},
        {"validation_end": 1.5},
        {"end": b"x"},
        {"purge": 5},
        {"purge": None},
        {"purge": 1.5},
        {"purge": "1s"},
        {"start": datetime(2026, 1, 5)},
    ],
    ids=repr,
)
def test_p1_malformed_split_plan_inputs_raise_split_error_not_attribute_or_type_error(
    kwargs: dict[str, Any],
) -> None:
    args: dict[str, Any] = {
        "start": T0,
        "train_end": T0 + BAR * 10,
        "validation_end": T0 + BAR * 20,
        "end": T0 + BAR * 30,
        "purge": timedelta(0),
    }
    args.update(kwargs)
    with pytest.raises(SplitError):
        SplitPlan(**args)


def test_p1_malformed_walk_forward_inputs_raise_split_error() -> None:
    call: Any = walk_forward_windows
    with pytest.raises(SplitError):
        call("plan", train=BAR, test=BAR)
    with pytest.raises(SplitError):
        call(split(), train=5, test=BAR)
    with pytest.raises(SplitError):
        call(split(), train=BAR, test=BAR, step=3)
    with pytest.raises(SplitError):
        call(split(), train=BAR, test=BAR, purge="x")


@pytest.mark.parametrize(
    "bar",
    [
        make_bar(1, "100", "101", "99", "100", bid="NaN", ask="100"),
        make_bar(1, "100", "101", "99", "100", bid="99", ask="Infinity"),
        make_bar(1, "100", "101", "99", "100", bid="0", ask="100"),
        make_bar(1, "100", "101", "99", "100", bid="99", ask="-3"),
    ],
    ids=["nan-bid", "inf-ask", "zero-bid", "negative-ask"],
)
def test_p1_non_finite_or_non_positive_quotes_are_rejected_by_the_exit_mode(
    bar: NormalizedPriceEvent,
) -> None:
    with pytest.raises(ExitInputError, match="invalid_quote"):
        simulate_exit(PLAN, (make_bar(0, "100", "100", "100", "100"), bar), OVERLAP)


def test_p1_ohlc_inconsistency_and_incomplete_bars_are_rejected_by_the_exit_mode() -> None:
    with pytest.raises(ExitInputError, match="invalid_ohlc"):
        simulate_exit(PLAN, bars_after_quiet(("100", "99", "98", "99")), OVERLAP)
    early = replace(make_bar(1, *ENTRY), received_at=T0 + BAR)  # arrives before it closes
    with pytest.raises(ExitInputError, match="bar_received_before_close"):
        simulate_exit(PLAN, (make_bar(0, "100", "100", "100", "100"), early), OVERLAP)


def test_p1_a_crossed_quote_is_still_tolerated_as_unavailable_spread() -> None:
    crossed = make_bar(1, "100", "104.5", "100", "104", bid="101", ask="100")
    result = simulate_exit(PLAN, (make_bar(0, "100", "100", "100", "100"), crossed), OVERLAP)
    assert result.status == "closed" and result.entry_spread is None


@pytest.mark.parametrize("side", ["LONG", "Long", "buy", "SELL", "short ", "", None, 1])
def test_p1_invalid_trade_plan_side_is_rejected_not_silently_treated_as_short(side: Any) -> None:
    plan = replace(PLAN, side=side)
    with pytest.raises(ExitInputError, match="invalid_side"):
        simulate_exit(plan, bars_after_quiet(ENTRY), OVERLAP)


def make_signal(action: str, side: str) -> ResearchSignal:
    decision = SignalDecision(
        action=action,  # type: ignore[arg-type]
        score=70,
        entry_zone=None,
        invalidation_price=D("98"),
        invalidation_reason=None,
        targets=(SignalTarget("TP1", D("103"), "rr"),),
        risk_reward=None,
        patterns=(),
        positive_evidence=(),
        negative_evidence=(),
        future_conditions=(),
        config_version="t",
        engine_version="t",
        source_refs=(),
    )
    return ResearchSignal(
        signal_id="sig",
        symbol="SYNTH",
        side=side,  # type: ignore[arg-type]
        sequence=1,
        occurrence_time=T0,
        confirmation_time=T0 + BAR,
        decision_time=T0 + BAR,
        reasons=(),
        reason_codes=(),
        source_refs=("ref",),
        config_version="t",
        engine_versions=("t",),
        status="active",
        decision=decision,
    )


def test_p1_signal_action_and_side_must_agree() -> None:
    assert plan_from_signal(make_signal("BUY", "long"), target_name="TP1").side == "long"
    assert plan_from_signal(make_signal("SELL", "short"), target_name="TP1").side == "short"
    with pytest.raises(ExitInputError, match="signal_action_side_mismatch"):
        plan_from_signal(make_signal("BUY", "short"), target_name="TP1")
    with pytest.raises(ExitInputError, match="signal_action_side_mismatch"):
        plan_from_signal(make_signal("SELL", "long"), target_name="TP1")
    with pytest.raises(ExitInputError, match="signal_not_actionable"):
        plan_from_signal(make_signal("WAIT", "long"), target_name="TP1")


def one_shot(items: list[Any]) -> Iterator[Any]:
    return iter(items)


def test_p1_one_shot_plan_iterator_gives_the_same_result_as_a_list() -> None:
    """Before the fix a generator was consumed by the id check and the run silently booked
    zero trades."""
    bars = bars_after_quiet(ENTRY, ("100.5", "104.5", "100", "104"))
    from_list = run([PLAN], bars, OVERLAP)
    from_iter = run(one_shot([PLAN]), bars, OVERLAP)
    assert len(from_iter.trades) == 1
    assert from_iter == from_list


def test_p1_one_shot_iterators_work_everywhere_else_too() -> None:
    events = flat_bars(5)
    assert audit_events(one_shot(list(events)), max_latency=LAT).total == 5  # type: ignore[arg-type]
    manifest = build_edge_manifest(one_shot(list(events)), SYN, max_latency=LAT)  # type: ignore[arg-type]
    verify: Any = verify_edge_dataset
    verify(manifest, one_shot(list(events)), expected_manifest_hash=manifest.manifest_hash)
    p = split()
    items = list(range(30))
    times = [T0 + BAR * i for i in items]
    select: Any = select_segment
    assert select(p, one_shot(items), one_shot(times), "train") == tuple(range(10))


def test_p1_feed_latency_does_not_cause_false_overlap() -> None:
    """Before the fix a trade's end was the bar's ARRIVAL time, so the next trade, entering at
    the bar's actual close, looked overlapping and was wrongly skipped."""
    late = timedelta(minutes=4)
    bars = (
        make_bar(0, "100", "100", "100", "100"),
        make_bar(1, *ENTRY),
        make_bar(2, "100.5", "101", "100", "100.8"),
        make_bar(3, "100.8", "104.5", "100.5", "104", latency=late),  # target bar, arrives late
        make_bar(4, "100", "101", "99.5", "100.5"),
        make_bar(5, "100.5", "104.5", "100", "104"),
    )
    a = TradePlan("a", "long", T0 + BAR * 1, D("98"), D("104"))
    c = TradePlan("c", "long", T0 + BAR * 4, D("98"), D("104"))
    result = run([a, c], bars, ONE, max_latency=BAR + late + timedelta(seconds=30))
    assert [t.exit.signal_id for t in result.trades] == ["a", "c"]
    assert result.skipped_overlap_count == 0
    first = result.trades[0].exit
    assert first.exit_time == T0 + BAR * 4  # the bar CLOSE, not its later arrival
    assert first.exit_time < bars[3].received_at


def test_p1_holding_time_and_swap_use_the_bar_close_not_the_arrival_time() -> None:
    late = timedelta(minutes=4)
    bars = (
        make_bar(0, "100", "100", "100", "100"),
        make_bar(1, *ENTRY),
        make_bar(2, "100.5", "104.5", "100", "104", latency=late),
    )
    model = CostModel(
        "t", "XXX", KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, CostComponent("known", D("86400"))
    )
    manifest, digest = synthetic_manifest(bars, max_latency=BAR + late + timedelta(seconds=30))
    result = run_exit_mode(
        [PLAN],
        bars,
        manifest=manifest,
        expected_manifest_hash=digest,
        policy=OVERLAP,
        cost_model=model,
        unit_size=D("1"),
    )
    # entry = bar 1 open (T0+5m), exit = bar 2 close (T0+15m): 600 s => swap 86400 * 600/86400
    assert dict(result.trades[0].costs.known)["swap"] == D("600")


def test_p1_same_time_signals_fail_closed_under_one_position_not_by_signal_id() -> None:
    bars = bars_after_quiet(ENTRY, ("100.5", "104.5", "100", "104"))
    buy = TradePlan("a-buy", "long", T0 + BAR, D("98"), D("104"))
    sell = TradePlan("b-sell", "short", T0 + BAR, D("102"), D("96"))
    with pytest.raises(ExitInputError, match="conflicting_same_time_signals"):
        run([buy, sell], bars, ONE)
    # Independent booking involves no tie-break, so it is allowed and says so.
    both = run([buy, sell], bars, OVERLAP)
    assert len(both.trades) == 2 and both.skipped_overlap_count == 0
    assert "overlapping_positions_may_stack_exposure" in both.notes


def test_quant_open_items_are_flagged_in_every_run() -> None:
    result = run([PLAN], bars_after_quiet(ENTRY), OVERLAP)
    assert "open_trades_at_segment_end_are_excluded_quant_decision_open" in result.notes
    assert "tp_selection_and_sizing_are_open_quant_decisions" in result.notes
    assert result.unresolved[0].status == "open_at_end"  # excluded, not guessed


def test_unaffected_ranges_still_work() -> None:
    p = split()
    assert p.bounds("holdout") == (T0 + BAR * 20, T0 + BAR * 30)
    assert datetime(2026, 1, 5, tzinfo=UTC) == T0
