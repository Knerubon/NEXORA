from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from nexora.market_regime import RegimeSnapshot, RegimeState
from nexora.matrix import MatrixAlignment, MatrixResolutionState, MatrixSnapshot
from nexora.pnf import PnfTransition
from nexora.signals import SignalConfig, SignalEngine, SignalWeights
from nexora.structure import CandidateLevel, ConfirmedPivot, StructureSnapshot

from tests.signal_pattern_golden import golden_cases

NOW = datetime(2026, 2, 3, 9, 0, tzinfo=UTC)


def _config() -> SignalConfig:
    return SignalConfig(
        symbol="XAUUSD",
        cooldown_events=0,
        expiry_events=12,
        version="p8a-test-v1",
        weights=SignalWeights(
            pnf_reversal=20,
            structure=25,
            support_resistance=20,
            matrix=20,
            regime=15,
            pattern_confirmation=10,
            pattern_conflict_penalty=15,
        ),
        wait_score_max=49,
        action_score_min=65,
        action_gap_min=8,
        pattern_price_tolerance=Decimal("0.8"),
        entry_zone_half_width=Decimal("0.5"),
        target_rr_tp1=Decimal("1.5"),
        target_rr_tp2=Decimal("2.5"),
    )


def _transition(
    *, direction: Literal["X", "O"], kind: Literal["seed", "extension", "reversal"], price: str
) -> PnfTransition:
    event_time = NOW + timedelta(minutes=2)
    return PnfTransition(
        type=kind,
        reason="reversed" if kind == "reversal" else "extended",
        symbol="XAUUSD",
        column_id=1,
        direction=direction,
        from_price=Decimal(price) - Decimal("0.5"),
        to_price=Decimal(price),
        boxes_moved=1,
        event_time=event_time,
        source_event_id=f"evt-{direction}-{price}",
        identity_key=f"transition-{direction}-{price}",
        config_version="p8a-test-v1",
        effective_box_size=Decimal("1.0"),
        sizing_rule_version="p8a-box-v1",
    )


def _matrix(
    alignment: MatrixAlignment, *, direction: Literal["X", "O"] = "X", price: str = "100.2"
) -> MatrixSnapshot:
    transition = _transition(direction=direction, kind="reversal", price=price)
    return MatrixSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        watermark_sequence=1,
        generated_at=NOW + timedelta(hours=1),
        alignment=alignment,
        strength=3,
        resolutions=(
            MatrixResolutionState(
                name="fast",
                symbol="XAUUSD",
                direction=direction,
                latest_transition=transition,
                status="ready",
            ),
            MatrixResolutionState(
                name="medium",
                symbol="XAUUSD",
                direction=direction,
                latest_transition=transition,
                status="ready",
            ),
            MatrixResolutionState(
                name="slow",
                symbol="XAUUSD",
                direction=direction,
                latest_transition=transition,
                status="ready",
            ),
        ),
    )


def _regime(label: Literal["trend", "range", "high_volatility", "unknown"]) -> RegimeSnapshot:
    return RegimeSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        state=RegimeState(
            label=label,
            reason="unit_test",
            effective_time=NOW,
            source_ref="regime:test",
            config_version="p7-regime-v1",
        ),
    )


def _structure(
    *,
    pivots: tuple[tuple[Literal["high", "low"], str, str], ...],
    levels: tuple[
        tuple[
            Literal["support", "resistance"],
            str,
            Literal["candidate", "confirmed", "invalidated", "unavailable"],
        ],
        ...,
    ] = (("support", "99.0", "confirmed"),),
) -> StructureSnapshot:
    pivot_items = tuple(
        ConfirmedPivot(
            kind=kind,
            price=Decimal(price),
            occurrence_time=NOW + timedelta(minutes=idx * 2),
            confirmation_time=NOW + timedelta(minutes=idx * 2 + 1),
            source_transition_id=f"pivot-{idx}",
            config_version="p3-fixed-v1",
        )
        for idx, (kind, price, _status) in enumerate(pivots, 1)
    )
    level_items = tuple(
        CandidateLevel(
            side=side,
            price=Decimal(price),
            status=status,
            source_pivot_id=f"level-{idx}",
            updated_at=NOW + timedelta(minutes=idx),
        )
        for idx, (side, price, status) in enumerate(levels, 1)
    )
    return StructureSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=len(pivot_items),
        pivots=pivot_items,
        levels=level_items,
    )


