"""ENGINEERING tests (synthetic timestamps): chronological splits, holdout isolation,
walk-forward boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nexora.edge import (
    HoldoutLocked,
    HoldoutUnlock,
    SplitError,
    SplitPlan,
    select_segment,
    walk_forward_windows,
)

D0 = datetime(2026, 1, 1, tzinfo=UTC)


def day(n: int) -> datetime:
    return D0 + timedelta(days=n)


def plan(purge: timedelta = timedelta(0)) -> SplitPlan:
    return SplitPlan(day(0), day(60), day(80), day(100), purge)


def unlock(p: SplitPlan) -> HoldoutUnlock:
    return HoldoutUnlock(p.plan_hash, "test-authority", "engineering test")


TIMES = [day(n) for n in range(100)]
ITEMS = list(range(100))


def test_segments_are_disjoint_ordered_and_cover_the_range() -> None:
    p = plan()
    train = select_segment(p, ITEMS, TIMES, "train")
    val = select_segment(p, ITEMS, TIMES, "validation")
    hold = select_segment(p, ITEMS, TIMES, "holdout", unlock=unlock(p))
    assert (train[0], train[-1]) == (0, 59)
    assert (val[0], val[-1]) == (60, 79)
    assert (hold[0], hold[-1]) == (80, 99)
    assert not set(train) & set(val) and not set(val) & set(hold)
    assert sorted((*train, *val, *hold)) == ITEMS


def test_boundaries_are_half_open() -> None:
    p = plan()
    assert 60 not in select_segment(p, ITEMS, TIMES, "train")
    assert 60 in select_segment(p, ITEMS, TIMES, "validation")


def test_purge_gap_items_belong_to_no_segment() -> None:
    p = plan(purge=timedelta(days=5))
    train = select_segment(p, ITEMS, TIMES, "train")
    val = select_segment(p, ITEMS, TIMES, "validation")
    hold = select_segment(p, ITEMS, TIMES, "holdout", unlock=unlock(p))
    used = {*train, *val, *hold}
    assert {60, 61, 62, 63, 64}.isdisjoint(used)
    assert {80, 81, 82, 83, 84}.isdisjoint(used)
    assert val[0] == 65 and hold[0] == 85


def test_holdout_is_locked_without_unlock() -> None:
    with pytest.raises(HoldoutLocked):
        select_segment(plan(), ITEMS, TIMES, "holdout")


def test_unlock_for_a_different_plan_is_rejected() -> None:
    other = SplitPlan(day(0), day(50), day(80), day(100))
    with pytest.raises(HoldoutLocked):
        select_segment(plan(), ITEMS, TIMES, "holdout", unlock=unlock(other))


@pytest.mark.parametrize("who,why", [("", "r"), ("  ", "r"), ("a", ""), ("a", " ")])
def test_unlock_requires_authority_and_reason(who: str, why: str) -> None:
    p = plan()
    with pytest.raises(HoldoutLocked):
        select_segment(p, ITEMS, TIMES, "holdout", unlock=HoldoutUnlock(p.plan_hash, who, why))


def test_train_and_validation_never_depend_on_holdout_content() -> None:
    p = plan()
    changed = [(-1 if t >= day(80) else i) for i, t in zip(ITEMS, TIMES, strict=True)]
    assert select_segment(p, ITEMS, TIMES, "train") == select_segment(p, changed, TIMES, "train")
    assert select_segment(p, ITEMS, TIMES, "validation") == select_segment(
        p, changed, TIMES, "validation"
    )


def test_selection_is_chronological_not_order_dependent() -> None:
    p = plan()
    shuffled = list(reversed(ITEMS))
    out = select_segment(p, shuffled, [TIMES[i] for i in shuffled], "train")
    assert set(out) == set(range(60))


def test_plan_hash_is_stable_and_changes_with_boundaries() -> None:
    assert plan().plan_hash == plan().plan_hash
    assert plan().plan_hash != SplitPlan(day(0), day(61), day(80), day(100)).plan_hash


@pytest.mark.parametrize(
    "args",
    [
        (day(0), day(0), day(80), day(100)),
        (day(0), day(60), day(60), day(100)),
        (day(0), day(60), day(80), day(80)),
        (day(0), day(60), day(80), day(70)),
    ],
)
def test_non_increasing_boundaries_rejected(
    args: tuple[datetime, datetime, datetime, datetime],
) -> None:
    with pytest.raises(SplitError):
        SplitPlan(*args)


def test_purge_that_swallows_a_segment_rejected() -> None:
    with pytest.raises(SplitError):
        SplitPlan(day(0), day(60), day(80), day(100), purge=timedelta(days=25))


def test_naive_datetime_rejected() -> None:
    with pytest.raises(SplitError):
        SplitPlan(datetime(2026, 1, 1), day(60), day(80), day(100))
    with pytest.raises(SplitError):
        select_segment(plan(), [1], [datetime(2026, 1, 2)], "train")


def test_length_mismatch_rejected() -> None:
    with pytest.raises(SplitError):
        select_segment(plan(), [1, 2], [day(1)], "train")


# ---- walk-forward ---------------------------------------------------------------------


def test_walk_forward_windows_are_chronological_and_test_after_train() -> None:
    ws = walk_forward_windows(
        day(0), day(80), train=timedelta(days=20), test=timedelta(days=10), purge=timedelta(days=2)
    )
    assert [w.index for w in ws] == list(range(len(ws)))
    for w in ws:
        assert w.train_start < w.train_end
        assert w.test_start == w.train_end + timedelta(days=2)
        assert w.test_start < w.test_end
    for a, b in zip(ws, ws[1:], strict=False):
        assert b.test_start >= a.test_end  # no overlapping test windows
        assert b.test_start > a.test_start


def test_walk_forward_never_reaches_the_holdout() -> None:
    p = plan()
    ws = walk_forward_windows(
        p.start, p.validation_end, train=timedelta(days=30), test=timedelta(days=10)
    )
    assert max(w.test_end for w in ws) <= p.validation_end
    hold_low, _ = p.bounds("holdout")
    assert all(w.test_end <= hold_low for w in ws)


def test_anchored_windows_share_the_start() -> None:
    ws = walk_forward_windows(
        day(0), day(80), train=timedelta(days=20), test=timedelta(days=10), anchored=True
    )
    assert {w.train_start for w in ws} == {day(0)}
    assert ws[-1].train_end > ws[0].train_end


def test_rolling_windows_keep_a_fixed_train_length() -> None:
    ws = walk_forward_windows(day(0), day(80), train=timedelta(days=20), test=timedelta(days=10))
    assert {w.train_end - w.train_start for w in ws} == {timedelta(days=20)}


def test_walk_forward_rejects_overlapping_test_step_and_short_range() -> None:
    with pytest.raises(SplitError):
        walk_forward_windows(
            day(0),
            day(80),
            train=timedelta(days=20),
            test=timedelta(days=10),
            step=timedelta(days=5),
        )
    with pytest.raises(SplitError):
        walk_forward_windows(day(0), day(10), train=timedelta(days=20), test=timedelta(days=10))
    with pytest.raises(SplitError):
        walk_forward_windows(day(0), day(80), train=timedelta(0), test=timedelta(days=10))


def test_walk_forward_is_deterministic() -> None:
    train, test = timedelta(days=20), timedelta(days=10)
    assert walk_forward_windows(day(0), day(80), train=train, test=test) == walk_forward_windows(
        day(0), day(80), train=train, test=test
    )
