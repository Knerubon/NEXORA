"""MATRIX CASE 3 - raw broker time -> UTC only through an independently documented rule."""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness.reference_model import (
    MAX_ABS_OFFSET_S,
    ReadinessError,
    check_time_fields,
    to_utc_msc,
    validate_segments,
)
from tests.windows_integration_readiness.vectors import (
    SEGMENTS_FALLBACK,
    SEGMENTS_SINGLE,
    T0,
    H,
)


def test_case3_offset_applied_only_with_declared_segment() -> None:
    assert to_utc_msc(T0 + 5, SEGMENTS_SINGLE) == T0 + 5 - 7_200_000


def test_case3_no_default_offset_outside_declared_segments() -> None:
    with pytest.raises(ReadinessError) as e:
        to_utc_msc(T0 - 1, SEGMENTS_SINGLE)
    assert e.value.code == "no_declared_offset_for_time"


def test_case3_conversion_is_required() -> None:
    with pytest.raises(ReadinessError) as e:
        to_utc_msc(T0, ())
    assert e.value.code == "time_conversion_required"


@pytest.mark.parametrize("evidence", ["", "   "])
def test_case3_evidence_ref_is_mandatory(evidence: str) -> None:
    with pytest.raises(ReadinessError) as e:
        validate_segments(((T0, None, 7200, evidence),))
    assert e.value.code == "offset_evidence_required"


def test_case3_offset_is_not_inferable_from_ticks() -> None:
    """Two different offsets are equally consistent with identical raw ticks: the data cannot
    select one. The harness therefore has no inference function to call."""
    raw = [T0 + i * 1000 for i in range(5)]
    a = [to_utc_msc(r, ((T0, None, 7200, "SYN-A"),)) for r in raw]
    b = [to_utc_msc(r, ((T0, None, 10800, "SYN-B"),)) for r in raw]
    assert a != b
    assert [x - y for x, y in zip(a, b, strict=True)] == [3_600_000] * 5


def test_case3_segment_boundaries_are_half_open() -> None:
    edge = T0 + 100 * H
    assert to_utc_msc(edge - 1, SEGMENTS_FALLBACK) == edge - 1 - 10_800_000
    assert to_utc_msc(edge, SEGMENTS_FALLBACK) == edge - 7_200_000


def test_case3_overlapping_segments_refused() -> None:
    with pytest.raises(ReadinessError) as e:
        validate_segments(((T0, T0 + 10, 7200, "E"), (T0 + 5, None, 7200, "E")))
    assert e.value.code == "segments_overlap"


def test_case3_unsorted_segments_refused_with_extractor_code() -> None:
    with pytest.raises(ReadinessError) as e:
        validate_segments(((T0 + 20, None, 7200, "E"), (T0, T0 + 10, 7200, "E")))
    assert e.value.code == "segments_not_sorted"


@pytest.mark.parametrize("offset", [MAX_ABS_OFFSET_S + 1, -(MAX_ABS_OFFSET_S + 1), 10**9])
def test_case3_offset_beyond_a_day_refused(offset: int) -> None:
    """F9: 1e9 s used to be accepted (the extractor still accepts it: EXT-DEF-2)."""
    with pytest.raises(ReadinessError) as e:
        validate_segments(((T0, None, offset, "E"),))
    assert e.value.code == "offset_out_of_range"


def test_case3_time_and_time_msc_must_agree() -> None:
    """F9: a row whose `time` contradicts `time_msc` is refused, never silently preferred."""
    check_time_fields(1_700_000_000, 1_700_000_000_999)
    for bad in ((1_700_000_001, 1_700_000_000_999), (1_699_999_999, 1_700_000_000_000)):
        with pytest.raises(ReadinessError) as e:
            check_time_fields(*bad)
        assert e.value.code == "time_fields_conflict"


def test_case3_gap_between_segments_refused_on_lookup() -> None:
    segs = ((T0, T0 + 10, 7200, "E"), (T0 + 20, None, 7200, "E"))
    with pytest.raises(ReadinessError):
        to_utc_msc(T0 + 15, segs)
