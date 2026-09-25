"""Outcome evaluation and MFE/MAE primitives (ADR-026 Decisions 6-7). Pure, no I/O.

The window is ``W_k`` = canonical samples with ``event_time`` in ``[B_k, E_k)``. The
reference is the frozen ``P_ref``; θ is only the value frozen in the prediction. No
outcome label or θ policy is chosen here (Q-M3): threshold-dependent fields stay null.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from nexora.m30_bias.candles import Sample, build_candle
from nexora.m30_bias.models import (
    POLICY_VERSION,
    FirstTouch,
    M30BiasOutcome,
    M30BiasOutcomeStatus,
    M30BiasPrediction,
    ThresholdLabel,
    outcome_id,
)

_ZERO = Decimal(0)


def excursions(
    reference: Decimal, samples: Sequence[Sample]
) -> tuple[Decimal, Decimal, Sample | None, Sample | None]:
    """``up = max(0, max(p - P_ref))``, ``down = max(0, max(P_ref - p))``.

    Also returns the first sample attaining each strictly positive extreme (else None).
    """
    up, down = _ZERO, _ZERO
    up_at: Sample | None = None
    down_at: Sample | None = None
    for sample in samples:
        rise, fall = sample.price - reference, reference - sample.price
        if rise > up:
            up, up_at = rise, sample
        if fall > down:
            down, down_at = fall, sample
    return up, down, up_at, down_at


def threshold_label(close_return: Decimal, threshold: Decimal) -> ThresholdLabel:
    """O3 candidate primitive; only evaluated with an explicitly frozen θ > 0."""
    if close_return >= threshold:
        return "UP"
    if close_return <= -threshold:
        return "DOWN"
    return "FLAT"


def first_touch(reference: Decimal, threshold: Decimal, samples: Sequence[Sample]) -> FirstTouch:
    """O4 candidate primitive over point samples.

    A point sample can satisfy at most one side when θ > 0, so ``AMBIGUOUS`` cannot arise
    from point samples; it is reserved for future interval (bar) samples.
    """
    upper, lower = reference + threshold, reference - threshold
    for sample in samples:
        if sample.price >= upper:
            return "UP"
        if sample.price <= lower:
            return "DOWN"
    return "NONE"


def evaluate_outcome(
    prediction: M30BiasPrediction, window: Sequence[Sample], closing: Sample
) -> M30BiasOutcome:
    """Evaluate a frozen prediction once its bucket is closed by ``closing`` (N7)."""
    if closing.event_time < prediction.target_end:
        raise ValueError("bucket_not_closed")
    for sample in window:
        if not prediction.target_start <= sample.event_time < prediction.target_end:
            raise ValueError("sample_outside_window")
    reasons: list[str] = []
    reference = prediction.reference_price
    theta = prediction.threshold
    status: M30BiasOutcomeStatus = "EVALUATED" if window else "NO_DATA"
    if not window:
        reasons.append("no_in_window_samples")
    if reference is None:
        reasons.append("reference_price_unavailable")
    if theta is None:
        # Mirror why θ is null: no Quant policy (Q-M3) vs a policy that produced no value.
        undecided = "threshold_policy_undecided" in prediction.reason_codes
        reasons.append("threshold_policy_undecided" if undecided else "threshold_unavailable")
    if any(s.completeness_unknown for s in window):
        reasons.append("completeness_unknown")

    candle = build_candle(tuple(window)) if window else None
    close_return = body = drift = None
    up = down = mfe = mae = None
    mfe_at: Sample | None = None
    mae_at: Sample | None = None
    label: ThresholdLabel | None = None
    touch: FirstTouch | None = None
    if candle is not None:
        body = candle.close - candle.open
        if reference is not None:
            close_return = candle.close - reference
            drift = candle.open - reference
            up, down, up_at, down_at = excursions(reference, window)
            directional = prediction.status == "FROZEN" and prediction.bias in ("UP", "DOWN")
            if directional and prediction.bias == "UP":
                mfe, mae, mfe_at, mae_at = up, down, up_at, down_at
            elif directional:
                mfe, mae, mfe_at, mae_at = down, up, down_at, up_at
            if theta is not None:
                label = threshold_label(close_return, theta)
                touch = first_touch(reference, theta, window)

    return M30BiasOutcome(
        schema_version=1,
        policy_version=POLICY_VERSION,
        outcome_id=outcome_id(prediction.algorithm_key, prediction.candle_id),
        prediction_id=prediction.prediction_id,
        candle_id=prediction.candle_id,
        algorithm_key=prediction.algorithm_key,
        status=status,
        evaluation_time=closing.received_at,
        closing_event_identity=closing.identity,
        open=candle.open if candle else None,
        high=candle.high if candle else None,
        low=candle.low if candle else None,
        close=candle.close if candle else None,
        sample_count=candle.sample_count if candle else 0,
        first_sample_time=candle.first_sample_time if candle else None,
        last_sample_time=candle.last_sample_time if candle else None,
        max_sample_gap_seconds=candle.max_sample_gap_seconds if candle else None,
        gap_or_incomplete_samples=candle.gap_or_incomplete_samples if candle else 0,
        close_return=close_return,
        body=body,
        pre_target_drift=drift,
        label_threshold=label,
        first_touch=touch,
        mfe=mfe,
        mae=mae,
        mfe_time=mfe_at.event_time if mfe_at else None,
        mae_time=mae_at.event_time if mae_at else None,
        up_excursion=up,
        down_excursion=down,
        reason_codes=tuple(reasons),
    )
