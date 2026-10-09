"""ENGINEERING regression tests for PR #79 Hardening Round 1 (findings F1-F8).

Each group reproduces the ORIGINAL failure from the self-audit and asserts it is now
prevented. Inputs are synthetic; nothing here is evidence about trading profitability.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from nexora.edge import (
    KNOWN_ZERO,
    UNKNOWN,
    CostModel,
    EdgeDatasetError,
    ExitInputError,
    ExitPolicy,
    HoldoutAccessLog,
    HoldoutLocked,
    HoldoutUnlock,
    Provenance,
    SplitPlan,
    TradePlan,
    audit_events,
    build_edge_manifest,
    run_exit_mode,
    select_segment,
    simulate_exit,
    to_edge_trade,
    walk_forward_windows,
)
from nexora.market_data.models import NormalizedPriceEvent

from tests.edge_fixtures import BAR, T0, flat_bars, make_bar, synthetic_manifest

D = Decimal
LAT = BAR + timedelta(seconds=30)
DECISION = T0 + BAR
ZERO = CostModel("t", "XXX", KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO)
ONE = ExitPolicy("h1", "one_position", BAR)
OVERLAP = ExitPolicy("h1", "allow_overlap", BAR)
PLAN = TradePlan("a", "long", DECISION, D("98"), D("104"))
ENTRY = ("100", "101", "99.5", "100.5")


def bars_after_quiet(*specs: tuple[str, str, str, str]) -> tuple[NormalizedPriceEvent, ...]:
    out = [make_bar(0, "100", "100", "100", "100")]
    out.extend(make_bar(i, *s) for i, s in enumerate(specs, start=1))
    return tuple(out)


def run(
    plans: tuple[TradePlan, ...],
    bars: tuple[NormalizedPriceEvent, ...],
    policy: ExitPolicy,
    **kwargs: Any,
) -> Any:
    manifest, digest = synthetic_manifest(bars)
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


# ---- F1: walk-forward could reach the holdout -----------------------------------------


def split_plan() -> SplitPlan:
    return SplitPlan(T0, T0 + BAR * 60, T0 + BAR * 80, T0 + BAR * 100)


def test_f1_walk_forward_range_comes_from_the_plan_not_the_caller() -> None:
    p = split_plan()
    ws = walk_forward_windows(p, train=BAR * 30, test=BAR * 10)
    hold_low, _ = p.bounds("holdout")
    assert ws and max(w.test_end for w in ws) <= p.validation_end <= hold_low
    # Every timestamp any test window can contain lies outside the holdout.
    items = list(range(100))
    times = [T0 + BAR * i for i in items]
    holdout_ids = set(
        select_segment(p, items, times, "holdout", unlock=_unlock(p), access_log=HoldoutAccessLog())
    )
    for w in ws:
        in_test = {i for i, t in zip(items, times, strict=True) if w.test_start <= t < w.test_end}
        assert not in_test & holdout_ids


def test_f1_the_old_caller_supplied_end_argument_is_gone() -> None:
    call: Any = walk_forward_windows
    with pytest.raises(TypeError):
        call(T0, T0 + BAR * 100, train=BAR * 30, test=BAR * 10)


# ---- F2: NaN / naive timestamps crashed the audit -------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("bid", D("NaN")),
        ("ask", D("NaN")),
        ("bid", D("Infinity")),
        ("open_price", D("NaN")),
        ("high", D("Infinity")),
        ("price", D("NaN")),
    ],
)
def test_f2_non_finite_values_are_counted_and_block_the_dataset(field: str, value: Decimal) -> None:
    good = flat_bars(2)
    overrides: dict[str, Any] = {field: value}
    bad = replace(make_bar(2, "100", "100", "100", "100", bid="99", ask="101"), **overrides)
    report = audit_events([*good, bad], max_latency=LAT)  # must not raise
    assert report.count("non_finite_value") == 1
    assert "non_finite_value" in report.blocking()
    with pytest.raises(EdgeDatasetError, match="non_finite_value"):
        build_edge_manifest(
            (*good, bad),
            Provenance("synthetic_fixture", "synthetic-test", T0, "UTC", "v"),
            max_latency=LAT,
        )


def test_f2_naive_timestamp_followed_by_aware_one_does_not_crash() -> None:
    naive = replace(make_bar(0, "100", "100", "100", "100"), event_time=datetime(2026, 1, 5))
    report = audit_events([naive, make_bar(1, "100", "100", "100", "100")], max_latency=LAT)
    assert report.count("timestamp_anomaly") == 1


# ---- F3: run identity was not bound to the data ---------------------------------------


def test_f3_dataset_hash_argument_no_longer_exists() -> None:
    bars = bars_after_quiet(ENTRY)
    manifest, digest = synthetic_manifest(bars)
    call: Any = run_exit_mode
    with pytest.raises(TypeError):
        call(
            (PLAN,),
            bars,
            policy=OVERLAP,
            cost_model=ZERO,
            unit_size=D("1"),
            dataset_hash="whatever",
        )
    result = run((PLAN,), bars, OVERLAP)
    assert result.manifest_hash == manifest.manifest_hash == digest


def test_f3_bars_that_do_not_match_the_manifest_are_refused() -> None:
    bars = bars_after_quiet(ENTRY, ("100.5", "104.5", "100", "104"))
    manifest, digest = synthetic_manifest(bars)
    tampered = (*bars[:2], make_bar(2, "100.5", "110", "100", "109"))
    with pytest.raises(ExitInputError, match="dataset_verification_failed"):
        run_exit_mode(
            (PLAN,),
            tampered,
            manifest=manifest,
            expected_manifest_hash=digest,
            policy=OVERLAP,
            cost_model=ZERO,
            unit_size=D("1"),
        )
    with pytest.raises(ExitInputError, match="dataset_verification_failed"):
        run_exit_mode(
            (PLAN,),
            bars,
            manifest=manifest,
            expected_manifest_hash="0" * 64,
            policy=OVERLAP,
            cost_model=ZERO,
            unit_size=D("1"),
        )


def segment_bars() -> tuple[NormalizedPriceEvent, ...]:
    """30 flat-ish bars so train = bars 0-9, validation = 10-19, holdout = 20-29."""
    return tuple(make_bar(i, "100", "100.5", "99.8", "100") for i in range(30))


def seg_plan() -> SplitPlan:
    return SplitPlan(T0, T0 + BAR * 10, T0 + BAR * 20, T0 + BAR * 30)


def test_f3_segment_run_uses_only_that_segments_completed_bars() -> None:
    p = seg_plan()
    base = segment_bars()
    # A violent spike that only exists after the train segment must not touch a train run.
    spiked = (*base[:12], make_bar(12, "100", "500", "1", "100"), *base[13:])
    plan = TradePlan("t", "long", T0 + BAR * 2, D("98"), D("104"))
    a = run((plan,), base, OVERLAP, split=p, segment="train")
    b = run((plan,), spiked, OVERLAP, split=p, segment="train")
    assert a.trades == b.trades and a.unresolved == b.unresolved
    assert a.segment == "train" and a.split_plan_hash == p.plan_hash
    assert a.manifest_hash != b.manifest_hash  # different data, different identity
    # The trade is still open at the end of the train segment: it may not peek at validation.
    assert [u.status for u in a.unresolved] == ["open_at_end"]


def test_f3_plans_must_decide_inside_the_segment() -> None:
    p = seg_plan()
    late = TradePlan("late", "long", T0 + BAR * 12, D("98"), D("104"))
    with pytest.raises(ExitInputError, match="plan_outside_segment"):
        run((late,), segment_bars(), OVERLAP, split=p, segment="train")


def test_f3_split_and_segment_must_be_given_together() -> None:
    with pytest.raises(ExitInputError, match="split_and_segment"):
        run((PLAN,), bars_after_quiet(ENTRY), OVERLAP, split=seg_plan())


def test_f3_unsplit_run_is_labelled_not_out_of_sample() -> None:
    result = run((PLAN,), bars_after_quiet(ENTRY), OVERLAP)
    assert result.segment == "unsplit" and result.split_plan_hash is None
    assert "unsplit_run_not_valid_out_of_sample" in result.notes


def _unlock(p: SplitPlan) -> HoldoutUnlock:
    return HoldoutUnlock(p.plan_hash, "test-authority", "engineering regression test")


def test_f3_holdout_run_needs_unlock_and_log_and_records_the_access() -> None:
    p = seg_plan()
    plan = TradePlan("h", "long", T0 + BAR * 21, D("98"), D("104"))
    with pytest.raises(HoldoutLocked):
        run((plan,), segment_bars(), OVERLAP, split=p, segment="holdout")
    with pytest.raises(HoldoutLocked, match="holdout_access_log_required"):
        run((plan,), segment_bars(), OVERLAP, split=p, segment="holdout", unlock=_unlock(p))
    log = HoldoutAccessLog()
    result = run(
        (plan,),
        segment_bars(),
        OVERLAP,
        split=p,
        segment="holdout",
        unlock=_unlock(p),
        access_log=log,
    )
    assert result.holdout_access is not None and result.holdout_access.sequence == 1
    assert result.holdout_access.authorized_by == "test-authority"
    assert len(log.entries) == 1 and not log.entries[0].repeat


# ---- F4: honour-system protections ----------------------------------------------------


def real_events(count: int = 5) -> tuple[NormalizedPriceEvent, ...]:
    return tuple(
        replace(e, source="adapter-feed", identity_key=f"feed:{i}", source_event_id=f"feed:{i}")
        for i, e in enumerate(flat_bars(count))
    )


def test_f4_synthetic_events_cannot_be_labelled_real_market() -> None:
    prov = Provenance("real_market", "adapter-feed", T0, "UTC", "v", capability_profile_ref="p:1")
    with pytest.raises(EdgeDatasetError, match="synthetic_marker_in_real_market_data"):
        build_edge_manifest(flat_bars(5), prov, max_latency=LAT)  # source "synthetic-test"
    labelled = replace(prov, source="synthetic-generator")
    with pytest.raises(EdgeDatasetError, match="synthetic_marker_in_real_market_data"):
        build_edge_manifest(real_events(), labelled, max_latency=LAT)


def test_f4_real_market_requires_an_adapter_capability_reference() -> None:
    prov = Provenance("real_market", "adapter-feed", T0, "UTC", "v")
    with pytest.raises(EdgeDatasetError, match="real_market_requires_capability_ref"):
        build_edge_manifest(real_events(), prov, max_latency=LAT)
    blank = replace(prov, capability_profile_ref="  ")
    with pytest.raises(EdgeDatasetError, match="real_market_requires_capability_ref"):
        build_edge_manifest(real_events(), blank, max_latency=LAT)


def test_f4_attested_real_market_label_still_yields_no_statistical_eligibility() -> None:
    prov = Provenance("real_market", "adapter-feed", T0, "UTC", "v", capability_profile_ref="p:1")
    bars = tuple(
        replace(b, source="adapter-feed", identity_key=f"feed:{i}", source_event_id=f"feed:{i}")
        for i, b in enumerate(bars_after_quiet(ENTRY, ("100.5", "104.5", "100", "104")))
    )
    manifest = build_edge_manifest(bars, prov, max_latency=LAT)
    assert manifest.declared_real_market
    result = run_exit_mode(
        (PLAN,),
        bars,
        manifest=manifest,
        expected_manifest_hash=manifest.manifest_hash,
        policy=OVERLAP,
        cost_model=ZERO,
        unit_size=D("1"),
    )
    # Integrity is verified, but nothing independent vouches for provenance or authorization.
    assert result.gates.dataset_integrity_verified
    assert not result.statistical_evidence_eligible
    assert "statistical_evidence_fail_closed" in result.notes


def test_f4_holdout_reads_are_logged_and_repeats_are_flagged() -> None:
    p = seg_plan()
    log = HoldoutAccessLog()
    items = list(range(30))
    times = [T0 + BAR * i for i in items]
    forged = HoldoutUnlock(p.plan_hash, "someone", "forged but visible")
    first = select_segment(p, items, times, "holdout", unlock=forged, access_log=log)
    second = select_segment(p, items, times, "holdout", unlock=forged, access_log=log)
    assert first == second == tuple(range(20, 30))
    assert [e.sequence for e in log.entries] == [1, 2]
    assert [e.repeat for e in log.entries] == [False, True]
    assert all(e.authorized_by == "someone" and e.selected == 10 for e in log.entries)


def test_f4_holdout_read_without_an_access_log_is_refused() -> None:
    p = seg_plan()
    items = list(range(30))
    times = [T0 + BAR * i for i in items]
    with pytest.raises(HoldoutLocked, match="holdout_access_log_required"):
        select_segment(p, items, times, "holdout", unlock=_unlock(p))


def test_f4_train_and_validation_reads_are_not_logged() -> None:
    p = seg_plan()
    log = HoldoutAccessLog()
    items = list(range(30))
    times = [T0 + BAR * i for i in items]
    select_segment(p, items, times, "train", access_log=log)
    select_segment(p, items, times, "validation", access_log=log)
    assert log.entries == ()


# ---- F5: overlapping positions were all booked ----------------------------------------

OVERLAP_BARS = bars_after_quiet(
    ENTRY,
    ("100.5", "101", "100", "100.8"),
    ("100.8", "104.5", "100.5", "104"),
    ("100", "101", "99.5", "100.5"),
    ("100.5", "104.5", "100", "104"),
)
A = TradePlan("a", "long", T0 + BAR * 1, D("98"), D("104"))  # enters bar 1, exits bar 3
B = TradePlan("b", "long", T0 + BAR * 2, D("98"), D("104"))  # enters bar 2 while A is open
C = TradePlan("c", "long", T0 + BAR * 4, D("98"), D("104"))  # enters bar 4 == A's exit time


def test_f5_concurrency_policy_has_no_default() -> None:
    call: Any = ExitPolicy
    with pytest.raises(TypeError):
        call("no-concurrency")


def test_f5_one_position_skips_entries_while_a_trade_is_open_and_records_them() -> None:
    result = run((A, B, C), OVERLAP_BARS, ONE)
    assert [t.exit.signal_id for t in result.trades] == ["a", "c"]
    assert [u.signal_id for u in result.unresolved] == ["b"]
    assert result.unresolved[0].status == "skipped_overlap"
    assert result.skipped_overlap_count == 1
    assert "concurrency=one_position" in result.notes


def test_f5_entry_exactly_at_the_previous_exit_time_is_allowed() -> None:
    result = run((A, C), OVERLAP_BARS, ONE)
    a, c = result.trades
    assert a.exit.exit_time == c.exit.entry_time
    assert result.skipped_overlap_count == 0


def test_f5_allow_overlap_books_every_plan_and_says_so() -> None:
    result = run((A, B, C), OVERLAP_BARS, OVERLAP)
    assert [t.exit.signal_id for t in result.trades] == ["a", "b", "c"]
    assert "overlapping_positions_may_stack_exposure" in result.notes


def test_f5_nothing_can_be_entered_after_a_trade_that_never_closed() -> None:
    bars = bars_after_quiet(
        ENTRY, ("100.5", "101", "100", "100.8"), ("100.8", "101", "100", "100.9")
    )
    late = TradePlan("late", "long", T0 + BAR * 2, D("98"), D("104"))
    first = TradePlan("first", "long", T0 + BAR * 1, D("98"), D("104"))
    result = run((first, late), bars, ONE)
    assert [u.status for u in result.unresolved] == ["open_at_end", "skipped_overlap"]


def test_f5_policy_choice_changes_run_identity() -> None:
    assert run((A, B), OVERLAP_BARS, ONE).run_id != run((A, B), OVERLAP_BARS, OVERLAP).run_id


# ---- F6: false ambiguity when a bar opens beyond the target ---------------------------


def test_f6_long_bar_opening_beyond_target_then_through_stop_is_a_target_not_ambiguous() -> None:
    bars = bars_after_quiet(ENTRY, ("106", "107", "97", "100"))
    r = simulate_exit(PLAN, bars, OVERLAP)
    assert (r.exit_reason, r.exit_price) == ("target", D("104"))
    assert not r.ambiguous and r.alternative_exit_prices == ()
    assert r.exit_time == bars[2].event_time  # resolved at the open, not the bar close
    assert "target_filled_at_target_price_no_gap_improvement" in r.assumptions


def test_f6_short_mirror() -> None:
    short = TradePlan("s", "short", DECISION, D("102"), D("96"))
    bars = bars_after_quiet(ENTRY, ("94", "103", "93", "100"))
    r = simulate_exit(short, bars, OVERLAP)
    assert (r.exit_reason, r.exit_price) == ("target", D("96")) and not r.ambiguous


def test_f6_a_genuinely_ambiguous_bar_is_still_ambiguous() -> None:
    r = simulate_exit(PLAN, bars_after_quiet(ENTRY, ("100", "105", "97", "100")), OVERLAP)
    assert r.ambiguous and r.exit_reason == "stop"


# ---- F7: a crossed entry quote aborted the run ----------------------------------------


def test_f7_crossed_entry_quote_makes_spread_unavailable_without_aborting() -> None:
    bars = (
        make_bar(0, "100", "100", "100", "100"),
        make_bar(1, "100", "104.5", "100", "104", bid="101", ask="100"),
    )
    r = simulate_exit(PLAN, bars, OVERLAP)
    assert r.status == "closed" and r.entry_spread is None
    assert "entry_quote_invalid_spread_unavailable" in r.assumptions
    model = CostModel(
        "t", "X", UNKNOWN, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, spread_from_quotes=True
    )
    trade = to_edge_trade(r, model, unit_size=D("1"))  # must not raise
    assert trade.costs.unavailable == ("spread",) and not trade.net_complete


def test_f7_valid_quote_still_gives_the_spread() -> None:
    bars = (
        make_bar(0, "100", "100", "100", "100"),
        make_bar(1, "100", "104.5", "100", "104", bid="99.9", ask="100.1"),
    )
    assert simulate_exit(PLAN, bars, OVERLAP).entry_spread == D("0.2")


# ---- F8: holding time ----------------------------------------------------------------


def test_f8_intrabar_fills_are_flagged_as_an_upper_bound_on_exit_time() -> None:
    stop = simulate_exit(PLAN, bars_after_quiet(ENTRY, ("100", "100.5", "97.5", "98")), OVERLAP)
    target = simulate_exit(PLAN, bars_after_quiet(ENTRY, ("100.5", "104.5", "100", "104")), OVERLAP)
    assert stop.exit_time_upper_bound and target.exit_time_upper_bound


def test_f8_time_stop_and_gap_exits_are_exact() -> None:
    held = simulate_exit(
        PLAN,
        bars_after_quiet(ENTRY, ("100.5", "101", "100", "100.8")),
        ExitPolicy("t", "allow_overlap", BAR, max_hold_bars=2),
    )
    gap = simulate_exit(PLAN, bars_after_quiet(ENTRY, ("96", "97", "95", "96.5")), OVERLAP)
    assert held.exit_reason == "time" and not held.exit_time_upper_bound
    assert gap.exit_reason == "stop_gap" and not gap.exit_time_upper_bound
    assert gap.exit_time == T0 + BAR * 2


def test_f8_utc_zone_is_preserved() -> None:
    r = simulate_exit(PLAN, bars_after_quiet(ENTRY, ("100.5", "104.5", "100", "104")), OVERLAP)
    assert r.exit_time is not None and r.exit_time.tzinfo is not None
    assert r.exit_time.astimezone(UTC) == r.exit_time
