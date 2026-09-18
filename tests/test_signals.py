from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from nexora.market_regime import RegimeLabel, RegimeSnapshot, RegimeState
from nexora.matrix import MatrixAlignment, MatrixResolutionState, MatrixSnapshot
from nexora.pnf import ColumnDirection, PnfTransition
from nexora.signals import SignalConfig, SignalEngine, SignalSnapshotStore
from nexora.structure import CandidateLevel, ConfirmedPivot, StructureSnapshot


def _transition(identity: str, direction: ColumnDirection) -> PnfTransition:
    return PnfTransition(
        type="extension",
        reason="extended",
        symbol="XAUUSD",
        column_id=1,
        direction=direction,
        from_price=Decimal("100.0"),
        to_price=Decimal("101.0"),
        boxes_moved=1,
        event_time=datetime(2026, 2, 4, 9, 0, tzinfo=UTC),
        source_event_id=f"evt-{identity}",
        identity_key=identity,
        config_version="p3-fixed-v1",
        effective_box_size=Decimal("1.0"),
        sizing_rule_version="p4-fixed-v1",
    )


def _structure(
    kind: Literal["high", "low"],
    pivot_id: str,
    when: datetime,
) -> StructureSnapshot:
    return StructureSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        pivots=(
            ConfirmedPivot(
                kind=kind,
                price=Decimal("101.0"),
                occurrence_time=when,
                confirmation_time=when,
                source_transition_id=pivot_id,
                config_version="p3-fixed-v1",
            ),
        ),
        levels=(
            CandidateLevel(
                side="support",
                price=Decimal("100.0"),
                status="confirmed",
                source_pivot_id=pivot_id,
                updated_at=when,
            ),
        ),
    )


def _matrix(alignment: MatrixAlignment, when: datetime) -> MatrixSnapshot:
    return MatrixSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        watermark_sequence=1,
        generated_at=when,
        alignment=alignment,
        strength=3,
        resolutions=(
            MatrixResolutionState(
                name="fast",
                symbol="XAUUSD",
                direction="X",
                latest_transition=_transition("fast-1", "X"),
                status="ready",
            ),
            MatrixResolutionState(
                name="medium",
                symbol="XAUUSD",
                direction="X",
                latest_transition=_transition("medium-1", "X"),
                status="ready",
            ),
        ),
    )


def _regime(label: RegimeLabel, when: datetime) -> RegimeSnapshot:
    return RegimeSnapshot(
        schema_version=1,
        symbol="XAUUSD",
        sequence=1,
        state=RegimeState(
            label=label,
            reason="test",
            effective_time=when,
            source_ref="regime-ref",
            config_version="p7-regime-v1",
        ),
    )


def _config() -> SignalConfig:
    return SignalConfig(
        symbol="XAUUSD",
        cooldown_events=1,
        expiry_events=2,
        version="p8-signal-v1",
    )


def test_signal_creation_includes_reasons_and_machine_evidence() -> None:
    engine = SignalEngine(config=_config())
    now = datetime(2026, 2, 4, 9, 0, tzinfo=UTC)
    snapshot = engine.evaluate(
        structure=_structure("low", "pivot-1", now),
        regime=_regime("trend", now),
        matrix=_matrix("aligned_bullish", now),
    )
    assert snapshot.latest is not None
    assert snapshot.latest.side == "long"
    assert snapshot.latest.reasons
    assert snapshot.latest.reason_codes
    assert "pivot-1" in snapshot.latest.source_refs
    assert snapshot.latest.config_version == "p8-signal-v1"


def test_signal_dedup_and_cooldown_prevent_duplicate_emission() -> None:
    engine = SignalEngine(config=_config())
    now = datetime(2026, 2, 4, 9, 1, tzinfo=UTC)
    first = engine.evaluate(
        structure=_structure("low", "pivot-2", now),
        regime=_regime("trend", now),
        matrix=_matrix("aligned_bullish", now),
    )
    second = engine.evaluate(
        structure=_structure("low", "pivot-2", now),
        regime=_regime("trend", now),
        matrix=_matrix("aligned_bullish", now),
    )
    assert len(first.history) == 1
    assert len(second.history) == 1


def test_signal_expiry_restart_and_rebuild_are_deterministic() -> None:
    engine = SignalEngine(config=_config())
    now = datetime(2026, 2, 4, 9, 2, tzinfo=UTC)
    first = engine.evaluate(
        structure=_structure("high", "pivot-3", now),
        regime=_regime("trend", now),
        matrix=_matrix("aligned_bearish", now),
    )
    engine.evaluate(
        structure=StructureSnapshot(
            schema_version=1,
            symbol="XAUUSD",
            sequence=2,
            pivots=(),
            levels=(),
        ),
        regime=_regime("unknown", now),
        matrix=_matrix("unavailable", now),
    )
    expired = engine.evaluate(
        structure=StructureSnapshot(
            schema_version=1,
            symbol="XAUUSD",
            sequence=3,
            pivots=(),
            levels=(),
        ),
        regime=_regime("unknown", now),
        matrix=_matrix("unavailable", now),
    )
    expired = engine.evaluate(
        structure=StructureSnapshot(
            schema_version=1,
            symbol="XAUUSD",
            sequence=4,
            pivots=(),
            levels=(),
        ),
        regime=_regime("unknown", now),
        matrix=_matrix("unavailable", now),
    )

    assert first.latest is not None
    assert expired.history[-1].status == "expired"

    resumed = SignalEngine.from_snapshot(config=_config(), snapshot=expired)
    continued = resumed.evaluate(
        structure=_structure("high", "pivot-4", now),
        regime=_regime("trend", now),
        matrix=_matrix("aligned_bearish", now),
    )
    assert len(continued.history) == 2
    assert continued.history[-1].signal_id.endswith(":short")

    store = SignalSnapshotStore()
    store.append(first)
    store.append(expired)
    assert store.replay() == store.rebuild()
