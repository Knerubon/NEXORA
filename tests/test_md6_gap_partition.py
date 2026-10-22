"""Adversarial tests for the MD-6 gap interval partition (finding M1, C2/C9; finding m3).

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

Every timestamp, calendar, offset table and threshold here is SYNTHETIC. Threshold values passed
to ``judge_gap`` are SYNTHETIC test inputs, not proposals; the MD-6 thresholds stay OPEN.
"""

from __future__ import annotations

import inspect
import itertools
from collections.abc import Callable

import pytest

from tests.md6_gap_partition_reference import (
    ArtifactIdentity,
    CalendarEntry,
    CalendarVersion,
    DefectKind,
    DisconnectEvidence,
    EntryKind,
    GapClass,
    Label,
    OffsetSegment,
    OffsetTransitionTable,
    OpenMeasure,
    Partition,
    PartitionStatus,
    Reason,
    canonical_sha256,
    judge_gap,
    old_rule_classify_gap,
    partition_gap,
    partition_span,
    window_coverage,
)

M = 60_000
H = 60 * M
D = 24 * H

# SYNTHETIC thresholds (test inputs only; MD-6 values are OPEN).
SYNTHETIC_THRESHOLD_6_MIN = 6 * M
SYNTHETIC_THRESHOLD_1_MIN = 1 * M


def ident(name: str, version: str = "v1") -> ArtifactIdentity:
    return ArtifactIdentity(f"SYNTHETIC-{name}", f"sha256-of-{name}-{version}", version)


def utc_calendar(
    coverage: tuple[int, int],
    sessions: tuple[tuple[int, int], ...],
    exceptions: tuple[tuple[int, int, EntryKind], ...] = (),
    *,
    name: str = "cal",
    version: str = "v1",
) -> CalendarVersion:
    entries = tuple(CalendarEntry(a, b, "SESSION_OPEN") for a, b in sessions) + tuple(
        CalendarEntry(a, b, k) for a, b, k in exceptions
    )
    return CalendarVersion(ident(name, version), "UTC", coverage[0], coverage[1], entries)


def weekly_calendar(
    exceptions: tuple[tuple[int, int, EntryKind], ...] = (), version: str = "v1"
) -> CalendarVersion:
    """SYNTHETIC: day 0 is a Monday. Mon-Fri sessions [00:00, 22:00) UTC; weekend closed."""
    days = [d for d in range(14) if d % 7 < 5]
    return utc_calendar(
        (0, 14 * D),
        tuple((d * D, d * D + 22 * H) for d in days),
        exceptions,
        name="weekly",
        version=version,
    )


def gap(prev: int, nxt: int, cals: tuple[CalendarVersion, ...]) -> Partition:
    return partition_gap(prev, nxt, calendars=cals, tick_utc_verified=True)


def shape(p: Partition) -> list[tuple[int, int, Label, Reason]]:
    return [(s.start_ms, s.end_ms, s.label, s.reason) for s in p.segments]


def assert_invariants(p: Partition) -> None:
    if p.status in (PartitionStatus.NO_GAP, PartitionStatus.REFUSED_NON_MONOTONIC):
        assert p.segments == ()
        return
    assert sum(s.duration_ms for s in p.segments) == p.end_ms - p.start_ms
    assert p.open_ms + p.closed_ms + p.unknown_ms == p.end_ms - p.start_ms
    assert p.segments[0].start_ms == p.start_ms
    assert p.segments[-1].end_ms == p.end_ms
    for a, b in itertools.pairwise(p.segments):
        assert a.end_ms == b.start_ms
        assert (a.label, a.reason, a.calendar, a.tz_rule) != (
            b.label,
            b.reason,
            b.calendar,
            b.tz_rule,
        )
    assert all(s.duration_ms > 0 for s in p.segments)
    assert (p.status is PartitionStatus.UNVERIFIED) == (p.unknown_ms > 0)
    assert p.longest_open_run_ms <= p.open_ms


# ---------------------------------------------------------------------------
# A1 to A5: open, closed, close/reopen, weekend, multiple closures
# ---------------------------------------------------------------------------


