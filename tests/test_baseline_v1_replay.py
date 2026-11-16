"""Synthetic proofs for the P&F Baseline V1 historical replay harness.

Every price, cost, size and policy value below is a synthetic test vector, never a
proposed trading setting. Expected values are hand-computed oracles written out in each
test rather than derived from the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from nexora.artifacts import canonical_hash
from nexora.backtest.baseline_v1 import (
    AccountConfig,
    BaselineV1Replay,
    CostModel,
    DataAuthorization,
    EvidenceCriteria,
    InstrumentCapabilities,
    ReplayInputError,
    ReplayPolicy,
    ReplayResult,
    UtcRange,
)
from nexora.backtest.baseline_v1.models import ReplayMetrics
from nexora.backtest.baseline_v1.replay import _verdict
from nexora.backtest.datasets import manifest_for
from nexora.entry_readiness import EntryReadinessSnapshot
from nexora.entry_readiness.models import EntryReadinessState
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_regime import RegimeSnapshot
from nexora.market_regime.models import RegimeState
from nexora.pnf import PnfConfig
from nexora.pnf.engine import PnfEngine
from nexora.research import PipelineConfig
from nexora.risk.models import RiskPolicy
from nexora.signals import ResearchSignal
from nexora.signals.models import SignalDecision, SignalTarget

from tests.validation_fixtures import pipeline_config, tick_events

START = datetime(2026, 3, 2, 9, tzinfo=UTC)
SYMBOL = "XAUUSD"


# ----------------------------------------------------------------------------- fixtures
def q(index: int, bid: str, ask: str, *, seconds: int | None = None) -> NormalizedPriceEvent:
    at = START + timedelta(seconds=10 * index if seconds is None else seconds)
    return NormalizedPriceEvent(
        1,
        f"q:{index}",
        "synthetic-test",
        SYMBOL,
        "tick",
        at,
        at,
        index,
        index,
        f"q:{index}",
        "bid",
        "USD/oz",
        2,
        D(bid),
        bid=D(bid),
        ask=D(ask),
    )


def caps() -> InstrumentCapabilities:
    return InstrumentCapabilities(
        instrument_id="synthetic:xau-like",
        symbol=SYMBOL,
        account_currency="USD",
        contract_multiplier=D("1"),
        quantity_step=D("0.01"),
        price_precision=2,
        source="synthetic-capability-profile",
        version="caps-test-v1",
    )


def risk_policy(**changes: object) -> RiskPolicy:
    base = RiskPolicy(
        version="risk-test-v1",
        currency="USD",
        max_risk_per_trade=D("10"),
        max_total_exposure=D("1000"),
        max_daily_loss=D("1000"),
        max_drawdown=D("1000"),
        min_stop_distance=D("0.01"),
        size_step=D("0.01"),
        max_input_age_seconds=10,
        approval_ttl_seconds=60,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def costs(**changes: object) -> CostModel:
    base = CostModel(
        version="cost-test-v1",
        currency="USD",
        commission_per_unit_per_side=D("0.5"),
        slippage_price_units_per_market_fill=D("0.1"),
        swap_long_per_unit_per_rollover=D("0"),
        swap_short_per_unit_per_rollover=D("0"),
        rollover_hour_utc=21,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def policy(**changes: object) -> ReplayPolicy:
    base = ReplayPolicy(
        version="replay-test-v1",
        entry_latency_seconds=D("0"),
        max_entry_quote_age_seconds=D("60"),
        max_hold_seconds=None,
        requested_size=D("2"),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def auth(events: tuple[NormalizedPriceEvent, ...], **changes: object) -> DataAuthorization:
    base = DataAuthorization(
        data_use="synthetic",
        instrument_id="synthetic:xau-like",
        source="synthetic-test",
        development=UtcRange(
            events[0].event_time, events[-1].event_time + timedelta(seconds=1), "dev"
        ),
        warmup=None,
        protected=(),
        protected_registry_complete=True,
        registry_authority="synthetic-test-registry",
        registry_version="synthetic-v1",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Script:
    side: str
    stop: str
    tp1: str
    readiness: EntryReadinessState = "READY"
    regime: str = "trend"


class ScriptedSource:
    """Emits a fixed signal at chosen indexes; mechanics tests only."""

    def __init__(self, script: dict[int, Script]) -> None:
        self.script = script
        self.index = -1
        self.history: list[ResearchSignal] = []
        self.readiness: EntryReadinessSnapshot | None = None
        self.regime: RegimeSnapshot | None = None

    def replay(self, event: NormalizedPriceEvent) -> None:
        self.index += 1
        spec = self.script.get(self.index)
        if spec is None:
            self.readiness = EntryReadinessSnapshot(1, SYMBOL, "NOT_READY", "WAIT", (), (), "v")
            return
        action = "BUY" if spec.side == "long" else "SELL"
        decision = SignalDecision(
            action=action,  # type: ignore[arg-type]
            score=80,
            entry_zone=None,
            invalidation_price=D(spec.stop),
            invalidation_reason="scripted",
            targets=(SignalTarget("TP1", D(spec.tp1), "scripted"),),
            risk_reward=None,
            patterns=(),
            positive_evidence=(),
            negative_evidence=(),
            future_conditions=(),
            config_version="scripted",
            engine_version="scripted",
            source_refs=(event.identity_key,),
        )
        self.history.append(
            ResearchSignal(
                signal_id=f"s:{self.index}:{spec.side}",
                symbol=SYMBOL,
                side=spec.side,  # type: ignore[arg-type]
                sequence=self.index,
                occurrence_time=event.event_time,
                confirmation_time=event.received_at,
                decision_time=event.received_at,
                reasons=("scripted",),
                reason_codes=("scripted",),
                source_refs=(event.identity_key,),
                config_version="scripted",
                engine_versions=("scripted",),
                status="active",
                decision=decision,
            )
        )
        self.readiness = EntryReadinessSnapshot(1, SYMBOL, spec.readiness, action, (), (), "v")  # type: ignore[arg-type]
        self.regime = RegimeSnapshot(
            1,
            SYMBOL,
            self.index,
            RegimeState(spec.regime, "scripted", event.event_time, None, "v"),  # type: ignore[arg-type]
        )

    def research_signals(self) -> tuple[ResearchSignal, ...]:
        return tuple(self.history)

    def latest_entry_readiness(self) -> EntryReadinessSnapshot | None:
        return self.readiness

    def latest_regime(self) -> RegimeSnapshot | None:
        return self.regime


def harness(
    script: dict[int, Script] | None = None,
    *,
    pol: ReplayPolicy | None = None,
    cost: CostModel | None = None,
    risk: RiskPolicy | None = None,
    config: PipelineConfig | None = None,
) -> BaselineV1Replay:
    return BaselineV1Replay(
        pipeline_config=config or pipeline_config(),
        policy=pol or policy(),
        costs=cost or costs(),
        account=AccountConfig("acct-synthetic", "USD", D("1000"), risk or risk_policy()),
        capabilities=caps(),
        decision_source_factory=(lambda _c: ScriptedSource(script)) if script is not None else None,
    )


def run(
    h: BaselineV1Replay,
    events: tuple[NormalizedPriceEvent, ...],
    *,
    authorization: DataAuthorization | None = None,
    criteria: EvidenceCriteria | None = None,
) -> ReplayResult:
    dataset = manifest_for(events, quality="complete")
    return h.run(
        events=events,
        dataset=dataset,
        expected_dataset_hash=canonical_hash(dataset),
        authorization=authorization or auth(events),
        criteria=criteria,
        code_sha="0" * 40,
        code_dirty=False,
    )


def path(*quotes: tuple[str, str]) -> tuple[NormalizedPriceEvent, ...]:
    return tuple(q(i, b, a) for i, (b, a) in enumerate(quotes))


# ------------------------------------------------------------------------- gate proofs
def test_readiness_not_ready_blocks_entry() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    states: tuple[EntryReadinessState, ...] = ("BLOCKED", "DEVELOPING", "NOT_READY")
    for state in states:
        result = run(harness({0: Script("long", "95", "105", readiness=state)}), events)
        assert result.trades == ()
        assert [o.outcome for o in result.opportunities] == ["readiness_not_ready"]
        assert result.opportunities[0].readiness_state == state


def test_ready_control_enters_with_same_path() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    assert [o.outcome for o in result.opportunities] == ["entered"]


def test_risk_rejection_blocks_entry() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    # stop distance 5.2 money/unit is below the synthetic minimum of 6 -> RiskEngine rejects
    result = run(
        harness({0: Script("long", "95", "105")}, risk=risk_policy(min_stop_distance=D("6"))),
        events,
    )
    assert result.trades == ()
    assert result.opportunities[0].outcome == "risk_rejected"
    assert result.opportunities[0].risk_reason == "stop_distance_too_small"


def test_risk_sizes_by_stop_distance() -> None:
    # max_risk 10 / stop distance (100.2-95=5.2) = 1.923 -> floor 0.01 = 1.92 < requested 2
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    assert result.trades[0].quantity == D("1.92")


# --------------------------------------------------------------------------- exits
def test_long_tp1_full_exit_exact_pnl() -> None:
    # decide@0, fill@1 ask 101.2 (+0.1 slip = 101.3), TP1 105 touched by bid 106 @2.
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    result = run(
        harness({0: Script("long", "95", "105")}, risk=risk_policy(max_risk_per_trade=D("100"))),
        events,
    )
    (trade,) = result.trades
    assert (trade.status, trade.exit_reason, trade.exit_price) == ("closed", "tp1", D("105"))
    assert trade.quantity == D("2")  # full requested size, closed 100% at TP1
    assert trade.entry_price == D("101.3")
    assert trade.gross_pnl == (D("105") - D("101.2")) * 2  # 7.6
    assert trade.costs.slippage == D("0.1") * 2  # entry market fill only; TP1 limit
    assert trade.costs.commission == D("0.5") * 2 * 2
    assert trade.net_pnl == D("7.6") - D("0.2") - D("2")
    assert result.metrics.final_equity == D("1000") + trade.net_pnl


def test_long_stop_gap_fills_worse_than_stop() -> None:
    # stop 95; bid gaps from 101 to 90 -> stop-market fills at 90 - 0.1, not at 95
    events = path(("100", "100.2"), ("101", "101.2"), ("90", "90.2"), ("110", "110.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    (trade,) = result.trades
    assert (trade.exit_reason, trade.exit_index, trade.exit_price) == ("stop", 2, D("89.9"))
    q_ = trade.quantity
    assert trade.gross_pnl == (D("90") - D("101.2")) * q_
    assert trade.net_pnl == trade.gross_pnl - D("0.2") * q_ - D("1") * q_


def test_stop_breach_then_recovery_still_exits() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("94.9", "95.1"), ("106", "106.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    assert result.trades[0].exit_reason == "stop"
    assert result.trades[0].exit_index == 2


def test_short_uses_ask_for_stop_and_target() -> None:
    # short: entry at bid 99 - 0.1; stop 105 checked on ask; TP1 95 checked on ask
    events = path(("100", "100.2"), ("99", "99.2"), ("94.7", "94.9"))
    result = run(
        harness({0: Script("short", "105", "95")}, risk=risk_policy(max_risk_per_trade=D("100"))),
        events,
    )
    (trade,) = result.trades
    assert trade.entry_price == D("98.9")
    assert (trade.exit_reason, trade.exit_price) == ("tp1", D("95"))
    assert trade.gross_pnl == (D("99") - D("95")) * 2
    # ask 105.1 hits the short stop even though bid 104.9 does not
    events2 = path(("100", "100.2"), ("99", "99.2"), ("104.9", "105.1"))
    stopped = run(harness({0: Script("short", "105", "95")}), events2).trades[0]
    assert (stopped.exit_reason, stopped.exit_price) == ("stop", D("105.2"))


def test_no_exit_on_entry_quote_and_entry_through_stop_rejected() -> None:
    events = path(("100", "100.2"), ("94", "94.2"), ("106", "106.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    assert result.trades == ()
    assert result.opportunities[0].outcome == "entry_through_stop"
    events2 = path(("100", "100.2"), ("105.5", "105.7"))
    assert run(harness({0: Script("long", "95", "105")}), events2).opportunities[0].outcome == (
        "entry_through_target"
    )


def test_max_hold_time_exit() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("102", "102.2"), ("103", "103.2"))
    result = run(harness({0: Script("long", "95", "110")}, pol=policy(max_hold_seconds=20)), events)
    (trade,) = result.trades
    # entry @10s, hold 20s -> first quote at >=30s is index 3, bid 103 - slip 0.1
    assert (trade.exit_reason, trade.exit_index, trade.exit_price) == ("max_hold", 3, D("102.9"))


def test_single_position_skips_overlapping_signal() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("102", "102.2"), ("106", "106.2"))
    result = run(harness({0: Script("long", "95", "105"), 2: Script("long", "96", "110")}), events)
    assert [o.outcome for o in result.opportunities] == ["entered", "position_busy"]
    assert len(result.trades) == 1


def test_open_position_at_end_is_censored_and_marked() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("99", "99.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    (trade,) = result.trades
    assert trade.status == "open_censored" and trade.exit_reason is None
    assert result.metrics.closed_trades == 0 and result.metrics.open_censored == 1
    qty = trade.quantity
    marked = (D("99") - D("101.2")) * qty - D("0.1") * qty - D("0.5") * qty
    assert trade.net_pnl == marked
    assert result.metrics.final_equity == D("1000") + marked


def test_floating_loss_counts_in_portfolio_drawdown() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("96", "96.2"), ("106", "106.2"))
    result = run(harness({0: Script("long", "95", "105")}), events)
    qty = result.trades[0].quantity
    # equity low at index 2: 1000 - commission(0.5q) + (96 - 101.3) q
    low = D("1000") - D("0.5") * qty + (D("96") - D("101.3")) * qty
    assert result.trades[0].exit_reason == "tp1"
    assert result.metrics.max_drawdown == D("1000") - low
    assert result.metrics.net_pnl > 0  # closed result alone would hide the drawdown


# --------------------------------------------------------------------------- costs
def test_costs_change_net_not_gross() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    low = run(harness({0: Script("long", "95", "105")}), events).trades[0]
    high = run(
        harness(
            {0: Script("long", "95", "105")}, cost=costs(commission_per_unit_per_side=D("1.5"))
        ),
        events,
    ).trades[0]
    assert low.gross_pnl == high.gross_pnl
    assert low.net_pnl - high.net_pnl == D("1") * 2 * low.quantity


def test_spread_costs_money_with_unchanged_mid() -> None:
    # mid stays 100 at entry and exit; long buys ask 100.5, max-hold exit sells bid 99.5
    events = path(("99.5", "100.5"), ("99.5", "100.5"), ("99.5", "100.5"))
    result = run(
        harness(
            {0: Script("long", "90", "110")},
            pol=policy(max_hold_seconds=10),
            cost=costs(
                commission_per_unit_per_side=D("0"), slippage_price_units_per_market_fill=D("0")
            ),
        ),
        events,
    )
    trade = result.trades[0]
    assert trade.gross_pnl == D("-1") * trade.quantity
    assert trade.costs.spread == D("2") * trade.quantity  # informational, not charged twice
    assert trade.net_pnl == trade.gross_pnl


def test_unknown_commission_blocks_net_claim() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    result = run(
        harness({0: Script("long", "95", "105")}, cost=costs(commission_per_unit_per_side=None)),
        events,
        criteria=criteria(),
    )
    assert result.metrics.net_complete is False
    assert result.verdict == "BLOCKED"
    assert "unknown_costs_block_net_claim" in result.verdict_reasons


def test_swap_charged_per_rollover_and_unknown_swap_flagged() -> None:
    day = 24 * 3600
    events = (
        q(0, "100", "100.2", seconds=0),
        q(1, "101", "101.2", seconds=10),
        q(2, "102", "102.2", seconds=2 * day),  # crosses two 21:00 UTC rollovers
        q(3, "106", "106.2", seconds=2 * day + 10),
    )
    known = run(
        harness(
            {0: Script("long", "95", "105")}, cost=costs(swap_long_per_unit_per_rollover=D("0.3"))
        ),
        events,
    ).trades[0]
    assert known.costs.swap == D("0.3") * 2 * known.quantity
    unknown = run(
        harness({0: Script("long", "95", "105")}, cost=costs(swap_long_per_unit_per_rollover=None)),
        events,
    )
    assert unknown.trades[0].costs.swap_unknown_rollovers == 2
    assert unknown.metrics.net_complete is False


# ---------------------------------------------------------------------- preflight
def test_preflight_fails_closed() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    h = harness({0: Script("long", "95", "105")})
    protected = UtcRange(events[1].event_time, events[1].event_time + timedelta(seconds=1), "hold")
    cases = {
        "protected_registry_incomplete": auth(events, protected_registry_complete=False),
        "scope_overlaps_protected_range": auth(events, protected=(protected,)),
        "instrument_not_authorized": auth(events, instrument_id="other"),
        "event_source_or_symbol_not_authorized": auth(events, source="other-feed"),
    }
    for code, a in cases.items():
        with pytest.raises(ReplayInputError) as err:
            run(h, events, authorization=a)
        assert err.value.code == code
    outside = auth(events, development=UtcRange(events[0].event_time, events[1].event_time, "d"))
    with pytest.raises(ReplayInputError, match="event_outside_authorized_scope"):
        run(h, events, authorization=outside)
    no_ask = (events[0], replace(events[1], ask=None), events[2])
    with pytest.raises(ReplayInputError, match="tick_bid_ask_required"):
        run(h, no_ask, authorization=auth(events))
    crossed = (events[0], replace(events[1], ask=D("100")), events[2])
    with pytest.raises(ReplayInputError, match="invalid_quote"):
        run(h, crossed, authorization=auth(events))
    dataset = manifest_for(events, quality="complete")
    with pytest.raises(ReplayInputError, match="dataset_hash_mismatch"):
        h.run(
            events=events,
            dataset=dataset,
            expected_dataset_hash="0" * 64,
            authorization=auth(events),
            criteria=None,
            code_sha="0" * 40,
            code_dirty=False,
        )


def test_duplicate_identity_rejected_so_reprocessed_seed_cannot_double_count() -> None:
    events = path(("100", "100.2"), ("101", "101.2"))
    dup = events + (replace(events[0], source_sequence=9, source_order=9),)
    with pytest.raises(ReplayInputError, match="duplicate_event_identity"):
        run(harness({}), dup, authorization=auth(events))


def test_pnf_engine_restore_seed_identity_defect_is_characterized() -> None:
    """#104 finding, retained: the harness never restores engine snapshots mid-run."""
    engine = PnfEngine((PnfConfig(SYMBOL, D("1"), 3, 2, "bid", "v"),))
    first, second = q(0, "100", "100.2"), q(1, "104", "104.2")
    engine.process(first)
    engine.process(second)
    restored = PnfEngine.from_snapshot(engine.snapshot())
    # Known defect (owned by DEV-PNF): seed identity is not persisted, so a re-fed seed
    # event is not recognised as a duplicate after restore and appends a transition.
    restored.process(first)
    assert restored.snapshot() != engine.snapshot()