def test_signal_pattern_golden_cases_match_expected_actions() -> None:
    for case in golden_cases(NOW):
        structure = _structure(
            pivots=case.pivot_shapes,
            levels=case.levels,
        )
        matrix = _matrix(
            case.matrix_alignment,
            direction="X" if case.matrix_direction == "X" else "O",
            price=case.matrix_price,
        )
        regime = _regime(case.regime)

        snapshot = SignalEngine(_config()).evaluate(
            structure=structure,
            regime=regime,
            matrix=matrix,
        )
        assert snapshot.decision.action == case.expected_action


def test_buy_signal_has_pattern_evidence_entry_zone_and_targets() -> None:
    config = _config()
    engine = SignalEngine(config)
    structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "99.8", "confirmed"),
        ),
        levels=(
            ("support", "99.0", "confirmed"),
            ("resistance", "108.0", "confirmed"),
        ),
    )
    snapshot = engine.evaluate(
        structure=structure,
        regime=_regime("trend"),
        matrix=_matrix("aligned_bullish", direction="X", price="100.2"),
    )

    assert snapshot.decision.action == "BUY"
    assert snapshot.decision.score >= 65
    assert snapshot.decision.entry_zone is not None
    assert snapshot.decision.entry_zone.low < snapshot.decision.entry_zone.high
    assert snapshot.decision.invalidation_price is not None
    assert len(snapshot.decision.targets) == 2
    assert {target.name for target in snapshot.decision.targets} == {"TP1", "TP2"}
    assert snapshot.decision.risk_reward is not None and snapshot.decision.risk_reward > 0
    assert any(
        pattern.pattern_type == "double_bottom" and pattern.direction == "bullish"
        for pattern in snapshot.decision.patterns
    )


def test_sell_signal_is_released_when_matrix_and_structure_align_bearish() -> None:
    engine = SignalEngine(_config())
    structure = _structure(
        pivots=(
            ("high", "111.0", "confirmed"),
            ("low", "104.5", "confirmed"),
            ("high", "110.8", "confirmed"),
        ),
        levels=(
            ("support", "100.0", "confirmed"),
            ("resistance", "111.5", "confirmed"),
        ),
    )
    snapshot = engine.evaluate(
        structure=structure,
        regime=_regime("trend"),
        matrix=_matrix("aligned_bearish", direction="O", price="110.8"),
    )

    assert snapshot.decision.action == "SELL"
    assert snapshot.decision.score >= 65
    assert snapshot.decision.invalidation_price == Decimal("111.5")
    assert snapshot.decision.risk_reward is not None and snapshot.decision.risk_reward > 0