def test_a1_gap_entirely_within_open_session() -> None:
    cal = weekly_calendar()
    p = gap(1 * D + 3 * H, 1 * D + 3 * H + 17 * M, (cal,))
    assert shape(p) == [
        (1 * D + 3 * H, 1 * D + 3 * H + 17 * M, Label.VERIFIED_OPEN, Reason.SESSION_OPEN)
    ]
    assert p.status is PartitionStatus.VERIFIED
    assert p.open_ms == p.longest_open_run_ms == 17 * M
    assert p.calendars == (cal.identity,)
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.UNEXPLAINED
    assert_invariants(p)


def test_a2_span_entirely_within_verified_closure_is_expected_closed() -> None:
    # A span with no tick inside the closure: the leading edge of a window that starts on Saturday.
    cal = weekly_calendar()
    sat, mon = 5 * D, 7 * D
    p = partition_span(
        sat + H, mon, calendars=(cal,), tick_utc_verified=True, observed_ticks_ms=(mon,)
    )
    assert shape(p) == [(sat + H, mon, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS)]
    assert p.open_ms == 0
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.EXPECTED_CLOSED
    assert_invariants(p)


def test_a2_inter_tick_gap_inside_closure_is_calendar_data_contradiction() -> None:
    # A tick observed inside VERIFIED_CLOSED contradicts the calendar: the run becomes UNKNOWN.
    cal = weekly_calendar()
    p = gap(5 * D + H, 5 * D + 2 * H, (cal,))
    assert shape(p) == [
        (5 * D + H, 5 * D + 2 * H, Label.UNKNOWN, Reason.TICK_INSIDE_VERIFIED_CLOSED)
    ]
    assert p.status is PartitionStatus.UNVERIFIED
    kinds = [d.kind for d in p.defects]
    assert kinds.count(DefectKind.CALENDAR_DATA_CONTRADICTION) == 2  # both ticks flagged
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.UNVERIFIED
    assert_invariants(p)