def test_warmup_builds_state_without_booking_trades() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("102", "102.2"), ("106", "106.2"))
    a = auth(
        events,
        warmup=UtcRange(events[0].event_time, events[2].event_time, "warm"),
        development=UtcRange(
            events[2].event_time, events[3].event_time + timedelta(seconds=1), "d"
        ),
    )
    result = run(
        harness({0: Script("long", "95", "105"), 2: Script("long", "95", "105")}),
        events,
        authorization=a,
    )
    assert [o.decision_index for o in result.opportunities] == [2]


# ------------------------------------------------------- real pipeline (end to end)
def real_events(count: int = 400) -> tuple[NormalizedPriceEvent, ...]:
    return tick_events(count)


def real_harness() -> BaselineV1Replay:
    return harness(None, risk=risk_policy(max_risk_per_trade=D("50")))


def test_real_pipeline_emits_and_gates_opportunities() -> None:
    events = real_events()
    result = run(real_harness(), events)
    assert result.manifest.decision_source == "research_pipeline"
    assert result.opportunities, "fixture must exercise the full chain"
    for opp in result.opportunities:
        if opp.outcome == "entered":
            assert opp.readiness_state == "READY"
    entered = {o.signal_id for o in result.opportunities if o.outcome == "entered"}
    assert entered == {t.signal_id for t in result.trades}
    for trade in result.trades:
        assert trade.entry_index > trade.decision_index  # never fills on the decision event
        if trade.exit_index is not None:
            assert trade.exit_index > trade.entry_index


