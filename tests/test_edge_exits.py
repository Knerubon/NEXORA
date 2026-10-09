"""ENGINEERING tests (synthetic bars): SL/TP-aware research exit mode.

These prove the code's mechanics (ordering, ambiguity handling, no look-ahead, costs). The
numbers come from invented bars and say nothing about trading profitability.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from nexora.edge import (
    KNOWN_ZERO,
    UNKNOWN,
    CostComponent,
    CostModel,
    EdgeExitRun,
    ExitInputError,
    ExitPolicy,
    ExitResult,
    TradePlan,
    plan_from_signal,
    run_exit_mode,
    simulate_exit,
    to_edge_trade,
)
from nexora.market_data.models import NormalizedPriceEvent
from nexora.signals import ResearchSignal
from nexora.signals.models import SignalDecision, SignalTarget

from tests.edge_fixtures import BAR, T0, make_bar, synthetic_manifest

D = Decimal
DECISION = T0 + BAR  # bar 0 closes here; bar 1 is the first bar that can be entered

LONG = TradePlan("s-long", "long", DECISION, stop_price=D("98"), target_price=D("104"))
SHORT = TradePlan("s-short", "short", DECISION, stop_price=D("102"), target_price=D("96"))
POLICY = ExitPolicy(version="t1", concurrency="allow_overlap")
ZERO_COSTS = CostModel("t", "XXX", KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO)


def quiet(i: int, price: str = "100") -> NormalizedPriceEvent:
    return make_bar(i, price, price, price, price)


def bars_with(*specs: tuple[str, str, str, str]) -> tuple[NormalizedPriceEvent, ...]:
    """bar0 is a quiet pre-decision bar; specs become bars 1.. (bar 1 = entry bar)."""
    out: list[NormalizedPriceEvent] = [quiet(0)]
    out.extend(make_bar(i, *s) for i, s in enumerate(specs, start=1))
    return tuple(out)


def sim(
    plan: TradePlan, bars: tuple[NormalizedPriceEvent, ...], policy: ExitPolicy = POLICY
) -> ExitResult:
    return simulate_exit(plan, bars, policy)


ENTRY = ("100", "101", "99.5", "100.5")


# ---- plain outcomes -------------------------------------------------------------------


def test_long_hits_target() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100.5", "104.5", "100", "104")))
    assert (r.status, r.exit_reason, r.exit_price) == ("closed", "target", D("104"))
    assert r.entry_price == D("100") and r.bars_held == 2
    assert r.r_multiple == D("2") and not r.ambiguous and r.assumptions == ()


def test_long_hits_stop() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100", "100.5", "97.5", "98")))
    assert (r.exit_reason, r.exit_price, r.r_multiple) == ("stop", D("98"), D("-1"))
    assert not r.ambiguous


def test_short_mirror_target_and_stop() -> None:
    win = sim(SHORT, bars_with(ENTRY, ("100", "100.5", "95.5", "96")))
    assert (win.exit_reason, win.exit_price, win.r_multiple) == ("target", D("96"), D("2"))
    loss = sim(SHORT, bars_with(ENTRY, ("100", "102.5", "99.5", "102")))
    assert (loss.exit_reason, loss.exit_price, loss.r_multiple) == ("stop", D("102"), D("-1"))


def test_time_stop_exits_at_close_after_n_bars() -> None:
    bars = bars_with(ENTRY, ("100.5", "101", "100", "100.8"), ("100.8", "101", "100", "100.2"))
    r = sim(LONG, bars, ExitPolicy("t", "allow_overlap", max_hold_bars=2))
    assert (r.exit_reason, r.exit_price, r.bars_held) == ("time", D("100.8"), 2)


def test_unresolved_trade_is_open_at_end_not_closed() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100.5", "101", "100", "100.8")))
    assert r.status == "open_at_end" and r.exit_price is None and r.r_multiple is None


def test_no_entry_when_no_bar_after_decision() -> None:
    r = sim(LONG, (quiet(0),))
    assert r.status == "no_entry"


def test_plan_invalid_at_actual_entry_is_rejected_not_traded() -> None:
    above = sim(LONG, bars_with(("105", "106", "105", "105")))  # opened beyond target
    below = sim(LONG, bars_with(("97", "98", "96", "97")))  # opened beyond stop
    assert above.status == below.status == "rejected_invalid_plan"


# ---- ambiguity (SL and TP both touched, no tick order) ---------------------------------


def test_ambiguous_bar_defaults_to_stop_first_and_is_flagged() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100", "105", "97", "100")))
    assert r.exit_reason == "stop" and r.exit_price == D("98")
    assert r.ambiguous
    assert r.assumptions == ("same_bar_stop_and_target_order_unknown:stop_first",)
    assert dict(r.alternative_exit_prices) == {"stop_first": D("98"), "target_first": D("104")}


def test_target_first_policy_is_available_but_still_flagged() -> None:
    r = sim(
        LONG,
        bars_with(ENTRY, ("100", "105", "97", "100")),
        ExitPolicy("t", "allow_overlap", ambiguity="target_first"),
    )
    assert r.exit_reason == "target" and r.exit_price == D("104") and r.ambiguous
    assert r.assumptions == ("same_bar_stop_and_target_order_unknown:target_first",)


def test_ambiguity_on_the_entry_bar_itself() -> None:
    r = sim(LONG, bars_with(("100", "105", "97", "100")))
    assert r.ambiguous and r.exit_reason == "stop" and r.bars_held == 1


def test_short_ambiguity_is_symmetric() -> None:
    r = sim(SHORT, bars_with(ENTRY, ("100", "103", "95", "100")))
    assert r.ambiguous and r.exit_reason == "stop" and r.exit_price == D("102")
    assert dict(r.alternative_exit_prices)["target_first"] == D("96")


def test_unambiguous_trade_carries_no_ambiguity_evidence() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100.5", "104.5", "100", "104")))
    assert not r.ambiguous and r.alternative_exit_prices == ()


# ---- gaps -----------------------------------------------------------------------------


def test_gap_through_stop_fills_at_the_worse_open() -> None:
    r = sim(LONG, bars_with(ENTRY, ("96", "97", "95", "96.5")))
    assert (r.exit_reason, r.exit_price, r.r_multiple) == ("stop_gap", D("96"), D("-2"))
    assert not r.ambiguous and "stop_filled_at_gap_open" in r.assumptions


def test_gap_through_target_gets_no_price_improvement() -> None:
    r = sim(LONG, bars_with(ENTRY, ("106", "107", "105", "106")))
    assert (r.exit_reason, r.exit_price) == ("target", D("104"))


# ---- no look-ahead --------------------------------------------------------------------


def test_bars_before_the_decision_are_never_used() -> None:
    bars = list(bars_with(ENTRY, ("100.5", "104.5", "100", "104")))
    base = sim(LONG, tuple(bars))
    bars[0] = make_bar(0, "100", "500", "1", "100")  # wild pre-decision bar
    assert sim(LONG, tuple(bars)) == base


def test_bars_after_the_exit_never_change_the_result() -> None:
    head = bars_with(ENTRY, ("100.5", "104.5", "100", "104"))
    base = sim(LONG, head)
    for tail in (
        (make_bar(3, "1", "1", "1", "1"),),
        (make_bar(3, "9999", "99999", "1", "5"), make_bar(4, "2", "2", "2", "2")),
    ):
        assert sim(LONG, (*head, *tail)) == base


def test_result_is_independent_of_truncating_after_exit() -> None:
    full = bars_with(ENTRY, ("100", "100.5", "97.5", "98"), ("98", "120", "90", "100"))
    cut = full[:3]
    assert sim(LONG, full) == sim(LONG, cut)


def test_entry_never_precedes_decision_plus_delay() -> None:
    bars = tuple(quiet(i) for i in range(10))
    r = sim(LONG, bars, ExitPolicy("t", "allow_overlap", entry_delay=timedelta(minutes=12)))
    assert r.entry_time is not None
    assert r.entry_time >= DECISION + timedelta(minutes=12)
    assert r.entry_time == T0 + BAR * 4  # first bar open at/after 5m + 12m = 17m -> 20m


def test_decision_late_in_series_ignores_earlier_bars() -> None:
    bars = bars_with(ENTRY, ("100", "100.5", "97.5", "98"))
    late = replace(LONG, decision_time=T0 + BAR * 3)  # after every bar opened
    assert sim(late, bars).status == "no_entry"


# ---- invalid input --------------------------------------------------------------------


def test_invalid_ohlc_rejected() -> None:
    bad_high = bars_with(("100", "99", "98", "99"))  # high below open/close
    with pytest.raises(ExitInputError, match="invalid_ohlc"):
        sim(LONG, bad_high)
    with pytest.raises(ExitInputError, match="invalid_ohlc"):
        sim(LONG, bars_with(("0", "1", "0", "1")))


def test_non_bar_or_incomplete_events_rejected() -> None:
    tick = replace(make_bar(1, "100", "100", "100", "100"), kind="tick")
    with pytest.raises(ExitInputError, match="bars_required"):
        sim(LONG, (quiet(0), tick))
    no_ohlc = replace(make_bar(1, "100", "100", "100", "100"), high=None)
    with pytest.raises(ExitInputError, match="bars_required"):
        sim(LONG, (quiet(0), no_ohlc))


def test_non_increasing_bars_rejected() -> None:
    with pytest.raises(ExitInputError, match="bars_not_strictly_increasing"):
        sim(LONG, (quiet(1), quiet(0)))


def test_invalid_plan_prices_rejected() -> None:
    for bad in (D("0"), D("-1"), D("NaN")):
        with pytest.raises(ExitInputError, match="invalid_plan_price"):
            sim(replace(LONG, stop_price=bad), bars_with(ENTRY))


def test_policy_validation() -> None:
    with pytest.raises(ExitInputError):
        ExitPolicy("t", "allow_overlap", max_hold_bars=0)
    with pytest.raises(ExitInputError):
        ExitPolicy("t", "allow_overlap", entry_delay=timedelta(seconds=-1))


# ---- extreme numerics -----------------------------------------------------------------


def test_huge_and_tiny_prices_stay_exact() -> None:
    scale = D("1E+24")
    big = TradePlan("big", "long", DECISION, D("98") * scale, D("104") * scale)
    bars = bars_with(
        ("100E+24", "101E+24", "99.5E+24", "100.5E+24"),
        ("100.5E+24", "104.5E+24", "100E+24", "104E+24"),
    )
    r = sim(big, bars)
    assert r.exit_price == D("104E+24") and r.r_multiple == D("2")
    tiny = TradePlan("tiny", "long", DECISION, D("0.98E-9"), D("1.04E-9"))
    tbars = bars_with(
        ("1.00E-9", "1.01E-9", "0.995E-9", "1.005E-9"),
        ("1.005E-9", "1.045E-9", "1.00E-9", "1.04E-9"),
    )
    assert sim(tiny, tbars).r_multiple == D("2")


def test_r_multiple_is_exact_decimal() -> None:
    plan = TradePlan("x", "long", DECISION, D("99.9"), D("100.3"))
    r = sim(plan, bars_with(("100", "100.4", "99.95", "100.3")))
    assert r.r_multiple == D("3")


# ---- costs + sensitivity --------------------------------------------------------------


def test_edge_trade_applies_known_costs() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100.5", "104.5", "100", "104")))
    model = CostModel(
        "t", "XXX", CostComponent("known", D("0.5")), KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO
    )
    t = to_edge_trade(r, model, unit_size=D("2"))
    assert t.gross_pnl == D("8") and t.costs.known_total == D("1")
    assert t.net_pnl_known_costs == D("7") and t.net_complete


def test_unknown_cost_makes_net_a_bound_not_a_result() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100.5", "104.5", "100", "104")))
    model = CostModel("t", "XXX", KNOWN_ZERO, UNKNOWN, KNOWN_ZERO, KNOWN_ZERO)
    t = to_edge_trade(r, model, unit_size=D("1"))
    assert not t.net_complete and t.costs.unknown == ("commission",)
    assert t.net_pnl_known_costs == t.gross_pnl


def test_ambiguous_trade_exposes_both_outcomes_for_sensitivity() -> None:
    r = sim(LONG, bars_with(ENTRY, ("100", "105", "97", "100")))
    t = to_edge_trade(r, ZERO_COSTS, unit_size=D("1"))
    sens = {label: (g, n) for label, g, n in t.sensitivity}
    assert sens["stop_first"][0] == D("-2") and sens["target_first"][0] == D("4")
    assert t.gross_pnl == D("-2")  # the conservative assumption is what is booked


def test_spread_is_taken_from_the_entry_bar_quote_only_if_present() -> None:
    quoted = bars_with(("100", "101", "99.5", "100.5"), ("100.5", "104.5", "100", "104"))
    quoted = (
        quoted[0],
        make_bar(1, *ENTRY, bid="99.9", ask="100.1"),
        quoted[2],
    )
    model = CostModel(
        "t", "XXX", UNKNOWN, KNOWN_ZERO, KNOWN_ZERO, KNOWN_ZERO, spread_from_quotes=True
    )
    t = to_edge_trade(sim(LONG, quoted), model, unit_size=D("1"))
    assert t.costs.complete and t.costs.known_total == D("0.2")
    unquoted = bars_with(ENTRY, ("100.5", "104.5", "100", "104"))
    t2 = to_edge_trade(sim(LONG, unquoted), model, unit_size=D("1"))
    assert t2.costs.unavailable == ("spread",) and not t2.net_complete


def test_unclosed_trade_cannot_be_costed() -> None:
    r = sim(LONG, bars_with(ENTRY))
    with pytest.raises(ExitInputError, match="trade_not_closed"):
        to_edge_trade(r, ZERO_COSTS, unit_size=D("1"))


# ---- run-level determinism ------------------------------------------------------------


def _run(
    plans: tuple[TradePlan, ...],
    bars: tuple[NormalizedPriceEvent, ...],
    policy: ExitPolicy = POLICY,
) -> EdgeExitRun:
    manifest, manifest_hash = synthetic_manifest(bars)
    return run_exit_mode(
        plans,
        bars,
        manifest=manifest,
        expected_manifest_hash=manifest_hash,
        policy=policy,
        cost_model=ZERO_COSTS,
        unit_size=D("1"),
    )


def test_run_is_deterministic_and_input_order_independent() -> None:
    bars = bars_with(ENTRY, ("100.5", "104.5", "100", "104"), ("104", "104", "97", "98"))
    p2 = replace(SHORT, decision_time=DECISION + BAR, signal_id="s-short")
    a = _run((LONG, p2), bars)
    b = _run((p2, LONG), bars)
    assert a == b and a.run_id == b.run_id
    other = bars_with(ENTRY, ("100.5", "104.5", "100", "104"), ("104", "104", "97", "99"))
    assert a.run_id != _run((LONG, p2), other).run_id  # different data -> different identity


def test_run_separates_closed_from_unresolved_and_counts_ambiguity() -> None:
    bars = bars_with(ENTRY, ("100", "105", "97", "100"))
    never = replace(LONG, signal_id="never", decision_time=T0 + BAR * 50)
    run = _run((LONG, never), bars)
    assert len(run.trades) == 1 and run.ambiguous_count == 1
    assert [u.status for u in run.unresolved] == ["no_entry"]
    assert "not_the_production_exit_rules" in run.notes
    assert "synthetic_data_not_statistical_evidence" in run.notes
    assert not run.statistical_evidence_eligible


def test_run_rejects_duplicate_ids_and_bad_unit_size() -> None:
    with pytest.raises(ExitInputError, match="duplicate_signal_id"):
        _run((LONG, LONG), bars_with(ENTRY))
    bars = bars_with(ENTRY)
    manifest, manifest_hash = synthetic_manifest(bars)
    with pytest.raises(ExitInputError, match="invalid_unit_size"):
        run_exit_mode(
            (LONG,),
            bars,
            manifest=manifest,
            expected_manifest_hash=manifest_hash,
            policy=POLICY,
            cost_model=ZERO_COSTS,
            unit_size=D("0"),
        )


# ---- signal -> plan -------------------------------------------------------------------


def _signal(invalidation: Decimal | None, targets: tuple[SignalTarget, ...]) -> ResearchSignal:
    decision = SignalDecision(
        action="BUY",
        score=70,
        entry_zone=None,
        invalidation_price=invalidation,
        invalidation_reason=None,
        targets=targets,
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
        side="long",
        sequence=1,
        occurrence_time=T0,
        confirmation_time=DECISION,
        decision_time=DECISION,
        reasons=(),
        reason_codes=(),
        source_refs=("ref",),
        config_version="t",
        engine_versions=("t",),
        status="active",
        decision=decision,
    )


def test_plan_from_signal_uses_the_named_target_only() -> None:
    sig = _signal(
        D("98"), (SignalTarget("TP1", D("103"), "rr"), SignalTarget("TP2", D("105"), "rr"))
    )
    assert plan_from_signal(sig, target_name="TP1").target_price == D("103")
    assert plan_from_signal(sig, target_name="TP2").target_price == D("105")
    assert plan_from_signal(sig, target_name="TP1").stop_price == D("98")


def test_plan_from_signal_fails_closed_on_missing_levels() -> None:
    with pytest.raises(ExitInputError, match="signal_missing_invalidation"):
        plan_from_signal(_signal(None, (SignalTarget("TP1", D("103"), "rr"),)), target_name="TP1")
    with pytest.raises(ExitInputError, match="signal_missing_target"):
        plan_from_signal(
            _signal(D("98"), (SignalTarget("TP1", D("103"), "rr"),)), target_name="TP2"
        )
    no_decision = replace(_signal(D("98"), ()), decision=None)
    with pytest.raises(ExitInputError, match="signal_missing_invalidation"):
        plan_from_signal(no_decision, target_name="TP1")
