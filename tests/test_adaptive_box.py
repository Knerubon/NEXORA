from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.adaptive_box import AdaptiveBoxConfig, AdaptiveBoxSizer, AdaptivePnfRunner
from nexora.market_data.models import NormalizedPriceEvent
from nexora.pnf import PnfConfig, PnfEngine, PnfTransition, exact_threshold_fixture


def _pnf_config(*, symbol: str = "XAUUSD") -> PnfConfig:
    return PnfConfig(
        symbol=symbol,
        box_size=Decimal("1.0"),
        reversal_boxes=2,
        price_precision=1,
        price_source="ask",
        version="p3-fixed-v1",
    )


def _event(sequence: int, price: str) -> NormalizedPriceEvent:
    event_time = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=sequence)
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=f"xau-ask-{sequence}",
        source="MT5",
        symbol="XAUUSD",
        kind="tick",
        event_time=event_time,
        received_at=event_time + timedelta(milliseconds=100),
        source_sequence=sequence,
        source_order=sequence,
        source_event_id=f"evt-{sequence}",
        price_source="ask",
        units="USD/oz",
        precision=1,
        price=Decimal(price),
        bid=Decimal(price) - Decimal("0.1"),
        ask=Decimal(price),
    )


def test_fixed_mode_matches_p3_baseline_output() -> None:
    events = exact_threshold_fixture()
    baseline = PnfEngine(configs=(_pnf_config(),))
    baseline_transitions: list[PnfTransition] = []
    for event in events:
        baseline_transitions.extend(baseline.process(event))

    fixed_sizer = AdaptiveBoxSizer(
        AdaptiveBoxConfig(
            mode="fixed",
            fixed_box_size=Decimal("1.0"),
            price_precision=1,
            rule_version="p3-fixed-v1",
        )
    )
    adaptive_runner = AdaptivePnfRunner((_pnf_config(),), fixed_sizer)
    adaptive_transitions: list[PnfTransition] = []
    for event in events:
        adaptive_transitions.extend(adaptive_runner.process(event))

    assert adaptive_transitions == baseline_transitions


def test_append_future_events_does_not_change_prefix() -> None:
    base_events = (
        _event(1, "100.0"),
        _event(2, "100.5"),
        _event(3, "101.3"),
        _event(4, "101.9"),
    )
    future_events = base_events + (_event(5, "103.2"), _event(6, "101.1"))
    config = AdaptiveBoxConfig(
        mode="atr",
        fixed_box_size=Decimal("1.0"),
        price_precision=1,
        rule_version="atr-v1",
        atr_period=2,
        atr_multiplier=Decimal("2.0"),
        min_box_size=Decimal("0.2"),
        max_box_size=Decimal("2.0"),
    )

    first_runner = AdaptivePnfRunner((_pnf_config(),), AdaptiveBoxSizer(config))
    first_transitions: list[PnfTransition] = []
    for event in base_events:
        first_transitions.extend(first_runner.process(event))

    second_runner = AdaptivePnfRunner((_pnf_config(),), AdaptiveBoxSizer(config))
    second_transitions: list[PnfTransition] = []
    for event in future_events:
        second_transitions.extend(second_runner.process(event))

    assert second_transitions[: len(first_transitions)] == first_transitions


def test_warmup_zero_volatility_and_clamp_policy() -> None:
    config = AdaptiveBoxConfig(
        mode="atr",
        fixed_box_size=Decimal("1.0"),
        price_precision=1,
        rule_version="atr-v1",
        atr_period=2,
        atr_multiplier=Decimal("10.0"),
        min_box_size=Decimal("0.5"),
        max_box_size=Decimal("2.0"),
    )
    sizer = AdaptiveBoxSizer(config)
    decisions = [
        sizer.decide(_event(1, "100.0")),
        sizer.decide(_event(2, "100.0")),
        sizer.decide(_event(3, "100.0")),
        sizer.decide(_event(4, "102.5")),
    ]

    assert decisions[0].warmup is True
    assert decisions[0].effective_box_size == Decimal("1.0")
    assert decisions[1].warmup is True
    assert decisions[2].reason == "zero_volatility_hold_last"
    assert decisions[2].effective_box_size == Decimal("1.0")
    assert decisions[3].effective_box_size == Decimal("2.0")


def test_gap_move_and_transition_trace_contains_effective_box() -> None:
    config = AdaptiveBoxConfig(
        mode="atr",
        fixed_box_size=Decimal("1.0"),
        price_precision=1,
        rule_version="atr-v1",
        atr_period=2,
        atr_multiplier=Decimal("1.5"),
        min_box_size=Decimal("0.5"),
        max_box_size=Decimal("3.0"),
    )
    runner = AdaptivePnfRunner((_pnf_config(),), AdaptiveBoxSizer(config))
    events = (_event(1, "100.0"), _event(2, "101.0"), _event(3, "105.0"))
    transitions: list[PnfTransition] = []
    for event in events:
        transitions.extend(runner.process(event))

    assert transitions
    for transition in transitions:
        assert transition.effective_box_size > 0
        assert transition.sizing_rule_version == "atr-v1"


def test_snapshot_restart_is_deterministic_for_adaptive_runner() -> None:
    config = AdaptiveBoxConfig(
        mode="atr",
        fixed_box_size=Decimal("1.0"),
        price_precision=1,
        rule_version="atr-v1",
        atr_period=2,
        atr_multiplier=Decimal("2.0"),
        min_box_size=Decimal("0.5"),
        max_box_size=Decimal("2.0"),
    )
    events = (
        _event(1, "100.0"),
        _event(2, "101.2"),
        _event(3, "102.0"),
        _event(4, "101.0"),
        _event(5, "103.5"),
    )

    continuous = AdaptivePnfRunner((_pnf_config(),), AdaptiveBoxSizer(config))
    continuous_transitions: list[PnfTransition] = []
    for event in events:
        continuous_transitions.extend(continuous.process(event))

    staged = AdaptivePnfRunner((_pnf_config(),), AdaptiveBoxSizer(config))
    for event in events[:3]:
        staged.process(event)
    snapshot = staged.snapshot()
    resumed = AdaptivePnfRunner.from_snapshot(snapshot)
    resumed_transitions: list[PnfTransition] = []
    for event in events[3:]:
        resumed_transitions.extend(resumed.process(event))

    assert resumed.pnf_engine.state_for("XAUUSD") == continuous.pnf_engine.state_for("XAUUSD")
    assert resumed.decisions_for("XAUUSD") == continuous.decisions_for("XAUUSD")
    assert resumed_transitions == continuous_transitions[-len(resumed_transitions) :]


def test_resize_boundary_keeps_history_unchanged() -> None:
    config = AdaptiveBoxConfig(
        mode="atr",
        fixed_box_size=Decimal("1.0"),
        price_precision=1,
        rule_version="atr-v1",
        atr_period=2,
        atr_multiplier=Decimal("3.0"),
        min_box_size=Decimal("0.5"),
        max_box_size=Decimal("2.0"),
    )
    runner = AdaptivePnfRunner((_pnf_config(),), AdaptiveBoxSizer(config))
    seed = _event(1, "100.0")
    build = _event(2, "101.1")
    jump = _event(3, "105.9")
    runner.process(seed)
    transitions_before = list(runner.process(build))
    runner.process(jump)
    transitions_after = runner.pnf_engine.state_for("XAUUSD").transitions
    assert transitions_before[0] == transitions_after[0]