def test_real_pipeline_replay_is_deterministic() -> None:
    events = real_events()
    first = run(real_harness(), events)
    second = run(real_harness(), events)
    assert first.result_hash == second.result_hash
    assert first == second


def test_no_look_ahead_suffix_mutation_does_not_change_prefix_decisions() -> None:
    events = real_events()
    cut = 250
    mutated = events[:cut] + tuple(
        replace(e, price=e.price + 50, bid=e.price + 50, ask=e.price + D("50.3"))
        for e in events[cut:]
    )
    base = run(real_harness(), events)
    other = run(real_harness(), mutated, authorization=auth(events))
    prefix = run(real_harness(), events[:cut], authorization=auth(events))

    def decided_before(result: ReplayResult) -> list[tuple[str, int, str, str]]:
        return [
            (o.signal_id, o.decision_index, o.readiness_state, o.regime)
            for o in result.opportunities
            if o.decision_index < cut
        ]

    assert decided_before(base) == decided_before(other) == decided_before(prefix)

    def closed_before(result: ReplayResult) -> list[object]:
        return [t for t in result.trades if t.exit_index is not None and t.exit_index < cut]

    assert closed_before(base) == closed_before(other) == closed_before(prefix)


def test_manifest_pins_inputs() -> None:
    events = real_events(120)
    result = run(real_harness(), events)
    m = result.manifest
    assert m.events_hash == canonical_hash(events)
    assert m.event_count == 120 and m.data_use == "synthetic"
    other = run(harness(None, risk=risk_policy(max_risk_per_trade=D("49"))), events)
    assert other.manifest.config_hash != m.config_hash
    assert result.verdict == "BLOCKED"
    assert "synthetic_data_is_not_market_evidence" in result.verdict_reasons


