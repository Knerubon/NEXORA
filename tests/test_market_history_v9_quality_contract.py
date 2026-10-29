"""Wave V9 / task 7: pin BOTH `non_monotonic_count` semantics; change neither.

The extractor's definition (`QualityAccumulator.add`, adjacent descent): a tick whose time is lower
than the PREVIOUS tick's time counts once.
PR #95's oracle (`tests/_pr95_duplicate_oracle.py`, `out_of_order`): a tick whose time is lower than
the RUNNING MAXIMUM seen so far counts once.

They agree on the yes/no question that feeds `hard_defects` (a stream is non-monotonic under one iff
under the other) and on the common single-inversion case, but the NUMBERS differ whenever an early
high value is followed by several lower ones. Which number the contract wants is an open question
(escalated in THREAT-MODEL-V4.md section 10 and the PR body; owner: Rin / Quant-engineering).
Nothing here changes either definition; these tests only make the difference explicit so it cannot
be changed silently by one side.

Also pinned: duplicates cannot straddle a chunk boundary (task 6, stale residual #9).
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.market_history.extract import verify_dataset
from nexora.market_history.models import NormalizedTick, RawTick
from nexora.market_history.quality import QualityAccumulator, QualityConfig, hard_defects

from tests import _pr95_duplicate_oracle as oracle
from tests.test_market_history import FakeSource, mk
from tests.test_market_history_corrective import go


def _adjacent(times: list[int]) -> int:
    acc = QualityAccumulator(QualityConfig(10**9))
    for t in times:
        acc.add(NormalizedTick(t, t, Decimal("1.00"), Decimal("1.10"), Decimal("0.10")))
    return int(acc.report()["non_monotonic_count"])


def _running_max(times: list[int]) -> int:
    return oracle.classify(
        [{"time_msc": t, "bid": "1.00", "ask": "1.10", "flags": 0} for t in times]
    )["out_of_order"]


# (label, stream, adjacent-descent count, running-maximum count) derived by hand.
CASES = [
    ("sorted", [1000, 1500, 2000], 0, 0),
    ("equal timestamps are not out of order", [1000, 1000, 1000], 0, 0),
    ("single inversion (D9)", [1000, 1500, 2000, 1500, 2500], 1, 1),
    ("strictly descending", [3000, 2000, 1000], 2, 2),
    ("early high, then recovery (differs)", [1000, 3000, 2000, 2500], 1, 2),
    ("early high, two lower, recovery (differs)", [1000, 5000, 2000, 3000, 4000, 6000], 1, 3),
    ("descent then plateau", [3000, 1000, 1000, 1000], 1, 3),
]


@pytest.mark.parametrize(
    ("label", "times", "adjacent", "running"), CASES, ids=[c[0] for c in CASES]
)
def test_both_definitions_are_pinned_on_concrete_streams(
    label: str, times: list[int], adjacent: int, running: int
) -> None:
    assert _adjacent(times) == adjacent, label
    assert _running_max(times) == running, label


def test_the_definitions_differ_on_a_concrete_stream_and_that_is_known() -> None:
    times = [1000, 3000, 2000, 2500]
    assert (_adjacent(times), _running_max(times)) == (1, 2)


def test_yes_no_agreement_and_ordering_hold_for_every_short_stream() -> None:
    """Exhaustive: all 4^1..4^6 streams over four timestamps. The boolean that feeds
    `hard_defects` is identical; the adjacent count never exceeds the running-maximum count."""
    for n in range(1, 7):
        for times in itertools.product((1000, 1500, 2000, 2500), repeat=n):
            stream = list(times)
            adj, run = _adjacent(stream), _running_max(stream)
            assert (adj > 0) == (run > 0), stream
            assert adj <= run, stream


def test_hard_defect_follows_the_boolean_not_the_number() -> None:
    for times, expected in (([1000, 3000, 2000, 2500], True), ([1000, 2000, 3000], False)):
        acc = QualityAccumulator(QualityConfig(10**9))
        for t in times:
            acc.add(NormalizedTick(t, t, Decimal("1.00"), Decimal("1.10"), Decimal("0.10")))
        assert ("non_monotonic_count" in hard_defects(acc.report())) is expected


def test_through_the_real_pipeline_the_manifest_carries_the_adjacent_definition(
    tmp_path: Path,
) -> None:
    ticks = [mk(t) for t in (1000, 2900, 1500, 2000, 2800, 4000)]  # one chunk: [1000, 3000)
    m = go(tmp_path / "ds", FakeSource(ticks))
    assert m["quality"]["non_monotonic_count"] == 1  # adjacent; the oracle would say 3
    assert "non_monotonic_count" in m["quality_hard_defects"]
    assert verify_dataset(tmp_path / "ds") == []


# ------------------------------------------------ task 6: duplicates cannot straddle chunks ---
def test_a_duplicate_pair_can_never_be_split_across_a_chunk_boundary(tmp_path: Path) -> None:
    """Chunks partition RAW time ([from, to) windows) and a tick's identity contains its raw time,
    so two ticks with the same identity always fall in the same chunk. Residual #9 and the V4 risk
    row ("cross-chunk duplicates not counted") described a case that cannot occur."""
    boundary = 3000  # REQ 1000..6000 with chunk_ms 2000 -> boundaries at 3000 and 5000
    ticks = [mk(1000), mk(boundary - 1), mk(boundary - 1), mk(boundary), mk(boundary), mk(5500)]
    m = go(tmp_path / "ds", FakeSource(ticks))
    assert [c["file"] for c in m["chunks"]] == [
        "chunk-1000-3000.jsonl",
        "chunk-3000-5000.jsonl",
        "chunk-5000-6000.jsonl",
    ]
    assert m["quality"]["exact_duplicate_count"] == 2  # one pair on EACH side of the boundary
    assert (
        m["quality"]["exact_duplicate_count"]
        == oracle.classify(
            [
                {"time_msc": t.time_msc, "bid": str(t.bid), "ask": str(t.ask)}
                for t in ticks
                if isinstance(t, RawTick)
            ]
        )["exact_duplicate"]
    )
    assert verify_dataset(tmp_path / "ds") == []
