from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from nexora.pnf import (
    PnfConfig,
    PnfEngine,
    PnfInputError,
    PnfTransition,
    exact_threshold_fixture,
    flat_fixture,
    monotonic_rise_fall_fixture,
    multi_box_gap_fixture,
    reversal_boundary_fixture,
)


def _config(*, symbol: str = "XAUUSD") -> PnfConfig:
    return PnfConfig(
        symbol=symbol,
        box_size=Decimal("1.0"),
        reversal_boxes=2,
        price_precision=1,
        price_source="ask",
        version="p3-fixed-v1",
    )


def test_flat_prices_below_threshold_do_not_create_transition() -> None:
    engine = PnfEngine(configs=(_config(),))
    transitions: list[PnfTransition] = []
    for event in flat_fixture():
        transitions.extend(engine.process(event))
    assert transitions == []
    state = engine.state_for("XAUUSD")
    assert state.seed_price == Decimal("100.0")
    assert state.columns == ()


def test_monotonic_rise_then_fall_creates_seed_extensions_and_reversal() -> None:
    engine = PnfEngine(configs=(_config(),))
    transitions: list[PnfTransition] = []
    for event in monotonic_rise_fall_fixture():
        transitions.extend(engine.process(event))
    assert [transition.type for transition in transitions] == [
        "seed",
        "extension",
        "extension",
        "reversal",
    ]
    assert [transition.boxes_moved for transition in transitions] == [1, 1, 1, 2]
    assert transitions[-1].direction == "O"


def test_exact_threshold_is_inclusive_for_seed_and_extension() -> None:
    engine = PnfEngine(configs=(_config(),))
    transitions: list[PnfTransition] = []
    for event in exact_threshold_fixture():
        transitions.extend(engine.process(event))
    assert [transition.type for transition in transitions] == ["seed", "extension"]
    assert [str(transition.to_price) for transition in transitions] == ["101.0", "102.0"]


def test_reversal_boundary_is_inclusive() -> None:
    engine = PnfEngine(configs=(_config(),))
    transitions: list[PnfTransition] = []
    for event in reversal_boundary_fixture():
        transitions.extend(engine.process(event))
    assert [transition.type for transition in transitions] == ["seed", "reversal"]
    assert transitions[-1].boxes_moved == 2
    assert transitions[-1].direction == "O"


def test_multi_box_gap_processed_as_single_transition_with_box_count() -> None:
    engine = PnfEngine(configs=(_config(),))
    transitions: list[PnfTransition] = []
    for event in multi_box_gap_fixture():
        transitions.extend(engine.process(event))
    assert [transition.type for transition in transitions] == ["seed", "reversal"]
    assert transitions[0].boxes_moved == 4
    assert transitions[1].boxes_moved == 7


def test_duplicate_ignored_and_out_of_order_rejected() -> None:
    engine = PnfEngine(configs=(_config(),))
    first, second = exact_threshold_fixture()[:2]
    seed_transitions = engine.process(first)
    assert seed_transitions == ()
    seeded = engine.process(second)
    assert len(seeded) == 1

    duplicate = replace(second, is_duplicate=True)
    assert engine.process(duplicate) == ()

    out_of_order = replace(second, identity_key="xau:old", source_sequence=0, is_out_of_order=True)
    with pytest.raises(PnfInputError) as exc:
        engine.process(out_of_order)
    assert exc.value.code == "out_of_order_event"


def test_snapshot_restart_parity_matches_continuous_replay() -> None:
    events = monotonic_rise_fall_fixture()
    continuous = PnfEngine(configs=(_config(),))
    continuous_transitions: list[PnfTransition] = []
    for event in events:
        continuous_transitions.extend(continuous.process(event))

    staged = PnfEngine(configs=(_config(),))
    first_half = events[:3]
    second_half = events[3:]
    for event in first_half:
        staged.process(event)
    snapshot = staged.snapshot()
    resumed = PnfEngine.from_snapshot(snapshot)
    resumed_transitions: list[PnfTransition] = []
    for event in second_half:
        resumed_transitions.extend(resumed.process(event))

    assert resumed.state_for("XAUUSD") == continuous.state_for("XAUUSD")
    expected_tail = continuous_transitions[
        len(continuous_transitions) - len(resumed_transitions):
    ]
    assert resumed_transitions == expected_tail


def test_symbol_states_are_isolated_and_deterministic() -> None:
    xau_config = _config(symbol="XAUUSD")
    eur_config = _config(symbol="EURUSD")
    engine = PnfEngine(configs=(xau_config, eur_config))

    xau_events = exact_threshold_fixture()
    eur_events = tuple(
        replace(
            event,
            symbol="EURUSD",
            identity_key=f"EUR:{index}",
            source_event_id=f"evt-EURUSD-{index}",
        )
        for index, event in enumerate(xau_events, 1)
    )

    for index in range(len(xau_events)):
        engine.process(xau_events[index])
        engine.process(eur_events[index])

    xau_state = engine.state_for("XAUUSD")
    eur_state = engine.state_for("EURUSD")
    assert len(xau_state.columns) == 1
    assert len(eur_state.columns) == 1
    assert xau_state.transitions != eur_state.transitions


@pytest.mark.parametrize(
    ("box_size", "reversal_boxes", "price_precision", "expected"),
    [
        (Decimal("0"), 2, 1, "invalid_box_size"),
        (Decimal("1"), 0, 1, "invalid_reversal_boxes"),
        (Decimal("1"), 2, 11, "invalid_precision"),
    ],
)
def test_invalid_config_is_rejected(
    box_size: Decimal, reversal_boxes: int, price_precision: int, expected: str
) -> None:
    with pytest.raises(PnfInputError) as exc:
        PnfConfig(
            symbol="XAUUSD",
            box_size=box_size,
            reversal_boxes=reversal_boxes,
            price_precision=price_precision,
            price_source="ask",
            version="p3-fixed-v1",
        )
    assert exc.value.code == expected
