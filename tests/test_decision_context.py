from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from nexora.artifacts import canonical_serialize, decode
from nexora.matrix import MatrixResolutionState, MatrixSnapshot
from nexora.signals import SignalDecision, SignalEngine, derive_decision_context
from nexora.signals.models import SignalEvidence

from tests.test_signals import _config, _matrix, _regime, _structure

NOW = datetime(2026, 2, 3, 9, 0, tzinfo=UTC)


def _resolutions(directions: tuple[str, ...]) -> MatrixSnapshot:
    """Build a matrix snapshot with explicit per-resolution directions, no engine."""
    names = ("fast", "medium", "slow")
    states = tuple(
        MatrixResolutionState(
            name=name,
            symbol="XAUUSD",
            direction=direction,  # type: ignore[arg-type]
            latest_transition=None,
            status="ready" if direction != "none" else "warmup",
        )
        for name, direction in zip(names, directions, strict=True)
    )
    known = [d for d in directions if d != "none"]
    if not known:
        alignment = "unavailable"
    elif all(d == "X" for d in known) and len(known) == len(directions):
        alignment = "aligned_bullish"
    elif all(d == "O" for d in known) and len(known) == len(directions):
        alignment = "aligned_bearish"
    elif len(known) == len(directions):
        alignment = "mixed"
    else:
        alignment = "unavailable"
    return MatrixSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        watermark_sequence=1,
        generated_at=NOW,
        alignment=alignment,  # type: ignore[arg-type]
        strength=0,
        resolutions=states,
    )


def _wait_decision(reason: str, *, has_positive: bool = False) -> SignalDecision:
    negative = (
        SignalEvidence(
            component="matrix", code="test_reason", points=0, polarity="neutral",
            reason=reason, source_refs=(),
        ),
    )
    positive = (
        (
            SignalEvidence(
                component="pnf", code="pnf_extension_bullish", points=20, polarity="bullish",
                reason="P&F extension remains bullish.", source_refs=(),
            ),
        )
        if has_positive
        else ()
    )
    return SignalDecision(
        action="WAIT",
        score=0,
        entry_zone=None,
        invalidation_price=None,
        invalidation_reason=None,
        targets=(),
        risk_reward=None,
        patterns=(),
        positive_evidence=positive,
        negative_evidence=negative,
        future_conditions=("Await confirmed market structure evidence.",),
        config_version="test-v1",
        engine_version="test-v1:decision-context",
        source_refs=(),
    )


# A. all required inputs unavailable => Bias UNAVAILABLE


def test_no_matrix_or_decision_yields_fully_unavailable_context() -> None:
    context = derive_decision_context(decision=None, matrix=None)
    assert context.bias == "UNAVAILABLE"
    assert context.state == "UNAVAILABLE"
    assert context.alignment.aligned == 0 and context.alignment.total == 0
    assert context.reasons == () and context.waiting_for == ()


def test_matrix_with_zero_known_directions_is_unavailable_not_mixed() -> None:
    matrix = _resolutions(("none", "none", "none"))
    context = derive_decision_context(decision=_wait_decision("No evidence yet."), matrix=matrix)
    assert context.bias == "UNAVAILABLE"
    assert context.state == "UNAVAILABLE"


# B. mixed matrix FAST X, MEDIUM X, SLOW O => Decision remains WAIT; explains disagreement;
#    NOT incorrectly UNAVAILABLE since two of three resolutions agree.


def test_two_bullish_one_bearish_is_bullish_lean_and_decision_stays_wait() -> None:
    structure = _structure(
        pivots=(
            ("low", "100.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "100.1", "confirmed"),
        ),
        levels=(("support", "99.8", "confirmed"), ("resistance", "104.2", "confirmed")),
    )
    matrix = _matrix("mixed", direction="X", price="103.9")
    snapshot = SignalEngine(_config()).evaluate(
        structure=structure, regime=_regime("trend"), matrix=matrix
    )
    assert snapshot.decision.action == "WAIT"

    # Independent of the engine call above: exercise the exact FAST/MEDIUM/SLOW
    # split from the task's own example directly against the pure function.
    lean_matrix = _resolutions(("X", "X", "O"))
    context = derive_decision_context(decision=snapshot.decision, matrix=lean_matrix)
    assert context.bias == "BULLISH_LEAN"
    assert context.state == "DEVELOPING"
    assert (context.alignment.aligned, context.alignment.total) == (2, 3)
    assert any("Matrix disagreement" in reason for reason in context.reasons)


# C. opposite mixed case FAST O, MEDIUM O, SLOW X


def test_two_bearish_one_bullish_is_bearish_lean() -> None:
    matrix = _resolutions(("O", "O", "X"))
    decision = _wait_decision("Matrix disagreement detected.")
    context = derive_decision_context(decision=decision, matrix=matrix)
    assert context.bias == "BEARISH_LEAN"
    assert context.state == "DEVELOPING"
    assert (context.alignment.aligned, context.alignment.total) == (2, 3)


# D. full bullish alignment


def test_full_bullish_alignment_with_buy_action_is_confirmed() -> None:
    structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "99.8", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "108.0", "confirmed")),
    )
    matrix = _matrix("aligned_bullish", direction="X", price="100.2")
    snapshot = SignalEngine(_config()).evaluate(
        structure=structure, regime=_regime("trend"), matrix=matrix
    )
    assert snapshot.decision.action == "BUY"

    context = derive_decision_context(decision=snapshot.decision, matrix=matrix)
    assert context.bias == "BULLISH"
    assert context.state == "CONFIRMED"
    assert (context.alignment.aligned, context.alignment.total) == (3, 3)
    assert context.reasons == () and context.waiting_for == ()


