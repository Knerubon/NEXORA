from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from nexora.pnf import PnfTransition, PnfTransitionReason, PnfTransitionType
from nexora.structure import ConfirmedPivot, StructureSnapshot
from nexora.trendline import TrendlineEngine
from nexora.trendline.models import (
    TrendlineAnchor,
    TrendlineKind,
    TrendlineLifecycleState,
    TrendlineLine,
    TrendlineSnapshot,
)

SYMBOL = "XAUUSD"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _t(seq: int, column_id: int, price: str, *, box: str = "1.0") -> PnfTransition:
    transition_type: PnfTransitionType = "seed" if seq == 1 else "reversal"
    reason: PnfTransitionReason = "seed_confirmed" if seq == 1 else "reversed"
    return PnfTransition(
        type=transition_type,
        reason=reason,
        symbol=SYMBOL,
        column_id=column_id,
        direction="X",
        from_price=Decimal(price),
        to_price=Decimal(price),
        boxes_moved=1,
        event_time=BASE + timedelta(minutes=seq),
        source_event_id=f"evt-{seq}",
        identity_key=f"tr-{seq}",
        config_version="p3-fixed-v1",
        effective_box_size=Decimal(box),
        sizing_rule_version="p4-fixed-v1",
    )


def _pivot(kind: Literal["high", "low"], price: str, source_transition_id: str) -> ConfirmedPivot:
    return ConfirmedPivot(
        kind=kind,
        price=Decimal(price),
        occurrence_time=BASE,
        confirmation_time=BASE,
        source_transition_id=source_transition_id,
        config_version="p3-fixed-v1",
    )


def _structure(pivots: tuple[ConfirmedPivot, ...]) -> StructureSnapshot:
    return StructureSnapshot(
        schema_version=1, symbol=SYMBOL, sequence=len(pivots), pivots=pivots, levels=()
    )


def _anchor(
    kind: Literal["high", "low"], price: str, column_id: int, pivot_id: str
) -> TrendlineAnchor:
    return TrendlineAnchor(
        pivot_kind=kind,
        price=Decimal(price),
        column_id=column_id,
        source_pivot_id=pivot_id,
        event_time=BASE,
    )


