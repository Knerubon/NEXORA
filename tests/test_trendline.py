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
        retest_outcome="none",
        replaced_by_line_id=None,
        age_columns=0,
        config_version="p3-fixed-v1",
        evidence=(),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- 1. bullish LOW -> HIGHER LOW formation ---------------------------------------------------


def test_bullish_low_to_higher_low_forms_line() -> None:
    engine = TrendlineEngine(SYMBOL)
    pivots: list[ConfirmedPivot] = []
    engine.process(_t(1, 1, "100"), _structure(tuple(pivots)))
    engine.process(_t(2, 2, "100"), _structure(tuple(pivots)))
    engine.process(_t(3, 3, "105"), _structure(tuple(pivots)))
    engine.process(_t(4, 4, "102"), _structure(tuple(pivots)))
    pivots.append(_pivot("low", "100", "tr-2"))
    snap = engine.process(_t(5, 5, "90"), _structure(tuple(pivots)))
    assert snap.active_bullish is None
    pivots.append(_pivot("low", "102", "tr-4"))
    snap = engine.process(_t(6, 6, "95"), _structure(tuple(pivots)))
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
    snap = engine.process(_t(6, 6, "115"), _structure(tuple(pivots)))
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
    snap = engine.process(_t(5, 5, "90"), _structure(pivots))
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
    snap = engine.process(_t(7, 7, "70"), _structure(pivots))
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
    snap = engine.process(_t(1, 8, "103.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.projected_price_at_latest_column == Decimal("103.0")
    assert isinstance(snap.active_bullish.projected_price_at_latest_column, Decimal)


# --- 7. equality with line is NOT break -------------------------------------------------------


def test_equality_with_line_is_not_break() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    snap = engine.process(_t(1, 8, "103.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state != "broken"
    assert snap.active_bullish.break_column is None


# --- 8. small penetration IS break -------------------------------------------------------------


def test_small_penetration_is_break() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    snap = engine.process(_t(1, 8, "102.99"), _structure(()))
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
    snap = engine.process(_t(1, 8, "103.0"), _structure(()))
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
    snap = engine.process(_t(1, 8, "103.99", box="1.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.touch_columns == (8,)


# --- 11. exactly 1 box away is NOT touch --------------------------------------------------------


def test_exactly_one_box_away_is_not_touch() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    engine = TrendlineEngine(SYMBOL)
    engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    # projected@8 = 103.0; box=1.0; price=104.0 -> distance=1.0, exclusive upper bound
    snap = engine.process(_t(1, 8, "104.0", box="1.0"), _structure(()))
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
    snap = engine.process(_t(1, 8, "101.5"), _structure(()))
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
    snap = engine.process(_t(1, 8, "105.5"), _structure(()))
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
    snap = engine.process(_t(1, 9, "103.2"), _structure(()))
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
    snap = engine.process(_t(1, 9, "103.4"), _structure(()))
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
    snap = engine.process(_t(1, 10, "102.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "retest_held"
    assert snap.active_bullish.retest_outcome == "held"
    assert snap.active_bullish.retest_resolved_column == 10


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
    snap = engine.process(_t(1, 10, "105.5"), _structure(()))
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
    snap = engine.process(_t(1, 10, "104.0"), _structure(()))
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
    snap = engine.process(_t(1, 10, "100.0"), _structure(()))
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
    )
    snap = engine.process(_t(1, 11, "999.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.state == "retest_held"
    assert snap.active_bullish.retest_outcome == "held"
    snap = engine.process(_t(2, 12, "-5.0"), _structure(()))
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
    snap = engine.process(_t(1, 12, "50.0"), _structure(pivots))
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
    )
    engine._lows = [
        _anchor("low", "105.0", 9, "tr-9"),
        _anchor("low", "107.0", 11, "tr-11"),
    ]
    engine._known_pivot_count = 2
    pivots = (_pivot("low", "105.0", "tr-9"), _pivot("low", "107.0", "tr-11"))
    snap = engine.process(_t(1, 12, "50.0"), _structure(pivots))
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
    )
    engine._lows = [
        _anchor("low", "105.0", 9, "tr-9"),
        _anchor("low", "107.0", 11, "tr-11"),
    ]
    engine._known_pivot_count = 2
    pivots = (_pivot("low", "105.0", "tr-9"), _pivot("low", "107.0", "tr-11"))
    snap = engine.process(_t(1, 12, "50.0"), _structure(pivots))
    assert len(snap.history) == 1
    old = snap.history[0]
    assert old.state == "replaced"
    assert old.retest_outcome == "failed"


# --- 24. Adaptive Box changes do not change line projection ---------------------------------------


def test_adaptive_box_change_does_not_change_projection() -> None:
    anchor_a = _anchor("low", "100.0", 2, "tr-2")
    anchor_b = _anchor("low", "101.5", 5, "tr-5")
    small_box_engine = TrendlineEngine(SYMBOL)
    small_box_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    large_box_engine = TrendlineEngine(SYMBOL)
    large_box_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)

    small_snap = small_box_engine.process(_t(1, 9, "103.6", box="0.5"), _structure(()))
    large_snap = large_box_engine.process(_t(1, 9, "103.6", box="5.0"), _structure(()))

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
    narrow_snap = narrow_engine.process(_t(1, 9, "104.2", box="0.5"), _structure(()))
    assert narrow_snap.active_bullish is not None
    assert narrow_snap.active_bullish.touch_columns == ()  # 0.7 >= box(0.5): not a touch

    wide_engine = TrendlineEngine(SYMBOL)
    wide_engine._active_bullish = _line("bullish_support", anchor_a, anchor_b)
    wide_snap = wide_engine.process(_t(1, 9, "104.2", box="1.0"), _structure(()))
    assert wide_snap.active_bullish is not None
    assert wide_snap.active_bullish.touch_columns == (9,)  # 0.7 < box(1.0): a touch


# --- 26. incremental process == replay/reconstruction ---------------------------------------------


def _zigzag_stream() -> list[tuple[PnfTransition, tuple[ConfirmedPivot, ...]]]:
    prices = ["100", "90", "98", "93", "101", "97", "105", "94", "108"]
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


def test_incremental_process_matches_full_replay() -> None:
    steps = _zigzag_stream()

    engine_a = TrendlineEngine(SYMBOL)
    incremental = [engine_a.process(transition, _structure(pivots)) for transition, pivots in steps]

    engine_b = TrendlineEngine(SYMBOL)
    for transition, pivots in steps:
        replayed_final = engine_b.process(transition, _structure(pivots))

    assert incremental[-1] == replayed_final


# --- 27. prefix invariance / explicit no-lookahead ------------------------------------------------


def test_prefix_invariance() -> None:
    steps = _zigzag_stream()
    prefix_steps = steps[:6]
    full_steps = steps

    prefix_engine = TrendlineEngine(SYMBOL)
    prefix_snapshots = [
        prefix_engine.process(transition, _structure(pivots)) for transition, pivots in prefix_steps
    ]

    full_engine = TrendlineEngine(SYMBOL)
    full_snapshots = [
        full_engine.process(transition, _structure(pivots)) for transition, pivots in full_steps
    ]

    assert prefix_snapshots == full_snapshots[: len(prefix_steps)]


# --- 28. future pivot cannot rewrite historical line ----------------------------------------------


def test_future_pivot_cannot_rewrite_historical_line() -> None:
    steps = _zigzag_stream()
    engine = TrendlineEngine(SYMBOL)
    snapshots = []
    for transition, pivots in steps:
        snapshots.append(engine.process(transition, _structure(pivots)))

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
        snap = engine.process(_t(6, 6, "95"), _structure(tuple(pivots)))
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
    snap = engine.process(_t(1, 20, "50.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.anchor_b.price == Decimal("105.0")
    assert len(snap.history) == 1

    # Round 2: the just-formed line breaks on this same low price, then is itself replaced.
    engine._lows.append(_anchor("low", "107.0", 11, "tr-11"))
    snap = engine.process(_t(2, 21, "50.0"), _structure(()))
    assert snap.active_bullish is not None
    assert snap.active_bullish.anchor_b.price == Decimal("107.0")
    assert len(snap.history) == 2
    assert [line.state for line in snap.history] == ["replaced", "replaced"]
    assert snap.history[0].anchor_a.price == Decimal("100.0")
    assert snap.history[1].anchor_a.price == Decimal("101.5")