# ------------------------------------------------------------------------ verdict
def criteria(**changes: object) -> EvidenceCriteria:
    base = EvidenceCriteria(
        version="criteria-test-v1",
        registered_at=START,
        min_closed_trades=3,
        confidence_level=D("0.95"),
        min_profit_factor=D("1.1"),
        bootstrap_resamples=200,
        bootstrap_seed=7,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def metrics(**changes: object) -> ReplayMetrics:
    base = ReplayMetrics(
        closed_trades=10,
        open_censored=0,
        wins=6,
        losses=4,
        win_rate=D("0.6"),
        gross_pnl=D("10"),
        total_costs=D("2"),
        net_pnl=D("8"),
        expectancy=D("0.8"),
        profit_factor=D("1.5"),
        max_drawdown=D("3"),
        max_drawdown_pct=D("0.003"),
        final_equity=D("1008"),
        expectancy_ci_low=D("0.1"),
        expectancy_ci_high=D("1.5"),
        net_complete=True,
        by_regime=(),
        opportunity_funnel=(),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_verdict_rules_are_preregistered_and_ordered() -> None:
    events = path(("100", "100.2"), ("101", "101.2"))
    dev = auth(events, data_use="development")
    c = criteria()
    assert _verdict(metrics(), dev, c)[0] == "SUPPORTS_EDGE"
    assert _verdict(metrics(closed_trades=2), dev, c)[0] == "INSUFFICIENT_EVIDENCE"
    assert _verdict(metrics(expectancy_ci_low=D("-0.1")), dev, c)[0] == "INSUFFICIENT_EVIDENCE"
    assert _verdict(metrics(profit_factor=D("1.05")), dev, c)[0] == "INSUFFICIENT_EVIDENCE"
    assert (
        _verdict(metrics(expectancy_ci_low=D("-2"), expectancy_ci_high=D("-0.1")), dev, c)[0]
        == "NO_EDGE"
    )
    assert _verdict(metrics(), dev, None)[0] == "BLOCKED"
    assert _verdict(metrics(), auth(events), c)[0] == "BLOCKED"
    assert _verdict(metrics(net_complete=False), dev, c)[0] == "BLOCKED"


# ------------------------------------------------- security review round 1 (sec-r1)
def test_price_must_match_quote_for_price_source() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    h = harness({0: Script("long", "95", "105")})
    lying = (events[0], replace(events[1], price=D("150")), events[2])
    with pytest.raises(ReplayInputError, match="price_quote_mismatch"):
        run(h, lying, authorization=auth(events))
    other_source = tuple(replace(e, price_source="ask", price=e.ask) for e in events)  # type: ignore[arg-type]
    with pytest.raises(ReplayInputError, match="price_source_mismatch"):
        run(h, other_source, authorization=auth(events))


def test_development_run_requires_nonempty_attested_registry() -> None:
    events = path(("100", "100.2"), ("101", "101.2"))
    h = harness({})
    with pytest.raises(ReplayInputError, match="protected_registry_empty"):
        run(h, events, authorization=auth(events, data_use="development"))
    with pytest.raises(ReplayInputError, match="protected_registry_incomplete"):
        run(h, events, authorization=auth(events, registry_version=""))
    with pytest.raises(ReplayInputError, match="invalid_data_use"):
        run(h, events, authorization=auth(events, data_use="holdout"))


def test_decision_time_checked_against_protected_and_scope() -> None:
    events = path(("100", "100.2"), ("101", "101.2"))
    late = (events[0], replace(events[1], received_at=events[1].event_time + timedelta(hours=1)))
    with pytest.raises(ReplayInputError, match="event_outside_authorized_scope"):
        run(harness({}), late, authorization=auth(events))
    hold = UtcRange(
        events[1].event_time + timedelta(seconds=30), events[1].event_time + timedelta(hours=2), "h"
    )
    dev = UtcRange(events[0].event_time, events[1].event_time + timedelta(seconds=30), "d")
    with pytest.raises(ReplayInputError, match="protected_range_event"):
        run(
            harness({}),
            late,
            authorization=auth(events, development=dev, protected=(hold,)),
        )


def test_code_identity_is_validated_and_dirty_code_blocks_evidence() -> None:
    events = path(("100", "100.2"), ("101", "101.2"))
    dataset = manifest_for(events, quality="complete")
    with pytest.raises(ReplayInputError, match="invalid_code_sha"):
        harness({}).run(
            events=events,
            dataset=dataset,
            expected_dataset_hash=canonical_hash(dataset),
            authorization=auth(events),
            criteria=None,
            code_sha="",
            code_dirty=False,
        )
    dev = auth(events, data_use="development")
    verdict, reasons = _verdict(metrics(), dev, criteria(), code_dirty=True)
    assert verdict == "BLOCKED" and "code_not_clean_commit" in reasons


def test_criteria_require_two_trades_and_cost_model_rejects_missing_slippage() -> None:
    with pytest.raises(ReplayInputError, match="invalid_evidence_criteria"):
        criteria(min_closed_trades=1)
    with pytest.raises(ReplayInputError, match="invalid_cost_model"):
        costs(slippage_price_units_per_market_fill=None)


def test_remaining_preflight_checks_fail_closed() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("102", "102.2"))
    h = harness({})
    bad_warmup = auth(
        events,
        warmup=UtcRange(events[0].event_time, events[2].event_time, "w"),
        development=UtcRange(events[1].event_time, events[2].event_time + timedelta(1), "d"),
    )
    with pytest.raises(ReplayInputError, match="warmup_must_precede_development"):
        run(h, events, authorization=bad_warmup)
    with pytest.raises(ReplayInputError, match="empty_events"):
        h.run(
            events=(),
            dataset=manifest_for(events, quality="complete"),
            expected_dataset_hash=canonical_hash(manifest_for(events, quality="complete")),
            authorization=auth(events),
            criteria=None,
            code_sha="0" * 40,
            code_dirty=False,
        )
    partial = manifest_for(events, quality="partial")
    with pytest.raises(ReplayInputError, match="dataset_quality_not_complete"):
        h.run(
            events=events,
            dataset=partial,
            expected_dataset_hash=canonical_hash(partial),
            authorization=auth(events),
            criteria=None,
            code_sha="0" * 40,
            code_dirty=False,
        )
    flagged = (events[0], replace(events[1], is_gap=False, is_out_of_order=True), events[2])
    with pytest.raises(ReplayInputError, match="noncanonical_event"):
        run(h, flagged, authorization=auth(events))
    # events that differ from the manifest they claim to be
    dataset = manifest_for(events, quality="complete")
    changed = replace(events[1], price=D("101.2"), bid=D("101.2"), ask=D("101.4"))
    swapped = (events[0], changed, events[2])
    with pytest.raises(ReplayInputError):
        h.run(
            events=swapped,
            dataset=dataset,
            expected_dataset_hash=canonical_hash(dataset),
            authorization=auth(events),
            criteria=None,
            code_sha="0" * 40,
            code_dirty=False,
        )


def test_manifest_hashes_bind_costs_and_authorization() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("102", "102.2"))
    base = run(harness({}), events).manifest
    cost_changed = run(
        harness({}, cost=costs(slippage_price_units_per_market_fill=D("0.2"))), events
    )
    assert cost_changed.manifest.config_hash != base.config_hash
    other_source = run(
        harness({}),
        events,
        authorization=auth(events, registry_authority="another-custodian"),
    )
    assert other_source.manifest.protected_registry_hash != base.protected_registry_hash