def _line(
    kind: TrendlineKind,
    anchor_a: TrendlineAnchor,
    anchor_b: TrendlineAnchor,
    *,
    state: TrendlineLifecycleState = "active",
    **overrides: object,
) -> TrendlineLine:
    slope = (anchor_b.price - anchor_a.price) / Decimal(anchor_b.column_id - anchor_a.column_id)
    base = TrendlineLine(
        line_id="test-line",
        kind=kind,
        state=state,
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        slope_price_per_column=slope,
        projected_price_at_latest_column=anchor_b.price,
        touch_columns=(),
        break_column=None,
        break_transition_id=None,
        retest_column=None,
        retest_resolved_column=None,
        retest_resolved_sequence=None,
        retest_outcome="none",
        replaced_by_line_id=None,
        age_columns=0,
        config_version="p3-fixed-v1",
        evidence=(),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _step(
    engine: TrendlineEngine, transition: PnfTransition, structure: StructureSnapshot
) -> TrendlineSnapshot:
    """process() now mutates only (M1 fix); tests read state via a fresh snapshot()."""
    engine.process(transition, structure)
    return engine.snapshot()


def _pivot_stream(prices: list[str]) -> list[tuple[PnfTransition, tuple[ConfirmedPivot, ...]]]:
    transitions = [_t(i + 1, i + 1, price) for i, price in enumerate(prices)]
    pivots: list[ConfirmedPivot] = []
    steps: list[tuple[PnfTransition, tuple[ConfirmedPivot, ...]]] = []
    for index, transition in enumerate(transitions):
        if index >= 2:
            left, center, right = transitions[index - 2], transitions[index - 1], transition
            if center.to_price > left.to_price and center.to_price > right.to_price:
                pivots.append(_pivot("high", str(center.to_price), center.identity_key))
            elif center.to_price < left.to_price and center.to_price < right.to_price:
                pivots.append(_pivot("low", str(center.to_price), center.identity_key))
        steps.append((transition, tuple(pivots)))
    return steps


def _zigzag_stream() -> list[tuple[PnfTransition, tuple[ConfirmedPivot, ...]]]:
    return _pivot_stream(["100", "90", "98", "93", "101", "97", "105", "94", "108"])


# A single realistic price path (via real StructureEngine-style pivot ingestion) that drives a
# bullish line through its entire lifecycle: formation (t6) -> touch (t7) -> break (t8) ->
# retest entry (t9) -> retest resolution (t10, RETEST_HELD) -> replacement (t11), with t12/t13
# as pure "future" filler beyond both the break and retest-resolution boundaries. A fresh valid
# anchor pair (101.5@5, 102.9@8) becomes available at t9 already (the break-point transition
# t8 is itself a local low once t9 bounces back up) and remains available through t10 and t11 --
# this is what makes t10 a genuine H1 same-transition-chaining test, not a contrived one.
LIFECYCLE_PRICES = [
    "110", "100", "115", "105", "101.5", "110", "103.0", "102.9", "103.2", "102.0",
    "90", "95", "100",
]


# --- 1. bullish LOW -> HIGHER LOW formation ---------------------------------------------------


def test_bullish_low_to_higher_low_forms_line() -> None:
    engine = TrendlineEngine(SYMBOL)
    pivots: list[ConfirmedPivot] = []
    engine.process(_t(1, 1, "100"), _structure(tuple(pivots)))
    engine.process(_t(2, 2, "100"), _structure(tuple(pivots)))
    engine.process(_t(3, 3, "105"), _structure(tuple(pivots)))
    engine.process(_t(4, 4, "102"), _structure(tuple(pivots)))
    pivots.append(_pivot("low", "100", "tr-2"))
    snap = _step(engine, _t(5, 5, "90"), _structure(tuple(pivots)))
    assert snap.active_bullish is None
    pivots.append(_pivot("low", "102", "tr-4"))
    snap = _step(engine, _t(6, 6, "95"), _structure(tuple(pivots)))
    line = snap.active_bullish
    assert line is not None
    assert line.kind == "bullish_support"
    assert line.state == "active"
    assert line.anchor_a.price == Decimal("100")
    assert line.anchor_b.price == Decimal("102")
    assert line.anchor_a.column_id == 2
    assert line.anchor_b.column_id == 4
    assert line.slope_price_per_column == Decimal("1")


# --- 2. bearish HIGH -> LOWER HIGH formation --------------------------------------------------


def test_bearish_high_to_lower_high_forms_line() -> None:
    engine = TrendlineEngine(SYMBOL)
    pivots: list[ConfirmedPivot] = []
    engine.process(_t(1, 1, "100"), _structure(tuple(pivots)))
    engine.process(_t(2, 2, "110"), _structure(tuple(pivots)))
    engine.process(_t(3, 3, "95"), _structure(tuple(pivots)))
    engine.process(_t(4, 4, "107"), _structure(tuple(pivots)))
    pivots.append(_pivot("high", "110", "tr-2"))
    engine.process(_t(5, 5, "120"), _structure(tuple(pivots)))
    pivots.append(_pivot("high", "107", "tr-4"))
    snap = _step(engine, _t(6, 6, "115"), _structure(tuple(pivots)))
    line = snap.active_bearish
    assert line is not None
    assert line.kind == "bearish_resistance"
    assert line.anchor_a.price == Decimal("110")
    assert line.anchor_b.price == Decimal("107")
    assert line.slope_price_per_column == Decimal("-1.5")


# --- 3. invalid latest pair produces no line ------------------------------------------------


def test_invalid_latest_pair_produces_no_line() -> None:
    engine = TrendlineEngine(SYMBOL)
    engine.process(_t(1, 1, "100"), _structure(()))
    engine.process(_t(2, 2, "100"), _structure(()))
    engine.process(_t(3, 3, "50"), _structure(()))
    engine.process(_t(4, 4, "105"), _structure(()))
    pivots = (_pivot("high", "100", "tr-2"), _pivot("high", "105", "tr-4"))
    snap = _step(engine, _t(5, 5, "90"), _structure(pivots))
    assert snap.active_bearish is None


# --- 4. no backward anchor search -------------------------------------------------------------


def test_no_backward_anchor_search() -> None:
    engine = TrendlineEngine(SYMBOL)
    engine.process(_t(1, 1, "100"), _structure(()))
    engine.process(_t(2, 2, "100"), _structure(()))
    engine.process(_t(3, 3, "50"), _structure(()))
    engine.process(_t(4, 4, "90"), _structure(()))
    engine.process(_t(5, 5, "60"), _structure(()))
    engine.process(_t(6, 6, "95"), _structure(()))
    pivots = (
        _pivot("high", "100", "tr-2"),
        _pivot("high", "90", "tr-4"),
        _pivot("high", "95", "tr-6"),
    )
    snap = _step(engine, _t(7, 7, "70"), _structure(pivots))
    # (h2=90, h3=95) is invalid (higher high); (h1=100, h3=95) would be valid if the
    # engine incorrectly searched backward past the two most recent pivots.
    assert snap.active_bearish is None


# --- 5. exact Decimal slope / 6. exact Decimal projection -----------------------------------


def test_exact_decimal_slope_and_projection() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    line = _line("bullish_support", anchor_a, anchor_b)
    assert line.slope_price_per_column == Decimal("0.5")
    assert isinstance(line.slope_price_per_column, Decimal)

    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = line
    snap = _step(engine, _t(1, 8, "103.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.projected_price_at_latest_column == Decimal("103.0")
    assert isinstance(snap.active_bullish.projected_price_at_latest_column, Decimal)


# --- 7. equality with line is NOT break -------------------------------------------------------


def test_equality_with_line_is_not_break() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    snap = _step(engine, _t(1, 8, "103.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state != "broken"
    assert snap.active_bullish.break_column is None


# --- 8. small penetration IS break -------------------------------------------------------------


def test_small_penetration_is_break() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    snap = _step(engine, _t(1, 8, "102.99"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "broken"
    assert snap.active_bullish.break_column == 8
    assert snap.active_bullish.break_transition_id == "tr-1"


# --- 9. touch at distance 0 --------------------------------------------------------------------


def test_touch_at_distance_zero() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    snap = _step(engine, _t(1, 8, "103.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.touch_columns == (8,)
    assert snap.active_bullish.state == "active"


# --- 10. touch just inside 1 box ---------------------------------------------------------------


def test_touch_just_inside_one_box() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    # projected@8 = 103.0; box=1.0; price=103.99 -> distance=0.99 < 1.0
    snap = _step(engine, _t(1, 8, "103.99", box="1.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.touch_columns == (8,)


# --- 11. exactly 1 box away is NOT touch --------------------------------------------------------


def test_exactly_one_box_away_is_not_touch() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    # projected@8 = 103.0; box=1.0; price=104.0 -> distance=1.0, exclusive upper bound
    snap = _step(engine, _t(1, 8, "104.0", box="1.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.touch_columns == ()
    assert snap.active_bullish.state == "active"
    assert snap.active_bullish.break_column is None


# --- 12. bullish break ---------------------------------------------------------------------------


def test_bullish_break() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    # projected@8 = 103.0; price=101.5 -> distance=-1.5
    snap = _step(engine, _t(1, 8, "101.5"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "broken"
    assert snap.active_bullish.break_column == 8


# --- 13. bearish break ----------------------------------------------------------------------------


def test_bearish_break() -> None:
    anchor_a = _anchor("high", "110.0", 2, "tr-2")
    anchor_b = _anchor("high", "108.0", 4, "tr-4")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bearish = _line("bearish_resistance", anchor_a, anchor_b)
    # slope=-1.0; projected@8 = 108 - 1*(8-4) = 104.0; price=105.5 -> distance=+1.5
    snap = _step(engine, _t(1, 8, "105.5"), _structure(()))
    assert snap.active_bearish is not None
    assert snap.active_bearish.state == "broken"
    assert snap.active_bearish.break_column == 8


# --- 14. bullish retest entry ---------------------------------------------------------------------


def test_bullish_retest_entry() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b,
        state="broken", break_column=8, break_transition_id="tr-8"
    )
    # projected@9 = 103.5; price=103.2 -> distance=-0.3, |distance| < box(1.0)
    snap = _step(engine, _t(1, 9, "103.2"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "retesting"
    assert snap.active_bullish.retest_column == 9


# --- 15. bearish retest entry ---------------------------------------------------------------------


def test_bearish_retest_entry() -> None:
    anchor_a = _anchor("high", "110.0", 2, "tr-2")
    anchor_b = _anchor("high", "108.0", 4, "tr-4")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bearish = _line(
        "bearish_resistance", anchor_a, anchor_b,
        state="broken", break_column=8, break_transition_id="tr-8"
    )
    # projected@9 = 103.0; price=103.4 -> distance=+0.4, |distance| < box(1.0)
    snap = _step(engine, _t(1, 9, "103.4"), _structure(()))
    assert snap.active_bearish is not None
    assert snap.active_bearish.state == "retesting"
    assert snap.active_bearish.retest_column == 9


# --- 16. bullish RETEST_HELD ----------------------------------------------------------------------


def test_bullish_retest_held() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b, state="retesting", break_column=8,
        break_transition_id="tr-8", retest_column=9,
    )
    # projected@10 = 104.0; price=102.0 -> distance=-2.0 <= -box(1.0)
    snap = _step(engine, _t(1, 10, "102.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "retest_held"
    assert snap.active_bullish.retest_outcome == "held"
    assert snap.active_bullish.retest_resolved_column == 10
    assert snap.active_bullish.retest_resolved_sequence == 1  # H1: engine sequence, not wall clock


# --- 17. bullish RETEST_FAILED --------------------------------------------------------------------


def test_bullish_retest_failed() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b, state="retesting", break_column=8,
        break_transition_id="tr-8", retest_column=9,
    )
    # projected@10 = 104.0; price=105.5 -> distance=+1.5 >= box(1.0)
    snap = _step(engine, _t(1, 10, "105.5"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "retest_failed"
    assert snap.active_bullish.retest_outcome == "failed"


# --- 18. bearish RETEST_HELD ----------------------------------------------------------------------


def test_bearish_retest_held() -> None:
    anchor_a = _anchor("high", "110.0", 2, "tr-2")
    anchor_b = _anchor("high", "108.0", 4, "tr-4")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bearish = _line(
        "bearish_resistance", anchor_a, anchor_b, state="retesting", break_column=8,
        break_transition_id="tr-8", retest_column=9,
    )
    # projected@10 = 102.0; price=104.0 -> distance=+2.0 >= box(1.0)
    snap = _step(engine, _t(1, 10, "104.0"), _structure(()))
    assert snap.active_bearish is not None
    assert snap.active_bearish.state == "retest_held"
    assert snap.active_bearish.retest_outcome == "held"


# --- 19. bearish RETEST_FAILED --------------------------------------------------------------------


def test_bearish_retest_failed() -> None:
    anchor_a = _anchor("high", "110.0", 2, "tr-2")
    anchor_b = _anchor("high", "108.0", 4, "tr-4")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bearish = _line(
        "bearish_resistance", anchor_a, anchor_b, state="retesting", break_column=8,
        break_transition_id="tr-8", retest_column=9,
    )
    # projected@10 = 102.0; price=100.0 -> distance=-2.0 <= -box(1.0)
    snap = _step(engine, _t(1, 10, "100.0"), _structure(()))
    assert snap.active_bearish is not None
    assert snap.active_bearish.state == "retest_failed"
    assert snap.active_bearish.retest_outcome == "failed"


# --- 20. sticky retest_outcome --------------------------------------------------------------------


def test_sticky_retest_outcome_survives_further_transitions() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b, state="retest_held", retest_outcome="held",
        break_column=8, break_transition_id="tr-8", retest_column=9, retest_resolved_column=10,
        retest_resolved_sequence=0,
    )
    snap = _step(engine, _t(1, 11, "999.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "retest_held"
    assert snap.active_bullish.retest_outcome == "held"
    snap = _step(engine, _t(2, 12, "-5.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.retest_outcome == "held"


# --- 21. broken -> replaced without retest --------------------------------------------------------


def test_broken_replaced_without_retest_preserves_none_outcome() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b,
        state="broken", break_column=8, break_transition_id="tr-8"
    )
    engine._lows = [
        _anchor("low", "105.0", 9, "tr-9"),
        _anchor("low", "107.0", 11, "tr-11"),
    ]
    engine._known_pivot_count = 2
    pivots = (_pivot("low", "105.0", "tr-9"), _pivot("low", "107.0", "tr-11"))
    # price far away keeps the old line "broken" (no accidental retest entry) this step.
    snap = _step(engine, _t(1, 12, "50.0"), _structure(pivots))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "active"
    assert snap.active_bullish.anchor_a.price == Decimal("105.0")
    assert snap.active_bullish.anchor_b.price == Decimal("107.0")
    assert len(snap.history) == 1
    old = snap.history[0]
    assert old.state == "replaced"
    assert old.retest_outcome == "none"
    assert old.replaced_by_line_id == snap.active_bullish.line_id
    assert old.anchor_a.price == Decimal("100.0")
    assert old.anchor_b.price == Decimal("101.5")


# --- 22. retest_held -> replaced preserving outcome -----------------------------------------------


def test_retest_held_replaced_preserves_held_outcome() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b, state="retest_held", retest_outcome="held",
        break_column=8, break_transition_id="tr-8", retest_column=9, retest_resolved_column=10,
        retest_resolved_sequence=0,  # resolved "before" this engine's own tracked history (H1-safe)
    )
    engine._lows = [
        _anchor("low", "105.0", 9, "tr-9"),
        _anchor("low", "107.0", 11, "tr-11"),
    ]
    engine._known_pivot_count = 2
    pivots = (_pivot("low", "105.0", "tr-9"), _pivot("low", "107.0", "tr-11"))
    snap = _step(engine, _t(1, 12, "50.0"), _structure(pivots))
    assert len(snap.history) == 1
    old = snap.history[0]
    assert old.state == "replaced"
    assert old.retest_outcome == "held"


# --- 23. retest_failed -> replaced preserving outcome ---------------------------------------------


def test_retest_failed_replaced_preserves_failed_outcome() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b, state="retest_failed", retest_outcome="failed",
        break_column=8, break_transition_id="tr-8", retest_column=9, retest_resolved_column=10,
        retest_resolved_sequence=0,
    )
    engine._lows = [
        _anchor("low", "105.0", 9, "tr-9"),
        _anchor("low", "107.0", 11, "tr-11"),
    ]
    engine._known_pivot_count = 2
    pivots = (_pivot("low", "105.0", "tr-9"), _pivot("low", "107.0", "tr-11"))
    snap = _step(engine, _t(1, 12, "50.0"), _structure(pivots))
    assert len(snap.history) == 1
    old = snap.history[0]
    assert old.state == "replaced"
    assert old.retest_outcome == "failed"


# --- H1. retest resolution and replacement must not occur on the same transition ------------------


def test_h1_retest_resolution_and_replacement_do_not_share_a_transition() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b, state="retesting",
        break_column=8, break_transition_id="tr-8", retest_column=9,
    )
    # A fresh valid anchor pair is already available (structurally identical to the "a new
    # Structure pivot forms a replacement candidate" scenario flagged in the review's H1).
    engine._lows = [anchor_a, anchor_b, _anchor("low", "105.0", 9, "tr-9")]

    # Transition N: resolves the retest (distance=-2.0 <= -box(1.0) -> RETEST_HELD).
    snap_n = _step(engine, _t(1, 10, "50.0"), _structure(()))
    assert snap_n.active_bullish is not None
    assert snap_n.active_bullish.state == "retest_held"
    assert snap_n.active_bullish.retest_outcome == "held"
    assert snap_n.active_bullish.anchor_a.price == Decimal("100.0")  # NOT replaced yet
    assert snap_n.active_bullish.anchor_b.price == Decimal("101.5")
    assert snap_n.history == ()

    # Transition N+1: no new pivot is required -- the pair was already available at N.
    snap_next = _step(engine, _t(2, 11, "50.0"), _structure(()))
    assert snap_next.active_bullish is not None
    assert snap_next.active_bullish.state == "active"
    assert snap_next.active_bullish.anchor_a.price == Decimal("101.5")
    assert snap_next.active_bullish.anchor_b.price == Decimal("105.0")
    assert len(snap_next.history) == 1
    assert snap_next.history[0].state == "replaced"
    assert snap_next.history[0].retest_outcome == "held"
    assert snap_next.history[0].anchor_a.price == Decimal("100.0")
    assert snap_next.history[0].anchor_b.price == Decimal("101.5")


# --- 24. Adaptive Box changes do not change line projection ---------------------------------------


def test_adaptive_box_change_does_not_change_projection() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    small_box_engine = TrendlineEngine(SYMBOL)
    small_box_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    large_box_engine = TrendlineEngine(SYMBOL)
    large_box_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)

    small_snap = _step(small_box_engine, _t(1, 9, "103.6", box="0.5"), _structure(()))
    large_snap = _step(large_box_engine, _t(1, 9, "103.6", box="5.0"), _structure(()))

    assert small_snap.active_bullish is not None
    assert large_snap.active_bullish is not None
    assert (
        small_snap.active_bullish.projected_price_at_latest_column
        == large_snap.active_bullish.projected_price_at_latest_column
        == Decimal("103.5")
    )


# --- 25. Adaptive Box changes correctly rescale touch/retest tolerance ----------------------------


def test_adaptive_box_change_rescales_touch_tolerance() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    # projected@9 = 103.5; price=104.2 -> distance=+0.7
    narrow_engine = TrendlineEngine(SYMBOL)
    narrow_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    narrow_snap = _step(narrow_engine, _t(1, 9, "104.2", box="0.5"), _structure(()))
    assert narrow_snap.active_bullish is not None
    assert narrow_snap.active_bullish.touch_columns == ()  # 0.7 >= box(0.5): not a touch

    wide_engine = TrendlineEngine(SYMBOL)
    wide_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    wide_snap = _step(wide_engine, _t(1, 9, "104.2", box="1.0"), _structure(()))
    assert wide_snap.active_bullish is not None
    assert wide_snap.active_bullish.touch_columns == (9,)  # 0.7 < box(1.0): a touch


# --- 26. incremental process == replay/reconstruction ---------------------------------------------


def test_incremental_process_matches_full_replay() -> None:
    steps = _zigzag_stream()

    engine_a = TrendlineEngine(SYMBOL)
    incremental = []
    for transition, pivots in steps:
        incremental.append(_step(engine_a, transition, _structure(pivots)))

    engine_b = TrendlineEngine(SYMBOL)
    replayed_final = None
    for transition, pivots in steps:
        replayed_final = _step(engine_b, transition, _structure(pivots))

    assert incremental[-1] == replayed_final


# --- 27. prefix invariance / explicit no-lookahead ---------------------------------------------
# Strengthened per Rin's fix-review directive: boundaries are placed strictly after a BREAK and
# strictly after a RETEST resolution (not just after formation), using the H1 lifecycle stream.


def _run_stream(
    steps: list[tuple[PnfTransition, tuple[ConfirmedPivot, ...]]],
) -> list[TrendlineSnapshot]:
    engine = TrendlineEngine(SYMBOL)
    return [_step(engine, transition, _structure(pivots)) for transition, pivots in steps]


def test_prefix_invariance_across_break_and_retest_resolution_boundaries() -> None:
    steps = _pivot_stream(LIFECYCLE_PRICES)
    full_snapshots = _run_stream(steps)

    # Boundary 1: strictly after the BREAK transition (t8, index 7).
    break_prefix_len = 8
    break_prefix_snapshots = _run_stream(steps[:break_prefix_len])
    assert break_prefix_snapshots == full_snapshots[:break_prefix_len]
    broken_line = break_prefix_snapshots[-1].active_bullish
    assert broken_line is not None
    assert broken_line.state == "broken"
    future_broken_line = full_snapshots[break_prefix_len - 1].active_bullish
    assert future_broken_line is not None
    assert future_broken_line.break_column == broken_line.break_column
    assert future_broken_line.break_transition_id == broken_line.break_transition_id
    assert future_broken_line.anchor_a == broken_line.anchor_a
    assert future_broken_line.anchor_b == broken_line.anchor_b
    assert future_broken_line.slope_price_per_column == broken_line.slope_price_per_column

    # Boundary 2: strictly after the RETEST RESOLUTION transition (t10, index 9).
    retest_prefix_len = 10
    retest_prefix_snapshots = _run_stream(steps[:retest_prefix_len])
    assert retest_prefix_snapshots == full_snapshots[:retest_prefix_len]
    resolved_line = retest_prefix_snapshots[-1].active_bullish
    assert resolved_line is not None
    assert resolved_line.state == "retest_held"
    future_resolved_line = full_snapshots[retest_prefix_len - 1].active_bullish
    assert future_resolved_line is not None
    assert future_resolved_line.retest_column == resolved_line.retest_column
    assert future_resolved_line.retest_resolved_column == resolved_line.retest_resolved_column
    assert future_resolved_line.retest_outcome == resolved_line.retest_outcome
    assert future_resolved_line.anchor_a == resolved_line.anchor_a
    assert future_resolved_line.anchor_b == resolved_line.anchor_b
    assert future_resolved_line.slope_price_per_column == resolved_line.slope_price_per_column
    # Appending t11 (replacement), t12, t13 to the full run must not retroactively touch
    # anything recorded at or before the retest-resolution boundary -- already proven by the
    # snapshot-list equality above; this is the same guarantee stated field-by-field.


# --- 28. future pivot cannot rewrite historical line ----------------------------------------------


def test_future_pivot_cannot_rewrite_historical_line() -> None:
    steps = _zigzag_stream()
    engine = TrendlineEngine(SYMBOL)
    snapshots = []
    for transition, pivots in steps:
        snapshots.append(_step(engine, transition, _structure(pivots)))

    recorded_at_step = next(s for s in snapshots if s.active_bullish is not None)
    captured_line = recorded_at_step.active_bullish
    assert captured_line is not None
    captured_anchor_a = captured_line.anchor_a
    captured_anchor_b = captured_line.anchor_b
    captured_slope = captured_line.slope_price_per_column

    # captured line's geometry must never change, whether it stays active/replaced,
    # regardless of how many further (future, at capture time) transitions arrive.
    final_snapshot = snapshots[-1]
    candidates = (final_snapshot.active_bullish, *final_snapshot.history)
    line_after_more_history = next(
        (
            line
            for line in candidates
            if line is not None
            and line.anchor_a.source_pivot_id == captured_anchor_a.source_pivot_id
            and line.anchor_b.source_pivot_id == captured_anchor_b.source_pivot_id
        ),
        None,
    )
    assert line_after_more_history is not None
    assert line_after_more_history.anchor_a == captured_anchor_a
    assert line_after_more_history.anchor_b == captured_anchor_b
    assert line_after_more_history.slope_price_per_column == captured_slope


# --- 29. deterministic line_id --------------------------------------------------------------------


def test_deterministic_line_id() -> None:
    def _form() -> str:
        engine = TrendlineEngine(SYMBOL)
        pivots: list[ConfirmedPivot] = []
        engine.process(_t(1, 1, "100"), _structure(tuple(pivots)))
        engine.process(_t(2, 2, "100"), _structure(tuple(pivots)))
        engine.process(_t(3, 3, "105"), _structure(tuple(pivots)))
        engine.process(_t(4, 4, "102"), _structure(tuple(pivots)))
        pivots.append(_pivot("low", "100", "tr-2"))
        engine.process(_t(5, 5, "90"), _structure(tuple(pivots)))
        pivots.append(_pivot("low", "102", "tr-4"))
        snap = _step(engine, _t(6, 6, "95"), _structure(tuple(pivots)))
        assert snap.active_bullish is not None
        return snap.active_bullish.line_id

    first_id = _form()
    second_id = _form()
    assert first_id == second_id
    assert isinstance(first_id, str)
    assert len(first_id) == 64  # sha256 hex digest


# --- 30. bounded history behavior (not implemented: unbounded, nothing silently pruned) -----------


def test_history_retains_every_replaced_line_without_pruning() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b,
        state="broken", break_column=8, break_transition_id="tr-8"
    )
    engine._lows = [anchor_a, anchor_b]
    engine._known_pivot_count = 10_000  # anchors are injected directly; bypass real pivot ingestion

    # Round 1: a fresh, valid pair replaces the pre-existing broken line.
    engine._lows.append(_anchor("low", "105.0", 9, "tr-9"))
    snap = _step(engine, _t(1, 20, "50.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.anchor_b.price == Decimal("105.0")
    assert len(snap.history) == 1

    # Round 2: the just-formed line breaks on this same low price, then is itself replaced.
    engine._lows.append(_anchor("low", "107.0", 11, "tr-11"))
    snap = _step(engine, _t(2, 21, "50.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.anchor_b.price == Decimal("107.0")
    assert len(snap.history) == 2
    assert [line.state for line in snap.history] == ["replaced", "replaced"]
    assert snap.history[0].anchor_a.price == Decimal("100.0")
    assert snap.history[1].anchor_a.price == Decimal("101.5")


# --- M2. equal-column-id guard: defends the slope division, must be a safe no-op ----------------


def test_equal_column_id_guard_is_a_safe_no_op_and_does_not_corrupt_active_line() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line(
        "bullish_support", anchor_a, anchor_b,
        state="broken", break_column=8, break_transition_id="tr-8"
    )
    # Two pathological same-kind anchors sharing column_id=9 -- cannot occur from real
    # StructureEngine pivots (verified in review), but must not crash the slope division
    # and must not silently form/replace using an invalid pair.
    engine._lows = [
        anchor_a,
        anchor_b,
        _anchor("low", "90.0", 9, "tr-9a"),
        _anchor("low", "95.0", 9, "tr-9b"),
    ]
    engine._known_pivot_count = 10_000

    # price far from the existing broken line's projection: no accidental retest entry.
    snap = _step(engine, _t(1, 12, "50.0"), _structure(()))

    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "broken"  # unchanged/uncorrupted
    assert snap.active_bullish.anchor_a.price == Decimal("100.0")
    assert snap.active_bullish.anchor_b.price == Decimal("101.5")
    assert snap.history == ()  # no line formed or replaced from the equal-column pair


# --- M3. full lifecycle via real StructureEngine-style ingestion, including the H1 guard --------


def test_full_lifecycle_via_real_ingestion_with_h1_same_transition_guard() -> None:
    steps = _pivot_stream(LIFECYCLE_PRICES)
    engine = TrendlineEngine(SYMBOL)
    snapshots = [_step(engine, transition, _structure(pivots)) for transition, pivots in steps]

    after_formation = snapshots[5]  # t6
    after_touch = snapshots[6]  # t7
    after_break = snapshots[7]  # t8
    after_retest_entry = snapshots[8]  # t9
    after_resolution = snapshots[9]  # t10 -- H1 checkpoint N
    after_next = snapshots[10]  # t11 -- H1 checkpoint N+1

    assert after_formation.active_bullish is not None
    assert after_formation.active_bullish.state == "active"
    assert after_formation.active_bullish.anchor_a.price == Decimal("100")
    assert after_formation.active_bullish.anchor_b.price == Decimal("101.5")

    assert after_touch.active_bullish is not None
    assert after_touch.active_bullish.state == "active"
    assert after_touch.active_bullish.touch_columns == (7,)

    assert after_break.active_bullish is not None
    assert after_break.active_bullish.state == "broken"
    assert after_break.active_bullish.break_column == 8

    assert after_retest_entry.active_bullish is not None
    assert after_retest_entry.active_bullish.state == "retesting"
    assert after_retest_entry.active_bullish.retest_column == 9

    # H1: transition N (t10) resolves the retest -- must NOT replace on this same transition,
    # even though a fresh valid anchor pair (101.5@5, 102.9@8) is already available.
    resolved = after_resolution.active_bullish
    assert resolved is not None
    assert resolved.state == "retest_held"
    assert resolved.retest_outcome == "held"
    assert resolved.retest_resolved_column == 10
    assert resolved.anchor_a.price == Decimal("100")  # still the ORIGINAL anchors
    assert resolved.anchor_b.price == Decimal("101.5")
    assert after_resolution.history == ()  # not replaced yet

    # Transition N+1 (t11): replacement is now allowed. No new pivot was required -- the
    # pair was already available at (and before) the resolving transition.
    replaced_snapshot = after_next
    assert replaced_snapshot.active_bullish is not None
    assert replaced_snapshot.active_bullish.state == "active"
    assert replaced_snapshot.active_bullish.anchor_a.price == Decimal("101.5")
    assert replaced_snapshot.active_bullish.anchor_b.price == Decimal("102.9")
    assert len(replaced_snapshot.history) == 1
    archived = replaced_snapshot.history[0]
    assert archived.state == "replaced"
    assert archived.retest_outcome == "held"  # preserved
    assert archived.anchor_a.price == Decimal("100")  # frozen historical geometry
    assert archived.anchor_b.price == Decimal("101.5")
    assert archived.replaced_by_line_id == replaced_snapshot.active_bullish.line_id
