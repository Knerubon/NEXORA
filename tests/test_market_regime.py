from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nexora.market_regime import MarketRegimeEngine, RegimeConfig
from nexora.matrix import MatrixAlignment, MatrixResolutionState, MatrixSnapshot
from nexora.structure import CandidateLevel, ConfirmedPivot, StructureSnapshot


def _matrix(alignment: MatrixAlignment, generated_at: datetime) -> MatrixSnapshot:
    return MatrixSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        watermark_sequence=1,
        generated_at=generated_at,
        alignment=alignment,
        strength=3,
        resolutions=(
            MatrixResolutionState(
                name="fast",
                symbol="XAUUSD",
                direction="X",
                latest_transition=None,
                status="ready",
            ),
        ),
    )


def _structure(prices: list[str]) -> StructureSnapshot:
    pivots = tuple(
        ConfirmedPivot(
            kind="high" if index % 2 == 0 else "low",
            price=Decimal(price),
            occurrence_time=datetime(2026, 2, 3, 9, index, tzinfo=UTC),
            confirmation_time=datetime(2026, 2, 3, 9, index + 1, tzinfo=UTC),
            source_transition_id=f"tr-{index}",
            config_version="p3-fixed-v1",
        )
        for index, price in enumerate(prices, 1)
    )
    return StructureSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=len(prices),
        pivots=pivots,
        levels=(
            CandidateLevel(
                side="support",
                price=Decimal("100.0"),
                status="confirmed",
                source_pivot_id="tr-1",
                updated_at=datetime(2026, 2, 3, 9, 0, tzinfo=UTC),
            ),
        ),
    )


def _config() -> RegimeConfig:
    return RegimeConfig(
        symbol="XAUUSD",
        lookback=4,
        trend_min_slope=Decimal("3.0"),
        high_volatility_min_width=Decimal("8.0"),
        hysteresis=Decimal("1.0"),
        version="p7-regime-v1",
    )


def test_regime_unknown_for_unavailable_or_missing_structure() -> None:
    engine = MarketRegimeEngine(config=_config())
    snapshot = engine.classify(
        structure=_structure([]),
        matrix=_matrix("unavailable", datetime(2026, 2, 3, 10, 0, tzinfo=UTC)),
    )
    assert snapshot.state.label == "unknown"
    assert snapshot.state.reason == "insufficient_inputs"


def test_regime_classifies_trend_range_and_high_volatility() -> None:
    trend_engine = MarketRegimeEngine(config=_config())
    trend = trend_engine.classify(
        structure=_structure(["100", "101", "103", "106"]),
        matrix=_matrix("aligned_bullish", datetime(2026, 2, 3, 10, 1, tzinfo=UTC)),
    )
    assert trend.state.label == "trend"

    range_engine = MarketRegimeEngine(config=_config())
    ranging = range_engine.classify(
        structure=_structure(["100", "101", "99.5", "101.2"]),
        matrix=_matrix("mixed", datetime(2026, 2, 3, 10, 2, tzinfo=UTC)),
    )
    assert ranging.state.label == "range"

    high_vol_engine = MarketRegimeEngine(config=_config())
    high_vol = high_vol_engine.classify(
        structure=_structure(["100", "110", "101", "109"]),
        matrix=_matrix("mixed", datetime(2026, 2, 3, 10, 3, tzinfo=UTC)),
    )
    assert high_vol.state.label == "high_volatility"


def test_regime_hysteresis_holds_previous_state_near_boundary() -> None:
    engine = MarketRegimeEngine(config=_config())
    baseline = engine.classify(
        structure=_structure(["100", "102", "104", "106"]),
        matrix=_matrix("aligned_bullish", datetime(2026, 2, 3, 10, 4, tzinfo=UTC)),
    )
    assert baseline.state.label == "trend"

    held = engine.classify(
        structure=_structure(["101", "101.5", "102", "102.5"]),
        matrix=_matrix("mixed", datetime(2026, 2, 3, 10, 5, tzinfo=UTC)),
    )
    assert held.state.label == "trend"
    assert held.state.reason == "hysteresis_hold"