# --------------------------------------------- quant review round 1 (quant-r1)
def test_risk_approval_expiry_and_stale_entry_quote_cancel_entry() -> None:
    # decision @0s; next quote @100s. TTL 60 -> approval expired before the quote.
    events = (q(0, "100", "100.2", seconds=0), q(1, "101", "101.2", seconds=100))
    expired = run(harness({0: Script("long", "95", "105")}), events)
    assert expired.opportunities[0].outcome == "risk_approval_expired"
    assert expired.trades == ()
    # TTL long enough, but quote age limit 30 s after eligibility -> stale
    stale = run(
        harness(
            {0: Script("long", "95", "105")},
            risk=risk_policy(approval_ttl_seconds=600),
            pol=policy(max_entry_quote_age_seconds=D("30")),
        ),
        events,
    )
    assert stale.opportunities[0].outcome == "stale_entry_quote"


class MismatchSource(ScriptedSource):
    def replay(self, event: NormalizedPriceEvent) -> None:
        super().replay(event)
        if self.readiness is not None and self.readiness.signal_action == "BUY":
            self.readiness = replace(self.readiness, signal_action="SELL")


def test_readiness_action_mismatch_blocks_entry() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    h = BaselineV1Replay(
        pipeline_config=pipeline_config(),
        policy=policy(),
        costs=costs(),
        account=AccountConfig("acct-synthetic", "USD", D("1000"), risk_policy()),
        capabilities=caps(),
        decision_source_factory=lambda _c: MismatchSource({0: Script("long", "95", "105")}),
    )
    assert run(h, events).opportunities[0].outcome == "readiness_mismatch"


