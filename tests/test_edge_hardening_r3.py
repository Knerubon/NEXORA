"""ENGINEERING regression tests for PR #79 Hardening Round 3 (Agent B review B-1 .. B-12).

Each test reproduces a finding of the independent review against the previous HEAD
(ec54201) and asserts it is now prevented. Inputs are synthetic; nothing here is evidence
about trading profitability.
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
    Provenance,
    SplitError,
    SplitPlan,
    TradePlan,
    WalkForwardWindow,
    audit_events,
    build_edge_manifest,
    run_exit_mode,
    simulate_exit,
    verified_evidence_gates,
    walk_forward_windows,
    window_bounds,
)
from nexora.market_data.models import NormalizedPriceEvent

from tests.edge_fixtures import BAR, T0, make_bar, synthetic_manifest

D = Decimal
LAT = BAR + timedelta(seconds=30)
ZERO = CostModel("t", "SYNTH-UNIT", KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO)
OVERLAP = ExitPolicy("r3", "allow_overlap", BAR)


def flat(count: int) -> tuple[NormalizedPriceEvent, ...]:
    return tuple(make_bar(i, "100", "100.5", "99.8", "100") for i in range(count))


def plan60() -> SplitPlan:
    return SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 40, T0 + BAR * 60)


def run(
    plans: Any,
    bars: tuple[NormalizedPriceEvent, ...],
    *,
    basis: Any = "bid",
    register: SplitPlan | None = None,
    cost_model: CostModel = ZERO,
    **kwargs: Any,
) -> Any:
    manifest, digest = synthetic_manifest(bars, ohlc_basis=basis, split_plan=register)
    return run_exit_mode(
        plans,
        bars,
        manifest=manifest,
        expected_manifest_hash=digest,
        policy=OVERLAP,
        cost_model=cost_model,
        unit_size=D("1"),
        **kwargs,
    )


# ---- B-1: stop/target triggers must be side-aware -----------------------------------------


def short_bars() -> tuple[NormalizedPriceEvent, ...]:
    # Bid OHLC; quoted spread 0.5. In bar 1 the bid high is 100.8, so the ASK high is 101.3,
    # which is through the short's stop at 101. Bar 2 then trades down to the target.
    return (
        make_bar(0, "100", "100", "100", "100", bid="100", ask="100.5"),
        make_bar(1, "100", "100.8", "99.9", "100", bid="100", ask="100.5"),
        make_bar(2, "100", "100", "97", "97"),
    )


SHORT = TradePlan("s", "short", T0 + BAR, D("101"), D("97"))


def test_b1_short_on_bid_bars_is_refused_not_booked_as_a_target_win() -> None:
    # Previously: status "closed", exit_reason "target" although the ask crossed the stop.
    with pytest.raises(ExitInputError, match="ohlc_basis_bid_cannot_fill_short_exits"):
        simulate_exit(SHORT, short_bars(), OVERLAP, ohlc_basis="bid")
    with pytest.raises(ExitInputError, match="ohlc_basis_bid_cannot_fill_short_exits"):
        run([SHORT], short_bars(), basis="bid")


def test_b1_same_market_on_ask_bars_stops_the_short_out() -> None:
    # The same market expressed as ASK OHLC (bid + 0.5): the stop is hit in bar 1.
    ask = (
        make_bar(0, "100.5", "100.5", "100.5", "100.5"),
        make_bar(1, "100.5", "101.3", "100.4", "100.5"),
        make_bar(2, "100.5", "100.5", "97.5", "97.5"),
    )
    result = simulate_exit(SHORT, ask, OVERLAP, ohlc_basis="ask")
    assert result.status == "closed" and result.exit_reason == "stop"
    assert result.exit_price == D("101")


@pytest.mark.parametrize("basis", ["mid", "last", "ask"])
def test_b1_long_needs_bid_bars(basis: str) -> None:
    long = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    with pytest.raises(ExitInputError, match=f"ohlc_basis_{basis}_cannot_fill_long_exits"):
        simulate_exit(long, flat(5), OVERLAP, ohlc_basis=basis)


@pytest.mark.parametrize("basis", ["mid", "last", "bid"])
def test_b1_short_needs_ask_bars(basis: str) -> None:
    short = TradePlan("s", "short", T0 + BAR, D("102"), D("96"))
    with pytest.raises(ExitInputError, match=f"ohlc_basis_{basis}_cannot_fill_short_exits"):
        simulate_exit(short, flat(5), OVERLAP, ohlc_basis=basis)


def test_b1_undeclared_or_invalid_basis_fails_closed() -> None:
    long = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    with pytest.raises(ExitInputError, match="ohlc_basis_undeclared"):
        run([long], flat(5), basis=None)
    with pytest.raises(ExitInputError, match="ohlc_basis_undeclared"):
        simulate_exit(long, flat(5), OVERLAP, ohlc_basis=None)
    with pytest.raises(ExitInputError, match="invalid_ohlc_basis"):
        simulate_exit(long, flat(5), OVERLAP, ohlc_basis="BID")


def test_b1_basis_is_part_of_the_manifest_identity_and_recorded_in_the_run() -> None:
    bid, _ = synthetic_manifest(flat(5), ohlc_basis="bid")
    ask, _ = synthetic_manifest(flat(5), ohlc_basis="ask")
    assert bid.manifest_hash != ask.manifest_hash
    long = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    result = run([long], flat(5))
    assert "ohlc_basis=bid" in result.notes
    assert "exit_triggers_on_exit_side_quotes" in result.notes


# ---- B-2: holdout lock bypass by running unsplit or with another plan ------------------------


def holdout_market() -> tuple[NormalizedPriceEvent, ...]:
    bars = list(flat(60))
    bars[46] = make_bar(46, "100", "104.5", "99.8", "100")  # target hit inside the holdout
    return tuple(bars)


HOLDOUT_PLAN = TradePlan("h", "long", T0 + BAR * 45, D("98"), D("104"))


def test_b2_registered_dataset_cannot_be_run_unsplit() -> None:
    # Previously: ('unsplit', 1 trade, holdout_access=None).
    with pytest.raises(ExitInputError, match="registered_split_requires_split_run"):
        run([HOLDOUT_PLAN], holdout_market(), register=plan60())


def test_b2_a_different_plan_cannot_relabel_the_holdout_as_validation() -> None:
    # Previously: validation_end moved to the dataset end read the holdout with no unlock.
    moved = SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 59, T0 + BAR * 60)
    with pytest.raises(ExitInputError, match="split_plan_hash_mismatch"):
        run(
            [HOLDOUT_PLAN],
            holdout_market(),
            register=plan60(),
            split=moved,
            segment="validation",
        )


def test_b2_a_split_run_needs_a_registered_plan() -> None:
    with pytest.raises(ExitInputError, match="split_not_registered_in_manifest"):
        run([HOLDOUT_PLAN], holdout_market(), split=plan60(), segment="validation")


def test_b2_holdout_still_needs_unlock_with_the_registered_plan() -> None:
    p = plan60()
    with pytest.raises(SplitError, match="holdout_locked"):
        run([HOLDOUT_PLAN], holdout_market(), register=p, split=p, segment="holdout")
    log = HoldoutAccessLog()
    result = run(
        [HOLDOUT_PLAN],
        holdout_market(),
        register=p,
        split=p,
        segment="holdout",
        unlock=HoldoutUnlock(p.plan_hash, "rin", "final check"),
        access_log=log,
    )
    assert result.segment == "holdout" and result.holdout_access is not None
    assert len(log.entries) == 1 and not result.statistical_evidence_eligible


def test_b2_registered_plan_must_partition_the_dataset() -> None:
    prov = Provenance("synthetic_fixture", "synthetic-test", T0, "UTC", "v", ohlc_basis="bid")
    with pytest.raises(EdgeDatasetError, match="split_plan_does_not_cover_dataset"):
        build_edge_manifest(
            flat(60),
            prov,
            max_latency=LAT,
            split_plan=SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 40, T0 + BAR * 50),
        )
    with pytest.raises(EdgeDatasetError, match="split_plan_holdout_empty"):
        build_edge_manifest(
            flat(40),
            prov,
            max_latency=LAT,
            split_plan=SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 40, T0 + BAR * 60),
        )


def test_b2_registration_changes_the_manifest_identity() -> None:
    none, _ = synthetic_manifest(flat(60))
    one, _ = synthetic_manifest(flat(60), split_plan=plan60())
    other, _ = synthetic_manifest(
        flat(60), split_plan=SplitPlan(T0, T0 + BAR * 25, T0 + BAR * 40, T0 + BAR * 60)
    )
    assert len({none.manifest_hash, one.manifest_hash, other.manifest_hash}) == 3
    assert one.split_plan_hash == plan60().plan_hash and none.split_plan_hash is None


def test_b2_tampering_with_the_registered_hash_fails_verification() -> None:
    manifest, digest = synthetic_manifest(holdout_market(), split_plan=plan60())
    forged = replace(manifest, split_plan_hash=None)
    with pytest.raises(ExitInputError, match="manifest_hash_mismatch"):
        run_exit_mode(
            [HOLDOUT_PLAN],
            holdout_market(),
            manifest=forged,
            expected_manifest_hash=digest,
            policy=OVERLAP,
            cost_model=ZERO,
            unit_size=D("1"),
        )


# ---- B-4 / B-5 / B-6: dataset audit gaps -------------------------------------------------------


def test_b4_two_bars_for_the_same_period_are_duplicates() -> None:
    second = replace(make_bar(0, "100", "100", "100", "100", seq=1), identity_key="x1")
    bars = (make_bar(0, "100", "100", "100", "100"), second)
    report = audit_events(bars, max_latency=LAT)
    assert report.count("duplicate") == 1  # previously: counts ()
    prov = Provenance("synthetic_fixture", "synthetic-test", T0, "UTC", "v")
    with pytest.raises(EdgeDatasetError, match="duplicate"):
        build_edge_manifest(bars, prov, max_latency=LAT)


def test_b4_same_timestamp_ticks_are_not_flagged() -> None:
    tick = replace(make_bar(0, "100", "100", "100", "100"), kind="tick")
    other = replace(tick, identity_key="t1", source_sequence=1, source_order=1)
    assert audit_events((tick, other), max_latency=LAT).count("duplicate") == 0


def test_b5_bars_closer_than_the_interval_are_rejected_by_the_exit_mode() -> None:
    bars = tuple(
        replace(
            make_bar(i, "100", "100", "100", "100"),
            event_time=T0 + timedelta(seconds=150 * i),
            received_at=T0 + timedelta(seconds=150 * i) + BAR,
        )
        for i in range(4)
    )
    long = TradePlan("l", "long", T0, D("98"), D("104"))
    with pytest.raises(ExitInputError, match="bars_overlap_interval"):
        simulate_exit(long, bars, OVERLAP, ohlc_basis="bid")


def test_b5_a_gap_larger_than_the_interval_is_still_allowed() -> None:
    bars = (make_bar(0, "100", "100", "100", "100"), make_bar(3, "100", "105", "100", "104"))
    long = TradePlan("l", "long", T0 - BAR, D("98"), D("104"))
    result = simulate_exit(long, bars, OVERLAP, ohlc_basis="bid")
    assert result.status == "closed"


@pytest.mark.parametrize(
    "change", [{"price_source": "open"}, {"units": "OTHER-UNIT"}], ids=["price_source", "units"]
)
def test_b6_mixed_price_source_or_units_block_the_dataset(change: dict[str, str]) -> None:
    bars = flat(3) + tuple(replace(b, **change) for b in flat(6)[3:])  # type: ignore[arg-type]
    assert audit_events(bars, max_latency=LAT).count("mixed_price_basis_or_units") == 1
    prov = Provenance("synthetic_fixture", "synthetic-test", T0, "UTC", "v")
    with pytest.raises(EdgeDatasetError, match="mixed_price_basis_or_units"):
        build_edge_manifest(bars, prov, max_latency=LAT)


# ---- B-7: costs must be in the same units as gross PnL ----------------------------------------


def test_b7_cost_currency_must_equal_dataset_price_units() -> None:
    long = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    usd = replace(ZERO, currency="USD")
    with pytest.raises(ExitInputError, match="cost_units_mismatch"):
        run([long], flat(5), cost_model=usd)
    assert "cost_units=SYNTH-UNIT" in run([long], flat(5)).notes


# ---- B-8: open trades at the end are counted, not only listed ---------------------------------


def test_b8_open_at_end_is_counted_beside_the_trades() -> None:
    long = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    result = run([long], flat(5))
    assert result.trades == () and result.open_at_end_count == 1
    assert "open_trades_at_segment_end_are_excluded_quant_decision_open" in result.notes


# ---- B-9: walk-forward purge and wiring ---------------------------------------------------------


def purged() -> SplitPlan:
    return SplitPlan(T0, T0 + BAR * 20, T0 + BAR * 40, T0 + BAR * 60, purge=BAR * 3)


def test_b9_walk_forward_inherits_the_plan_purge() -> None:
    windows = walk_forward_windows(purged(), train=BAR * 10, test=BAR * 5)
    # Previously the default purge was 0, so train_end == test_start.
    assert all(w.test_start - w.train_end == BAR * 3 for w in windows)


def test_b9_walk_forward_purge_cannot_be_smaller_than_the_plan_purge() -> None:
    with pytest.raises(SplitError, match="purge_below_plan_purge"):
        walk_forward_windows(purged(), train=BAR * 10, test=BAR * 5, purge=BAR)
    wider = walk_forward_windows(purged(), train=BAR * 10, test=BAR * 5, purge=BAR * 4)
    assert wider[0].test_start - wider[0].train_end == BAR * 4


def test_b9_walk_forward_window_runs_on_a_registered_dataset() -> None:
    p = purged()
    window = walk_forward_windows(p, train=BAR * 10, test=BAR * 5)[0]
    bars = list(flat(60))
    bars[14] = make_bar(14, "100", "104.5", "99.8", "100")
    plan = TradePlan("w", "long", window.test_start, D("98"), D("104"))
    result = run([plan], tuple(bars), register=p, split=p, walk_forward=(window, "test"))
    assert result.segment == "walk_forward:0:test" and len(result.trades) == 1
    assert "unsplit_run_not_valid_out_of_sample" not in result.notes
    with pytest.raises(ExitInputError, match="plan_outside_segment"):
        run([plan], tuple(bars), register=p, split=p, walk_forward=(window, "train"))


def test_b9_hand_built_window_cannot_reach_the_holdout() -> None:
    p = purged()
    sneaky = WalkForwardWindow(0, T0, T0 + BAR * 10, T0 + BAR * 45, T0 + BAR * 55)
    with pytest.raises(SplitError, match="window_not_derived_from_plan"):
        window_bounds(p, sneaky, "test")
    unpurged = WalkForwardWindow(0, T0, T0 + BAR * 10, T0 + BAR * 10, T0 + BAR * 15)
    with pytest.raises(SplitError, match="window_not_derived_from_plan"):
        window_bounds(p, unpurged, "test")
    with pytest.raises(SplitError, match="invalid_window_part"):
        window_bounds(p, walk_forward_windows(p, train=BAR * 10, test=BAR * 5)[0], "holdout")


def test_b9_segment_and_walk_forward_are_exclusive() -> None:
    p = purged()
    window = walk_forward_windows(p, train=BAR * 10, test=BAR * 5)[0]
    with pytest.raises(ExitInputError, match="segment_and_walk_forward_are_exclusive"):
        run(
            [],
            flat(60),
            register=p,
            split=p,
            segment="train",
            walk_forward=(window, "test"),
        )


# ---- B-10: the integrity gate can no longer be self-asserted -----------------------------------


def test_b10_manifest_gates_never_claim_integrity_without_verification() -> None:
    manifest, digest = synthetic_manifest(flat(5))
    assert manifest.evidence_gates().dataset_integrity_verified is False
    call: Any = manifest.evidence_gates
    with pytest.raises(TypeError):
        call(dataset_integrity_verified=True)
    gates = verified_evidence_gates(manifest, flat(5), expected_manifest_hash=digest)
    assert gates.dataset_integrity_verified and not gates.statistical_evidence_eligible
    with pytest.raises(EdgeDatasetError, match="manifest_hash_mismatch"):
        verified_evidence_gates(manifest, flat(5), expected_manifest_hash="forged")


# ---- B-12: quote timing is declared in every run ----------------------------------------------


def test_b12_quote_timing_assumptions_are_in_the_notes() -> None:
    long = TradePlan("l", "long", T0 + BAR, D("98"), D("104"))
    notes = run([long], flat(5)).notes
    assert "entry_spread_quote_timing_within_bar_undeclared" in notes
    assert "exit_side_spread_not_modelled" in notes