def test_wait_signal_occurs_on_matrix_disagreement_and_conflicting_pressure() -> None:
    engine = SignalEngine(_config())
    structure = _structure(
        pivots=(
            ("low", "100.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "100.1", "confirmed"),
        ),
        levels=(
            ("support", "99.8", "confirmed"),
            ("resistance", "104.2", "confirmed"),
        ),
    )
    snapshot = engine.evaluate(
        structure=structure,
        regime=_regime("range"),
        matrix=_matrix("mixed", direction="X", price="103.9"),
    )

    assert snapshot.decision.action == "WAIT"
    assert snapshot.decision.score <= 49
    assert any(
        "Matrix disagreement detected." in evidence.reason
        for evidence in snapshot.decision.negative_evidence
    )


def test_pattern_conflict_detects_bearish_reversal_near_resistance() -> None:
    engine = SignalEngine(_config())
    structure = _structure(
        pivots=(
            ("high", "102.0", "confirmed"),
            ("low", "100.0", "confirmed"),
            ("high", "101.8", "confirmed"),
        ),
        levels=(
            ("support", "99.0", "confirmed"),
            ("resistance", "101.5", "confirmed"),
        ),
    )
    assessment = engine._assess_components(
        structure=structure,
        regime=_regime("trend"),
        matrix=_matrix("aligned_bullish", direction="X", price="101.2"),
    )

    assert any(pattern.relation == "conflict" for pattern in assessment.patterns)
    assert any(
        "Bearish reversal pattern detected near resistance context." in evidence.reason
        for evidence in assessment.negative_evidence
    )


def test_support_and_resistance_context_supports_bullish_and_bearish_tension() -> None:
    transition = _transition(direction="X", kind="reversal", price="99.8")
    structure = _structure(
        pivots=(
            ("low", "98.8", "confirmed"),
            ("high", "101.0", "confirmed"),
            ("low", "99.4", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "102.0", "confirmed")),
    )
    matrix = _matrix("aligned_bullish", direction="X", price="99.8")
    matrix = replace(
        matrix,
        resolutions=(
            MatrixResolutionState(
                name="fast",
                symbol="XAUUSD",
                direction="X",
                latest_transition=transition,
                status="ready",
            ),
        ),
    )

    signal = SignalEngine(_config())
    buy = signal._support_resistance_evidence(structure=structure, matrix=matrix)
    assert buy[0] == "BUY"
    assert "support" in buy[1].lower()

    sell_structure = _structure(
        pivots=(
            ("high", "103.5", "confirmed"),
            ("low", "100.2", "confirmed"),
            ("high", "102.9", "confirmed"),
        ),
        levels=(("support", "100.0", "confirmed"), ("resistance", "102.0", "confirmed")),
    )
    sell_transition = _transition(direction="O", kind="reversal", price="102.4")
    sell_matrix = replace(
        matrix,
        alignment="aligned_bearish",
        resolutions=(
            MatrixResolutionState(
                name="fast",
                symbol="XAUUSD",
                direction="O",
                latest_transition=sell_transition,
                status="ready",
            ),
        ),
    )
    sell = signal._support_resistance_evidence(structure=sell_structure, matrix=sell_matrix)
    assert sell[0] == "SELL"
    assert "resistance" in sell[1].lower()


def test_matrix_disagreement_returns_wait_and_explicit_reason() -> None:
    engine = SignalEngine(_config())
    mixed = engine._matrix_evidence(_matrix("mixed", direction="X", price="103.9"))
    assert mixed[0] == "WAIT"
    assert mixed[1] == "Matrix disagreement detected."
    assert mixed[2] == "matrix_mixed"


def test_signal_score_boundaries_and_entry_invalidation_are_deterministic() -> None:
    engine = SignalEngine(_config())
    assert engine._resolve_action_and_score(0, 0) == ("WAIT", 0)
    assert engine._resolve_action_and_score(100, 0) == ("BUY", 100)

    structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "100.2", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "108.0", "confirmed")),
    )
    setup = engine._build_trade_setup(
        action="BUY",
        structure=structure,
        matrix=_matrix("aligned_bullish", direction="X", price="99.8"),
    )
    assert setup is not None
    entry_zone, invalidation, reason, targets, rr = setup
    assert entry_zone.low == Decimal("99.30")
    assert entry_zone.high == Decimal("100.30")
    assert invalidation == Decimal("99.0")
    assert reason == "Derived from nearest confirmed structure level."
    assert {target.name for target in targets} == {"TP1", "TP2"}
    assert rr > 0


def test_signal_snapshot_replay_is_deterministic() -> None:
    from nexora.signals.repository import SignalSnapshotStore

    engine = SignalEngine(_config())
    structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "99.8", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "108.0", "confirmed")),
    )
    snapshot = engine.evaluate(
        structure=structure,
        regime=_regime("trend"),
        matrix=_matrix("aligned_bullish", direction="X", price="99.8"),
    )

    store = SignalSnapshotStore()
    store.append(snapshot)
    assert store.replay() == store.rebuild()
    assert snapshot.decision.action == "BUY"


def test_same_dataset_and_config_replay_produces_immutable_signal_history() -> None:
    base_structure = _structure(
        pivots=(
            ("low", "99.0", "confirmed"),
            ("high", "104.0", "confirmed"),
            ("low", "99.8", "confirmed"),
        ),
        levels=(("support", "99.0", "confirmed"), ("resistance", "108.0", "confirmed")),
    )
    engine = SignalEngine(_config())
    first = engine.evaluate(
        structure=base_structure,
        regime=_regime("trend"),
        matrix=_matrix("aligned_bullish", direction="X", price="99.8"),
    )
    second = SignalEngine(_config()).evaluate(
        structure=base_structure,
        regime=_regime("trend"),
        matrix=_matrix("aligned_bullish", direction="X", price="99.8"),
    )

    assert first.decision == second.decision
    assert first.history == second.history


__all__ = ["_config", "_matrix", "_regime", "_structure"]
