from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from nexora.pnf import ColumnDirection, PnfTransition, PnfTransitionReason, PnfTransitionType
from nexora.structure import StructureEngine, StructureSnapshotStore


def _transition(
    *,
    sequence: int,
    price: str,
    direction: ColumnDirection,
    transition_type: PnfTransitionType = "extension",
) -> PnfTransition:
    event_time = datetime(2026, 2, 2, 9, 0, 0, tzinfo=UTC) + timedelta(seconds=sequence)
    reason: PnfTransitionReason = "extended" if transition_type == "extension" else "reversed"
    if transition_type == "seed":
        reason = "seed_confirmed"
    return PnfTransition(
        type=transition_type,
        reason=reason,
        symbol="XAUUSD",
        column_id=1,
        direction=direction,
        from_price=Decimal(price) - Decimal("0.5"),
        to_price=Decimal(price),
        boxes_moved=1,
        event_time=event_time,
        source_event_id=f"evt-{sequence}",
        identity_key=f"tr-{sequence}",
        config_version="p3-fixed-v1",
        effective_box_size=Decimal("1.0"),
        sizing_rule_version="p4-fixed-v1",
    )


def test_structure_confirms_pivot_and_tracks_occurrence_vs_confirmation_time() -> None:
    engine = StructureEngine(symbol="XAUUSD")
    engine.process(_transition(sequence=1, price="100.0", direction="X", transition_type="seed"))
    engine.process(_transition(sequence=2, price="105.0", direction="X"))
    snapshot = engine.process(
        _transition(sequence=3, price="101.0", direction="O", transition_type="reversal")
    )

    assert len(snapshot.pivots) == 1
    pivot = snapshot.pivots[0]
    assert pivot.kind == "high"
    assert pivot.occurrence_time < pivot.confirmation_time
    assert pivot.source_transition_id == "tr-2"


def test_structure_level_lifecycle_includes_invalidated_state() -> None:
    engine = StructureEngine(symbol="XAUUSD")
    stream = (
        _transition(sequence=1, price="100.0", direction="X", transition_type="seed"),
        _transition(sequence=2, price="105.0", direction="X"),
        _transition(sequence=3, price="101.0", direction="O", transition_type="reversal"),
        _transition(sequence=4, price="106.0", direction="X", transition_type="reversal"),
    )
    snapshot = None
    for transition in stream:
        snapshot = engine.process(transition)
    assert snapshot is not None
    assert any(
        level.side == "resistance" and level.status == "invalidated"
        for level in snapshot.levels
    )


def test_structure_prefix_invariance_and_persistence_rebuild() -> None:
    base = (
        _transition(sequence=1, price="100.0", direction="X", transition_type="seed"),
        _transition(sequence=2, price="104.0", direction="X"),
        _transition(sequence=3, price="101.0", direction="O", transition_type="reversal"),
    )
    future = base + (
        _transition(sequence=4, price="106.0", direction="X", transition_type="reversal"),
    )

    first_engine = StructureEngine(symbol="XAUUSD")
    prefix = [first_engine.process(item) for item in base]

    full_engine = StructureEngine(symbol="XAUUSD")
    full = [full_engine.process(item) for item in future]
    assert full[: len(prefix)] == prefix

    store = StructureSnapshotStore()
    for snapshot in full:
        store.append(snapshot)
    replay = store.replay()
    assert replay == store.rebuild()
    third_snapshot = cast(dict[str, Any], replay[2])
    pivots = cast(list[dict[str, Any]], third_snapshot["pivots"])
    assert pivots[0]["source_transition_id"] == "tr-2"