def test_contract_multiplier_scales_risk_and_pnl() -> None:
    events = path(("100", "100.2"), ("101", "101.2"), ("106", "106.2"))
    h = harness(
        {0: Script("long", "95", "105")},
        risk=risk_policy(
            max_risk_per_trade=D("1040"), max_total_exposure=D("5000"), max_daily_loss=D("5000")
        ),
    )
    h.capabilities = replace(caps(), contract_multiplier=D("100"))
    (trade,) = run(h, events).trades
    # stop distance money/unit = (100.2-95)*100 = 520 -> size min(2, 1040/520=2) = 2
    assert trade.quantity == D("2")
    assert trade.gross_pnl == (D("105") - D("101.2")) * 2 * 100


def test_rollover_counting_is_timezone_independent() -> None:
    from datetime import timezone

    bkk = timezone(timedelta(hours=7))
    day = 24 * 3600
    events = (
        q(0, "100", "100.2", seconds=0),
        q(1, "101", "101.2", seconds=10),
        q(2, "102", "102.2", seconds=3 * day),
        q(3, "106", "106.2", seconds=3 * day + 10),
    )
    shifted = tuple(
        replace(
            e, event_time=e.event_time.astimezone(bkk), received_at=e.received_at.astimezone(bkk)
        )
        for e in events
    )
    cost = costs(swap_long_per_unit_per_rollover=D("1"))
    utc = run(harness({0: Script("long", "95", "105")}, cost=cost), events).trades[0]
    local = run(
        harness({0: Script("long", "95", "105")}, cost=cost), shifted, authorization=auth(events)
    ).trades[0]
    assert utc.costs.swap == local.costs.swap == D("3") * utc.quantity