# E. full bearish alignment


def test_full_bearish_alignment_with_sell_action_is_confirmed() -> None:
    structure = _structure(
        pivots=(
            ("high", "111.0", "confirmed"),
            ("low", "104.5", "confirmed"),
            ("high", "110.8", "confirmed"),
        ),
        levels=(("support", "100.0", "confirmed"), ("resistance", "111.5", "confirmed")),
    )
    matrix = _matrix("aligned_bearish", direction="O", price="110.8")
    snapshot = SignalEngine(_config()).evaluate(
        structure=structure, regime=_regime("trend"), matrix=matrix
    )
    assert snapshot.decision.action == "SELL"

    context = derive_decision_context(decision=snapshot.decision, matrix=matrix)
    assert context.bias == "BEARISH"
    assert context.state == "CONFIRMED"
    assert (context.alignment.aligned, context.alignment.total) == (3, 3)


# F. genuinely conflicting evidence => MIXED, not UNAVAILABLE, not a fabricated lean


def test_tied_known_directions_are_mixed_not_unavailable() -> None:
    matrix = _resolutions(("X", "O", "none"))
    decision = _wait_decision("Conflicting evidence.")
    context = derive_decision_context(decision=decision, matrix=matrix)
    assert context.bias == "MIXED"
    assert context.state == "UNAVAILABLE"
    assert (context.alignment.aligned, context.alignment.total) == (1, 3)


# G. cooldown WAIT


def test_cooldown_wait_surfaces_cooldown_reason() -> None:
    config = replace(_config(), cooldown_events=5)
    engine = SignalEngine(config)
    structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "99.8", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "108.0", "confirmed")),
    )
    matrix = _matrix("aligned_bullish", direction="X", price="99.8")
    first = engine.evaluate(structure=structure, regime=_regime("trend"), matrix=matrix)
    assert first.decision.action == "BUY"
    second = engine.evaluate(structure=structure, regime=_regime("trend"), matrix=matrix)
    assert second.decision.action == "WAIT"

    context = derive_decision_context(decision=second.decision, matrix=matrix)
    assert context.reasons == ("Signal cooldown window is active.",)
    assert context.waiting_for == second.decision.future_conditions


# H. insufficient-input WAIT (structure pivots missing) while matrix itself is fully aligned


def test_insufficient_inputs_wait_still_reports_matrix_bias_honestly() -> None:
    engine = SignalEngine(_config())
    structure = _structure(pivots=())
    matrix = _matrix("aligned_bullish", direction="X", price="100.2")
    snapshot = engine.evaluate(structure=structure, regime=_regime("trend"), matrix=matrix)
    assert snapshot.decision.action == "WAIT"

    context = derive_decision_context(decision=snapshot.decision, matrix=matrix)
    assert context.bias == "BULLISH"
    assert context.state == "DEVELOPING"
    assert any("unavailable" in reason.lower() for reason in context.reasons)


# I. range/high-volatility WAIT


def test_range_regime_wait_surfaces_breakout_confirmation_reason() -> None:
    engine = SignalEngine(_config())
    structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "99.8", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "108.0", "confirmed")),
    )
    matrix = _matrix("aligned_bullish", direction="X", price="99.8")
    snapshot = engine.evaluate(structure=structure, regime=_regime("range"), matrix=matrix)
    assert snapshot.decision.action == "WAIT"

    context = derive_decision_context(decision=snapshot.decision, matrix=matrix)
    assert any("breakout confirmation" in reason.lower() for reason in context.reasons)
    assert context.bias == "BULLISH"


# J. replay/restart determinism


def test_decision_context_is_deterministic_across_serialize_decode_round_trip() -> None:
    structure = _structure(
        pivots=(
            ("low", "100.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "100.1", "confirmed"),
        ),
        levels=(("support", "99.8", "confirmed"), ("resistance", "104.2", "confirmed")),
    )
    matrix = _matrix("mixed", direction="X", price="103.9")
    snapshot = SignalEngine(_config()).evaluate(
        structure=structure, regime=_regime("range"), matrix=matrix
    )

    first = derive_decision_context(decision=snapshot.decision, matrix=matrix)

    decision_roundtrip = decode(SignalDecision, canonical_serialize(snapshot.decision))
    matrix_roundtrip = decode(MatrixSnapshot, canonical_serialize(matrix))
    second = derive_decision_context(decision=decision_roundtrip, matrix=matrix_roundtrip)

    assert first == second
    third = derive_decision_context(decision=decision_roundtrip, matrix=matrix_roundtrip)
    assert second == third


def test_decision_context_serializes_and_is_additive_only() -> None:
    matrix = _resolutions(("X", "X", "O"))
    decision = _wait_decision("Matrix disagreement detected.")
    context = derive_decision_context(decision=decision, matrix=matrix)
    payload = canonical_serialize(context)
    assert payload["bias"] == "BULLISH_LEAN"
    assert payload["alignment"] == {"aligned": 2, "total": 3}
    assert set(payload) == {
        "schema_version", "bias", "state", "alignment", "reasons", "waiting_for",
    }


__all__ = ["_resolutions", "_wait_decision"]
