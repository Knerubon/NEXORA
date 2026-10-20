"""MATRIX CASE 5 - chunk boundaries, duplicates, ordering, deterministic hashes."""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness.reference_model import (
    ReadinessError,
    build_chunks,
    chunk_windows,
    content_sha256,
    order_report,
)
from tests.windows_integration_readiness.vectors import T0, TICKS_SYNTH, H

LO, HI = T0, T0 + 3 * H


def test_case5_windows_are_half_open_contiguous_and_cover_range() -> None:
    w = chunk_windows(LO, HI, H)
    assert w[0][0] == LO and w[-1][1] == HI
    assert all(a[1] == b[0] for a, b in zip(w, w[1:], strict=False))


def test_case5_non_divisible_chunk_has_short_tail() -> None:
    assert chunk_windows(0, 10, 4) == [(0, 4), (4, 8), (8, 10)]


@pytest.mark.parametrize(("lo", "hi", "ms"), [(5, 5, 1), (5, 4, 1), (0, 5, 0), (0, 5, -1)])
def test_case5_invalid_chunking_refused(lo: int, hi: int, ms: int) -> None:
    with pytest.raises(ReadinessError):
        chunk_windows(lo, hi, ms)


def test_case5_boundary_tick_lands_in_exactly_one_chunk() -> None:
    bodies = build_chunks(TICKS_SYNTH, LO, HI, H)
    boundary = f'"t":{T0 + H}}}'.encode()
    assert [boundary in b for b in bodies] == [False, True, False]


def test_case5_out_of_window_ticks_returned_by_source_are_dropped() -> None:
    plain = build_chunks(TICKS_SYNTH, LO, HI, H)
    sloppy = build_chunks(TICKS_SYNTH, LO, HI, H, fetch_extra=1000)
    assert plain == sloppy


def test_case5_same_input_same_bytes_same_hash_across_runs() -> None:
    a = build_chunks(TICKS_SYNTH, LO, HI, H)
    b = build_chunks(TICKS_SYNTH, LO, HI, H)
    assert a == b and content_sha256(a) == content_sha256(b)


def test_case5_content_hash_is_chunking_invariant_when_stream_identical() -> None:
    h1 = content_sha256(build_chunks(TICKS_SYNTH, LO, HI, H))
    h2 = content_sha256(build_chunks(TICKS_SYNTH, LO, HI, 30 * 60 * 1000))
    h3 = content_sha256(build_chunks(TICKS_SYNTH, LO, HI, 3 * H))
    assert h1 == h2 == h3


def test_case5_per_chunk_bytes_differ_with_chunking_so_chunk_hashes_are_not_identity() -> None:
    assert build_chunks(TICKS_SYNTH, LO, HI, H) != build_chunks(TICKS_SYNTH, LO, HI, 3 * H)


def test_case5_any_change_changes_hash() -> None:
    base = content_sha256(build_chunks(TICKS_SYNTH, LO, HI, H))
    mutated = list(TICKS_SYNTH)
    mutated[0] = (mutated[0][0], "1.1", "1.3", mutated[0][3])
    assert content_sha256(build_chunks(mutated, LO, HI, H)) != base
    assert content_sha256(build_chunks(TICKS_SYNTH[:-1], LO, HI, H)) != base


def test_case5_duplicates_are_kept_and_reported_not_dropped() -> None:
    bodies = build_chunks(TICKS_SYNTH, LO, HI, H)
    assert sum(b.count(b"\n") for b in bodies) == len(TICKS_SYNTH)
    assert order_report(TICKS_SYNTH) == {
        "non_monotonic": 0,
        "exact_duplicate": 1,
        "same_ts_diff_quote": 1,
    }


def test_case5_disorder_is_reported_and_input_order_preserved() -> None:
    shuffled = [TICKS_SYNTH[1], TICKS_SYNTH[0], *TICKS_SYNTH[2:]]
    assert order_report(shuffled)["non_monotonic"] >= 1
    first = build_chunks(shuffled, LO, HI, H)[0].splitlines()[0]
    assert f'"t":{TICKS_SYNTH[1][0]}}}'.encode() in first
