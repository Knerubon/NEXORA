"""MATRIX CASE 4 - DST fold / ambiguous-window rejection and window splitting."""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness.reference_model import (
    ReadinessError,
    check_window,
    derived_fold_ranges,
    split_around_folds,
)
from tests.windows_integration_readiness.vectors import (
    EXPECTED_FOLD_FALLBACK,
    SEGMENTS_FALLBACK,
    SEGMENTS_SINGLE,
    SEGMENTS_SPRING,
    T0,
    H,
)


def test_case4_fold_derived_from_falling_offset_both_sides() -> None:
    assert derived_fold_ranges(SEGMENTS_FALLBACK) == EXPECTED_FOLD_FALLBACK


def test_case4_rising_offset_and_single_segment_derive_no_fold() -> None:
    assert derived_fold_ranges(SEGMENTS_SPRING) == ()
    assert derived_fold_ranges(SEGMENTS_SINGLE) == ()


@pytest.mark.parametrize(
    ("lo", "hi"),
    [
        (T0, T0 + 100 * H),  # ends inside the repeated stretch
        (T0 + 100 * H, T0 + 200 * H),  # starts inside it
        (T0 + 99 * H, T0 + 101 * H),  # exactly the fold
        (T0, T0 + 300 * H),  # spans it
    ],
)
def test_case4_windows_touching_fold_are_refused(lo: int, hi: int) -> None:
    with pytest.raises(ReadinessError) as e:
        check_window(SEGMENTS_FALLBACK, lo, hi)
    assert e.value.code == "window_touches_fold_range"


def test_case4_windows_clear_of_fold_are_accepted() -> None:
    check_window(SEGMENTS_FALLBACK, T0, T0 + 99 * H)
    check_window(SEGMENTS_FALLBACK, T0 + 101 * H, T0 + 200 * H)


def test_case4_attested_ambiguous_range_is_also_enforced() -> None:
    with pytest.raises(ReadinessError):
        check_window(
            SEGMENTS_SINGLE, T0, T0 + 10 * H, attested_ambiguous=((T0 + 5 * H, T0 + 6 * H),)
        )


def test_case4_empty_attested_list_does_not_waive_derived_fold() -> None:
    with pytest.raises(ReadinessError):
        check_window(SEGMENTS_FALLBACK, T0, T0 + 300 * H, attested_ambiguous=())


def test_case4_split_excludes_repeated_stretch() -> None:
    parts = split_around_folds(SEGMENTS_FALLBACK, T0, T0 + 300 * H)
    assert parts == ((T0, T0 + 99 * H), (T0 + 101 * H, T0 + 300 * H))
    for lo, hi in parts:
        check_window(SEGMENTS_FALLBACK, lo, hi)


def test_case4_split_is_identity_without_fold() -> None:
    assert split_around_folds(SEGMENTS_SINGLE, T0, T0 + 5 * H) == ((T0, T0 + 5 * H),)


def test_case4_nothing_detects_a_fold_from_ordered_ticks() -> None:
    """An ascending tick list for a repeated hour looks perfectly ordered: ordering checks cannot
    stand in for the evidence-based fold rules above."""
    ticks = [T0 + i for i in range(0, 10)]
    assert ticks == sorted(ticks)
