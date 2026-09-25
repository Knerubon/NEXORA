"""ADR-026 Phase 2A: outcome evaluation, MFE/MAE hand-calculated examples."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest
from nexora.m30_bias import M30BiasPrediction, Sample, evaluate_outcome
from nexora.m30_bias.models import M30BiasValue

from tests.test_m30_bias_core import (
    ExplicitThreshold,
    at,
    ev,
    outcomes,
    predictions,
    run,
    scenario,
)

# Scenario target 10:30: P_ref = 100.20 (e3); W_k = 100.50, 99.20, 101.30, 100.80.
P_REF = Decimal("100.20")


def _prediction(bias: M30BiasValue = "UP", status: str = "FROZEN") -> M30BiasPrediction:
    base = predictions(run(scenario()))[0]
    return dataclasses.replace(base, bias=bias, status=status)  # type: ignore[arg-type]


def _window() -> list[Sample]:
    prices = [(30, "100.50"), (40, "99.20"), (50, "101.30"), (59, "100.80")]
    return [Sample.from_event(ev(at(m), p, f"w{m}"), "complete") for m, p in prices]


CLOSING = Sample.from_event(ev(at(60), "100.90", "close"), "complete")


def test_mfe_mae_hand_calculated_for_up_bias() -> None:
    outcome = evaluate_outcome(_prediction("UP"), _window(), CLOSING)
    # up = max(100.50,99.20,101.30,100.80) - 100.20 = 1.10; down = 100.20 - 99.20 = 1.00
    assert outcome.up_excursion == Decimal("1.10")
    assert outcome.down_excursion == Decimal("1.00")
    assert outcome.mfe == Decimal("1.10") and outcome.mfe_time == at(50)
    assert outcome.mae == Decimal("1.00") and outcome.mae_time == at(40)
    assert outcome.close_return == Decimal("0.60")  # 100.80 - 100.20
    assert outcome.body == Decimal("0.30")  # 100.80 - 100.50
    assert outcome.pre_target_drift == Decimal("0.30")  # 100.50 - 100.20
    assert (outcome.open, outcome.high, outcome.low, outcome.close) == (
        Decimal("100.50"),
        Decimal("101.30"),
        Decimal("99.20"),
        Decimal("100.80"),
    )
    assert outcome.evaluation_time == CLOSING.received_at
    assert outcome.closing_event_identity == "close"
    assert outcome.status == "EVALUATED" and outcome.sample_count == 4


def test_mfe_mae_hand_calculated_for_down_bias() -> None:
    outcome = evaluate_outcome(_prediction("DOWN"), _window(), CLOSING)
    assert outcome.mfe == Decimal("1.00") and outcome.mfe_time == at(40)
    assert outcome.mae == Decimal("1.10") and outcome.mae_time == at(50)


@pytest.mark.parametrize(
    ("bias", "status"),
    [
        ("NO_EDGE", "FROZEN"),
        ("UNAVAILABLE", "FROZEN"),
        ("UP", "SKIPPED"),
        ("UNAVAILABLE", "SKIPPED"),
    ],
)
def test_non_directional_records_have_null_mfe_mae(bias: M30BiasValue, status: str) -> None:
    outcome = evaluate_outcome(_prediction(bias, status), _window(), CLOSING)
    assert outcome.mfe is None and outcome.mae is None
    assert outcome.mfe_time is None and outcome.mae_time is None
    assert outcome.up_excursion == Decimal("1.10")
    assert outcome.down_excursion == Decimal("1.00")


def test_no_positive_excursion_has_zero_and_no_time() -> None:
    flat = [Sample.from_event(ev(at(31), "100.20", "f"), "complete")]
    outcome = evaluate_outcome(_prediction("UP"), flat, CLOSING)
    assert outcome.mfe == 0 and outcome.mae == 0
    assert outcome.mfe_time is None and outcome.mae_time is None


def test_threshold_dependent_fields_are_null_when_quant_policy_undecided() -> None:
    outcome = evaluate_outcome(_prediction("UP"), _window(), CLOSING)
    assert outcome.label_threshold is None and outcome.first_touch is None
    assert "threshold_policy_undecided" in outcome.reason_codes


@pytest.mark.parametrize(
    ("theta", "label", "touch"),
    [
        ("1.00", "FLAT", "DOWN"),  # 99.20 <= 99.20 before 101.30 >= 101.20
        ("0.60", "UP", "DOWN"),  # 100.80-100.20 = 0.60 >= θ; 99.20 <= 99.60 first
        ("0.25", "UP", "UP"),  # 100.50 >= 100.45 first
        ("1.50", "FLAT", "NONE"),
    ],
)
def test_explicit_threshold_primitives(theta: str, label: str, touch: str) -> None:
    prediction = dataclasses.replace(_prediction("UP"), threshold=Decimal(theta))
    outcome = evaluate_outcome(prediction, _window(), CLOSING)
    assert outcome.label_threshold == label and outcome.first_touch == touch


def test_empty_window_is_no_data() -> None:
    outcome = evaluate_outcome(_prediction("UP"), [], CLOSING)
    assert outcome.status == "NO_DATA" and outcome.sample_count == 0
    assert "no_in_window_samples" in outcome.reason_codes
    assert outcome.up_excursion is None and outcome.mfe is None and outcome.close is None


def test_unknown_completeness_is_reported_not_assumed_complete() -> None:
    window = [Sample.from_event(ev(at(31), "100.40", "u"), "unknown")]
    outcome = evaluate_outcome(_prediction("UP"), window, CLOSING)
    assert "completeness_unknown" in outcome.reason_codes


def test_outcome_requires_closed_bucket_and_in_window_samples() -> None:
    early = Sample.from_event(ev(at(59), "100", "early"), "complete")
    with pytest.raises(ValueError, match="bucket_not_closed"):
        evaluate_outcome(_prediction(), _window(), early)
    outside = [Sample.from_event(ev(at(29), "100", "o"), "complete")]
    with pytest.raises(ValueError, match="sample_outside_window"):
        evaluate_outcome(_prediction(), outside, CLOSING)


def test_core_outcome_matches_hand_calculation_and_uses_frozen_theta() -> None:
    emitted = run(scenario(), threshold=ExplicitThreshold(Decimal("0.60")))
    outcome = outcomes(emitted)[0]
    first = predictions(emitted)[0]
    assert outcome.prediction_id == first.prediction_id and outcome.candle_id == first.candle_id
    assert outcome.mfe == Decimal("1.10") and outcome.mae == Decimal("1.00")
    assert outcome.label_threshold == "UP" and outcome.first_touch == "DOWN"
    assert outcome.evaluation_time == at(60) + timedelta(milliseconds=100)