def test_a3_gap_crossing_a_close_and_reopen() -> None:
    cal = weekly_calendar()
    prev, nxt = 1 * D + 21 * H + 50 * M, 2 * D + 20 * M
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [
        (prev, 1 * D + 22 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (1 * D + 22 * H, 2 * D, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (2 * D, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.open_ms == 30 * M
    assert p.longest_open_run_ms == 20 * M
    assert p.closed_ms == 2 * H
    assert_invariants(p)


def _a4_calendar() -> CalendarVersion:
    # SYNTHETIC: verified closed interval [100h, 148h), open on both sides.
    return utc_calendar((0, 300 * H), ((0, 100 * H), (148 * H, 300 * H)), name="a4")


def test_a4_weekend_straddle_counts_only_seven_open_minutes() -> None:
    cal = _a4_calendar()
    prev, nxt = 100 * H - 2 * M, 148 * H + 5 * M
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [
        (prev, 100 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (100 * H, 148 * H, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (148 * H, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.open_ms == 420_000
    assert p.longest_open_run_ms == 300_000
    assert p.closed_ms == 48 * H
    assert p.unknown_ms == 0
    assert p.status is PartitionStatus.VERIFIED
    assert_invariants(p)
    # The two measures can disagree; the choice is an OPEN Quant decision.
    total = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    longest = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.LONGEST_CONTIGUOUS_VERIFIED_OPEN,
        disconnects=None,
    )
    assert (total.measured_open_ms, total.gap_class) == (420_000, GapClass.UNEXPLAINED)
    assert (longest.measured_open_ms, longest.gap_class) == (300_000, GapClass.WITHIN_THRESHOLD)


def test_a4_old_rule_classifies_full_span_unexplained() -> None:
    prev, nxt = 100 * H - 2 * M, 148 * H + 5 * M
    cls, counted = old_rule_classify_gap(
        prev,
        nxt,
        closed_intervals=((100 * H, 148 * H),),
        disconnect_intervals=(),
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
    )
    assert cls == "unexplained"
    assert counted == 48 * H + 7 * M  # 48.12 h counted, although only 7 open minutes elapsed
    assert round(counted / H, 2) == 48.12


def test_a4_window_unexplained_coverage_uses_verified_open_denominator() -> None:
    cal = _a4_calendar()
    w_from, w_to = 100 * H - 3 * M, 148 * H + 6 * M
    ticks = (w_from, 100 * H - 2 * M, 148 * H + 5 * M, w_to - 1)
    cov = window_coverage(
        w_from,
        w_to,
        ticks,
        calendars=(cal,),
        tick_utc_verified=True,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        edge_threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert cov.status == "VERIFIED"
    assert cov.verified_open_ms == 9 * M  # A-06 / D12 denominator: open time only
    assert cov.unexplained_open_ms == 7 * M  # A-06 numerator
    assert cov.covered_open_ms == 2 * M
    assert cov.unknown_ms == 0
    # OLD A-06: full gap duration over full window duration.
    old_num = 48 * H + 7 * M
    old_den = w_to - w_from
    assert (old_num, old_den) == (173_220_000, 173_340_000)
    assert (cov.unexplained_open_ms, cov.verified_open_ms) != (old_num, old_den)


def test_a5_gap_spanning_daily_breaks_and_weekend() -> None:
    cal = weekly_calendar()
    prev = 3 * D + 22 * H - 2 * M  # Thursday 21:58
    nxt = 7 * D + 5 * M  # next Monday 00:05
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [
        (prev, 3 * D + 22 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (3 * D + 22 * H, 4 * D, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (4 * D, 4 * D + 22 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (4 * D + 22 * H, 7 * D, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (7 * D, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.open_ms == 22 * H + 7 * M
    assert p.longest_open_run_ms == 22 * H
    assert p.closed_ms == 52 * H
    assert_invariants(p)


# ---------------------------------------------------------------------------
# A6: holiday and exceptional closure, versioned calendar
# ---------------------------------------------------------------------------


def test_a6_holiday_and_exceptional_closure_from_versioned_calendar() -> None:
    v2 = weekly_calendar(
        exceptions=(
            (2 * D, 3 * D, "EXCEPTION_CLOSED"),  # Wednesday holiday
            (3 * D + 12 * H, 3 * D + 22 * H, "EXCEPTION_CLOSED"),  # Thursday early close
        ),
        version="v2",
    )
    prev, nxt = 1 * D + 22 * H - 2 * M, 3 * D + 5 * M
    p = gap(prev, nxt, (v2,))
    assert shape(p) == [
        (prev, 1 * D + 22 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (1 * D + 22 * H, 2 * D, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (2 * D, 3 * D, Label.VERIFIED_CLOSED, Reason.EXCEPTION_CLOSED),
        (3 * D, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.open_ms == 7 * M
    assert all(s.calendar == v2.identity for s in p.segments)
    assert p.calendars == (v2.identity,)
    early = gap(3 * D + 12 * H - M, 4 * D + M, (v2,))
    assert early.open_ms == 2 * M
    assert early.closed_ms == 12 * H
    assert_invariants(p)
    assert_invariants(early)


def test_a6_exception_missing_from_calendar_version_counts_as_open() -> None:
    # Limitation: the calendar is the authority. A holiday absent from v1 is VERIFIED_OPEN, so the
    # gap is flagged (never silently excused). The identity recorded shows which version said so.
    v1 = weekly_calendar(version="v1")
    prev, nxt = 1 * D + 22 * H - 2 * M, 3 * D + 5 * M
    p = gap(prev, nxt, (v1,))
    assert p.open_ms == 22 * H + 7 * M
    assert p.calendars == (v1.identity,)
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.UNEXPLAINED


def test_a6_exception_open_special_session_on_weekend() -> None:
    cal = weekly_calendar(exceptions=((5 * D + 10 * H, 5 * D + 12 * H, "EXCEPTION_OPEN"),))
    p = gap(5 * D + 10 * H + M, 5 * D + 11 * H, (cal,))
    assert shape(p) == [
        (5 * D + 10 * H + M, 5 * D + 11 * H, Label.VERIFIED_OPEN, Reason.EXCEPTION_OPEN)
    ]


# ---------------------------------------------------------------------------
# A7: DST and offsets via an explicit SYNTHETIC transition table (no system tzdata)
# ---------------------------------------------------------------------------

T_SPRING = 2 * D + 1 * H  # SYNTHETIC: offset +2h -> +3h (local 03:00-04:00 on day 2 skipped)
T_FALL = 5 * D + 1 * H  # SYNTHETIC: offset +3h -> +2h (local 03:00-04:00 on day 5 repeated)


def synthetic_tz(evidence_ref: str | None = "SYNTHETIC-evidence-ref") -> OffsetTransitionTable:
    return OffsetTransitionTable(
        identity=ident("tz-table"),
        evidence_ref=evidence_ref,
        segments=(
            OffsetSegment(0, 2 * H),
            OffsetSegment(T_SPRING, 3 * H),
            OffsetSegment(T_FALL, 2 * H),
        ),
        utc_coverage_end_ms=12 * D,
    )


def local_calendar(
    sessions: tuple[tuple[int, int], ...], tz: OffsetTransitionTable | None
) -> CalendarVersion:
    return CalendarVersion(
        ident("local-cal"),
        "VENUE_LOCAL",
        D,
        10 * D,
        tuple(CalendarEntry(a, b, "SESSION_OPEN") for a, b in sessions),
        tz,
    )


def test_a7_dst_session_converted_with_evidenced_rule() -> None:
    tz = synthetic_tz()
    cal = local_calendar(tuple((d * D + 9 * H, d * D + 17 * H) for d in range(1, 10)), tz)
    # Day 1 local 09:00-17:00 at +2h -> UTC 07:00-15:00. Day 2 and 3 at +3h -> UTC 06:00-14:00.
    prev, nxt = 1 * D + 15 * H - 2 * M, 3 * D + 6 * H + 5 * M
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [
        (prev, 1 * D + 15 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (1 * D + 15 * H, 2 * D + 6 * H, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (2 * D + 6 * H, 2 * D + 14 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (2 * D + 14 * H, 3 * D + 6 * H, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (3 * D + 6 * H, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.open_ms == 8 * H + 7 * M
    assert p.tz_rules == (tz.identity,)
    assert all(s.tz_rule == tz.identity for s in p.segments)
    assert_invariants(p)


def test_a7_ambiguous_local_boundary_is_unknown_not_guessed() -> None:
    tz = synthetic_tz()
    # Day 4 at +3h: local 09-17 -> UTC 06-14. Day 5 session starts at local 03:30, which occurs
    # twice (fall-back): UTC 00:30 (+3h) or 01:30 (+2h). Ends local 17:00 at +2h -> UTC 15:00.
    cal = local_calendar(
        ((4 * D + 9 * H, 4 * D + 17 * H), (5 * D + 3 * H + 30 * M, 5 * D + 17 * H)), tz
    )
    prev, nxt = 4 * D + 14 * H - M, 5 * D + 2 * H
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [
        (prev, 4 * D + 14 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (4 * D + 14 * H, 5 * D + 30 * M, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (5 * D + 30 * M, 5 * D + H + 30 * M, Label.UNKNOWN, Reason.TZ_AMBIGUOUS_OR_NONEXISTENT),
        (5 * D + H + 30 * M, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.status is PartitionStatus.UNVERIFIED
    assert p.unknown_ms == H
    assert DefectKind.TZ_BOUNDARY_UNRESOLVED in {d.kind for d in p.defects}
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.UNVERIFIED
    assert_invariants(p)


def test_a7_nonexistent_local_boundary_is_unknown_not_guessed() -> None:
    tz = synthetic_tz()
    # Session local [day2 00:00, day2 03:30): 03:30 does not exist (spring-forward at UTC 01:00).
    cal = local_calendar(((2 * D, 2 * D + 3 * H + 30 * M), (2 * D + 9 * H, 2 * D + 17 * H)), tz)
    prev, nxt = 2 * D, 2 * D + 6 * H + M
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [
        (prev, 2 * D + 30 * M, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (2 * D + 30 * M, 2 * D + H + 30 * M, Label.UNKNOWN, Reason.TZ_AMBIGUOUS_OR_NONEXISTENT),
        (2 * D + H + 30 * M, 2 * D + 6 * H, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (2 * D + 6 * H, nxt, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.status is PartitionStatus.UNVERIFIED
    assert_invariants(p)


@pytest.mark.parametrize(
    "tz",
    [
        pytest.param(None, id="no-rule"),
        pytest.param(synthetic_tz(evidence_ref=None), id="rule-without-evidence-ref"),
        pytest.param(
            OffsetTransitionTable(
                ident("short-tz"), "SYNTHETIC-ref", (OffsetSegment(0, 2 * H),), D
            ),
            id="rule-not-covering-calendar",
        ),
    ],
)
def test_a7_missing_offset_evidence_makes_whole_partition_unverified(
    tz: OffsetTransitionTable | None,
) -> None:
    cal = local_calendar(tuple((d * D + 9 * H, d * D + 17 * H) for d in range(1, 10)), tz)
    prev, nxt = 1 * D + 10 * H, 3 * D
    p = gap(prev, nxt, (cal,))
    assert shape(p) == [(prev, nxt, Label.UNKNOWN, Reason.TZ_EVIDENCE_MISSING)]
    assert p.open_ms == 0 and p.closed_ms == 0
    assert p.status is PartitionStatus.UNVERIFIED
    assert DefectKind.TZ_EVIDENCE_MISSING in {d.kind for d in p.defects}


def test_a7_unverified_tick_utc_conversion_makes_partition_unknown() -> None:
    p = partition_gap(D, 2 * D, calendars=(weekly_calendar(),), tick_utc_verified=False)
    assert shape(p) == [(D, 2 * D, Label.UNKNOWN, Reason.TICK_UTC_UNVERIFIED)]


# ---------------------------------------------------------------------------
# A8: missing, partial and contradictory calendar
# ---------------------------------------------------------------------------


def test_a8_missing_calendar_is_all_unknown() -> None:
    p = gap(D, 3 * D, ())
    assert shape(p) == [(D, 3 * D, Label.UNKNOWN, Reason.NO_CALENDAR_COVERAGE)]
    assert p.status is PartitionStatus.UNVERIFIED
    assert p.calendars == ()


def test_a8_partial_calendar_tail_is_unknown() -> None:
    cal = utc_calendar((0, 10 * H), ((0, 10 * H),), name="partial")
    p = gap(8 * H, 12 * H, (cal,))
    assert shape(p) == [
        (8 * H, 10 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (10 * H, 12 * H, Label.UNKNOWN, Reason.NO_CALENDAR_COVERAGE),
    ]
    assert p.open_ms == 2 * H and p.unknown_ms == 2 * H
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.UNVERIFIED


def test_a8_contradictory_exceptions_in_one_version_are_unknown() -> None:
    cal = weekly_calendar(
        exceptions=(
            (D + 2 * H, D + 4 * H, "EXCEPTION_CLOSED"),
            (D + 3 * H, D + 5 * H, "EXCEPTION_OPEN"),
        )
    )
    p = gap(D + H, D + 6 * H, (cal,))
    assert shape(p) == [
        (D + H, D + 2 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (D + 2 * H, D + 3 * H, Label.VERIFIED_CLOSED, Reason.EXCEPTION_CLOSED),
        (D + 3 * H, D + 4 * H, Label.UNKNOWN, Reason.CONFLICTING_EVIDENCE),
        (D + 4 * H, D + 5 * H, Label.VERIFIED_OPEN, Reason.EXCEPTION_OPEN),
        (D + 5 * H, D + 6 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
    ]
    assert p.status is PartitionStatus.UNVERIFIED


def test_a8_two_versions_claiming_authority_are_unknown() -> None:
    v1 = utc_calendar((0, 2 * D), ((0, 22 * H),), name="dual", version="v1")
    v2 = utc_calendar((D, 3 * D), ((D, D + 22 * H),), name="dual", version="v2")
    p = gap(20 * H, D + 2 * H, (v1, v2))
    assert shape(p) == [
        (20 * H, 22 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (22 * H, D, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
        (D, D + 2 * H, Label.UNKNOWN, Reason.CALENDAR_AUTHORITY_CONFLICT),
    ]
    assert p.status is PartitionStatus.UNVERIFIED


def test_a8_versions_tiling_by_effective_date_record_each_identity() -> None:
    v1 = utc_calendar((0, D), ((0, 22 * H),), name="tile", version="v1")
    v2 = utc_calendar((D, 2 * D), ((D, D + 22 * H),), name="tile", version="v2")
    p = gap(21 * H, D + H, (v1, v2))
    assert [s.calendar for s in p.segments] == [v1.identity, v1.identity, v2.identity]
    assert p.calendars == (v1.identity, v2.identity)
    assert p.status is PartitionStatus.VERIFIED


# ---------------------------------------------------------------------------
# A9: exact boundaries and zero-duration intervals
# ---------------------------------------------------------------------------


def _a9_calendar() -> CalendarVersion:
    return utc_calendar((0, 2 * D), ((0, 10 * H), (D, D + 10 * H)), name="a9")


def test_a9_tick_exactly_at_close_is_inside_closed_and_flagged() -> None:
    # Half-open session [00:00, 10:00): the instant 10:00 is closed, so a tick there contradicts it.
    p = gap(10 * H, D, (_a9_calendar(),))
    assert shape(p) == [(10 * H, D, Label.UNKNOWN, Reason.TICK_INSIDE_VERIFIED_CLOSED)]
    assert DefectKind.CALENDAR_DATA_CONTRADICTION in {d.kind for d in p.defects}


def test_a9_tick_one_ms_before_close_and_exactly_at_open() -> None:
    p = gap(10 * H - 1, D, (_a9_calendar(),))
    assert shape(p) == [
        (10 * H - 1, 10 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (10 * H, D, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
    ]
    assert p.open_ms == 1
    assert p.status is PartitionStatus.VERIFIED
    assert p.defects == ()


def test_a9_zero_duration_gap_is_empty_partition() -> None:
    p = gap(5 * H, 5 * H, (_a9_calendar(),))
    assert p.status is PartitionStatus.NO_GAP
    assert p.segments == ()
    assert (p.open_ms, p.closed_ms, p.unknown_ms) == (0, 0, 0)
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GE",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.NO_GAP


def test_a9_zero_length_session_rejects_calendar_deterministically() -> None:
    cal = utc_calendar((0, 2 * D), ((0, 10 * H), (12 * H, 12 * H)), name="zero")
    p = gap(H, 2 * H, (cal,))
    assert shape(p) == [(H, 2 * H, Label.UNKNOWN, Reason.CALENDAR_INVALID)]
    assert [d.kind for d in p.defects] == [DefectKind.CALENDAR_INVALID]
    assert gap(H, 2 * H, (cal,)) == p


# ---------------------------------------------------------------------------
# A10: overlapping sessions and out-of-order timestamps
# ---------------------------------------------------------------------------


def test_a10_overlapping_sessions_are_unioned_without_double_counting() -> None:
    cal = utc_calendar((0, D), ((0, 10 * H), (5 * H, 12 * H), (13 * H, 20 * H)), name="overlap")
    p = gap(4 * H, 13 * H, (cal,))
    assert shape(p) == [
        (4 * H, 12 * H, Label.VERIFIED_OPEN, Reason.SESSION_OPEN),
        (12 * H, 13 * H, Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS),
    ]
    assert p.open_ms == 8 * H
    assert [d.kind for d in p.defects] == [DefectKind.OVERLAPPING_OPEN_SESSIONS]
    assert_invariants(p)


def test_a10_out_of_order_timestamps_refused_never_sorted() -> None:
    cal = weekly_calendar()
    p = gap(D + 2 * H, D + H, (cal,))
    assert p.status is PartitionStatus.REFUSED_NON_MONOTONIC
    assert p.segments == ()
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert j.gap_class is GapClass.REFUSED_NON_MONOTONIC
    cov = window_coverage(
        D,
        2 * D,
        (D + 2 * H, D + H, D + 3 * H),
        calendars=(cal,),
        tick_utc_verified=True,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        edge_threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert cov.status == "REFUSED_NON_MONOTONIC"
    assert cov.judgements == ()


# ---------------------------------------------------------------------------
# Finding m3: partition first, then disconnect evidence on the open portion only
# ---------------------------------------------------------------------------


def test_m3_disconnect_over_closed_portion_does_not_explain_open_gap() -> None:
    cal = _a4_calendar()
    prev, nxt = 100 * H - 2 * M, 148 * H + 5 * M
    outage = DisconnectEvidence(ident("heartbeat"), ((110 * H, 120 * H),))
    p = gap(prev, nxt, (cal,))
    j = judge_gap(
        p,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=outage,
    )
    assert j.disconnect_open_overlap_ms == 0
    assert j.gap_class is GapClass.UNEXPLAINED
    old_cls, _ = old_rule_classify_gap(
        prev,
        nxt,
        closed_intervals=((100 * H, 148 * H),),
        disconnect_intervals=outage.intervals,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
    )
    assert old_cls == "source_fault_evidenced"  # old rule credits an outage during closure


def test_m3_disconnect_over_open_portion_counts_only_open_overlap() -> None:
    cal = _a4_calendar()
    prev, nxt = 100 * H - 2 * M, 148 * H + 5 * M
    outage = DisconnectEvidence(ident("heartbeat"), ((99 * H, 101 * H), (100 * H - M, 100 * H)))
    j = judge_gap(
        gap(prev, nxt, (cal,)),
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=outage,
    )
    assert j.disconnect_open_overlap_ms == 2 * M
    assert j.gap_class is GapClass.SOURCE_FAULT_EVIDENCED
    assert j.disconnect_evidence == outage.identity


# ---------------------------------------------------------------------------
# Window-level: UNKNOWN never enters numerator or denominator silently
# ---------------------------------------------------------------------------


def test_window_with_unknown_span_is_unverified() -> None:
    cal = utc_calendar((0, 10 * H), ((0, 10 * H),), name="partial")
    cov = window_coverage(
        0,
        12 * H,
        (0, 9 * H),
        calendars=(cal,),
        tick_utc_verified=True,
        threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        edge_threshold_ms=SYNTHETIC_THRESHOLD_1_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    assert cov.status == "UNVERIFIED"
    assert cov.unknown_ms == 2 * H
    assert cov.verified_open_ms == 10 * H


def test_window_spans_tile_window_exactly() -> None:
    cal = weekly_calendar()
    ticks = (D + H, D + H, D + 21 * H, 2 * D + 2 * H)
    cov = window_coverage(
        D,
        3 * D,
        ticks,
        calendars=(cal,),
        tick_utc_verified=True,
        threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        edge_threshold_ms=SYNTHETIC_THRESHOLD_6_MIN,
        direction="GT",
        measure=OpenMeasure.TOTAL_VERIFIED_OPEN,
        disconnects=None,
    )
    total = sum(j.partition.end_ms - j.partition.start_ms for j in cov.judgements)
    assert total == 2 * D
    assert cov.verified_open_ms == 44 * H
    assert cov.covered_open_ms == cov.verified_open_ms - cov.unexplained_open_ms


# ---------------------------------------------------------------------------
# Invariants: durations sum, determinism, no strategy input
# ---------------------------------------------------------------------------

_INVARIANT_CASES = [
    (D + 3 * H, D + 3 * H + 17 * M),
    (D + 21 * H + 50 * M, 2 * D + 20 * M),
    (3 * D + 22 * H - 2 * M, 7 * D + 5 * M),
    (5 * D + H, 5 * D + 2 * H),
    (13 * D, 15 * D),  # runs past calendar coverage
    (D, D),
    (2 * D, D),
]


@pytest.mark.parametrize(("prev", "nxt"), _INVARIANT_CASES)
def test_invariant_partition_sums_and_is_deterministic(prev: int, nxt: int) -> None:
    cal = weekly_calendar(exceptions=((2 * D, 3 * D, "EXCEPTION_CLOSED"),))
    first = gap(prev, nxt, (cal,))
    second = gap(prev, nxt, (cal,))
    assert_invariants(first)
    assert first == second
    assert canonical_sha256(first) == canonical_sha256(second)
    shuffled = CalendarVersion(
        cal.identity,
        cal.time_basis,
        cal.coverage_start_ms,
        cal.coverage_end_ms,
        tuple(reversed(cal.entries)),
    )
    assert canonical_sha256(gap(prev, nxt, (shuffled,))) == canonical_sha256(first)


def test_invariant_no_strategy_input_in_signatures() -> None:
    allowed: dict[Callable[..., object], set[str]] = {
        partition_gap: {"prev_ms", "next_ms", "calendars", "tick_utc_verified"},
        partition_span: {
            "start_ms",
            "end_ms",
            "calendars",
            "tick_utc_verified",
            "observed_ticks_ms",
        },
        judge_gap: {"partition", "threshold_ms", "direction", "measure", "disconnects"},
    }
    for fn, names in allowed.items():
        assert set(inspect.signature(fn).parameters) == names


def test_invariant_threshold_has_no_default() -> None:
    for fn in (judge_gap, window_coverage, old_rule_classify_gap):
        params = inspect.signature(fn).parameters
        for name in ("threshold_ms", "direction"):
            assert params[name].default is inspect.Parameter.empty
    assert inspect.signature(judge_gap).parameters["measure"].default is inspect.Parameter.empty
    assert (
        inspect.signature(window_coverage).parameters["edge_threshold_ms"].default
        is inspect.Parameter.empty
    )


def test_invariant_rejects_non_integer_timestamps() -> None:
    with pytest.raises(TypeError):
        partition_gap(1.5, 3, calendars=(), tick_utc_verified=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        partition_gap(True, 3, calendars=(), tick_utc_verified=True)
