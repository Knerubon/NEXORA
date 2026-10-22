"""MD-6 gap interval partition: reference model.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

Specified in docs/research/edge-validation/md6-data-gap-policy/GAP-INTERVAL-PARTITION.md.

A gap between two observed UTC instants ``[prev, next)`` is partitioned into maximal half-open
sub-intervals labelled VERIFIED_OPEN, VERIFIED_CLOSED or UNKNOWN, using calendar evidence only.
Only VERIFIED_OPEN time is eligible open-market gap time. UNKNOWN fails closed.

No numeric MD-6 threshold, tolerance or limit is chosen here. Every such value is a required
parameter without a default; tests pass SYNTHETIC values. Ratios are returned as integer
numerator/denominator pairs and are never compared with a limit in this module.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class Label(enum.Enum):
    VERIFIED_OPEN = "VERIFIED_OPEN"
    VERIFIED_CLOSED = "VERIFIED_CLOSED"
    UNKNOWN = "UNKNOWN"


class Reason(enum.Enum):
    # VERIFIED_OPEN reasons
    SESSION_OPEN = "SESSION_OPEN"
    EXCEPTION_OPEN = "EXCEPTION_OPEN"
    # VERIFIED_CLOSED reasons
    OUTSIDE_SESSIONS = "OUTSIDE_SESSIONS"
    EXCEPTION_CLOSED = "EXCEPTION_CLOSED"
    # UNKNOWN reasons (fail closed)
    NO_CALENDAR_COVERAGE = "NO_CALENDAR_COVERAGE"
    CALENDAR_INVALID = "CALENDAR_INVALID"
    CALENDAR_AUTHORITY_CONFLICT = "CALENDAR_AUTHORITY_CONFLICT"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    TZ_AMBIGUOUS_OR_NONEXISTENT = "TZ_AMBIGUOUS_OR_NONEXISTENT"
    TZ_EVIDENCE_MISSING = "TZ_EVIDENCE_MISSING"
    TICK_UTC_UNVERIFIED = "TICK_UTC_UNVERIFIED"
    TICK_INSIDE_VERIFIED_CLOSED = "TICK_INSIDE_VERIFIED_CLOSED"


class DefectKind(enum.Enum):
    CALENDAR_DATA_CONTRADICTION = "CALENDAR_DATA_CONTRADICTION"
    CALENDAR_INVALID = "CALENDAR_INVALID"
    OVERLAPPING_OPEN_SESSIONS = "OVERLAPPING_OPEN_SESSIONS"
    TZ_EVIDENCE_MISSING = "TZ_EVIDENCE_MISSING"
    TZ_BOUNDARY_UNRESOLVED = "TZ_BOUNDARY_UNRESOLVED"


class PartitionStatus(enum.Enum):
    VERIFIED = "VERIFIED"  # every segment is VERIFIED_OPEN or VERIFIED_CLOSED
    UNVERIFIED = "UNVERIFIED"  # at least one UNKNOWN segment
    NO_GAP = "NO_GAP"  # prev == next: empty partition
    REFUSED_NON_MONOTONIC = "REFUSED_NON_MONOTONIC"  # next < prev: never sorted


class OpenMeasure(enum.Enum):
    """Per-gap quantity compared with the OPEN threshold. Choice is a Quant decision (OPEN)."""

    TOTAL_VERIFIED_OPEN = "TOTAL_VERIFIED_OPEN"
    LONGEST_CONTIGUOUS_VERIFIED_OPEN = "LONGEST_CONTIGUOUS_VERIFIED_OPEN"


class GapClass(enum.Enum):
    NO_GAP = "no_gap"
    REFUSED_NON_MONOTONIC = "refused_non_monotonic"
    UNVERIFIED = "unverified"
    EXPECTED_CLOSED = "expected_closed"
    WITHIN_THRESHOLD = "within_threshold"
    SOURCE_FAULT_EVIDENCED = "source_fault_evidenced"
    UNEXPLAINED = "unexplained"


Direction = Literal["GT", "GE"]
TimeBasis = Literal["UTC", "VENUE_LOCAL"]
EntryKind = Literal["SESSION_OPEN", "EXCEPTION_OPEN", "EXCEPTION_CLOSED"]


# ---------------------------------------------------------------------------
# Evidence artifacts (all data, never code)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class ArtifactIdentity:
    """Identity recorded with every labelled segment: artifact id, content hash, version."""

    artifact_id: str
    content_sha256: str
    version: str


@dataclass(frozen=True)
class OffsetSegment:
    """From ``utc_start_ms`` (inclusive) the venue-local wall clock is UTC + ``offset_ms``."""

    utc_start_ms: int
    offset_ms: int


@dataclass(frozen=True)
class OffsetTransitionTable:
    """Evidenced venue-local to UTC rule. In tests it is an explicit SYNTHETIC table.

    Segments are valid from their ``utc_start_ms`` to the next segment start; the last one to
    ``utc_coverage_end_ms``. Without an ``evidence_ref`` the rule is not admissible (D07).
    """

    identity: ArtifactIdentity
    evidence_ref: str | None
    segments: tuple[OffsetSegment, ...]
    utc_coverage_end_ms: int


@dataclass(frozen=True)
class CalendarEntry:
    start_ms: int
    end_ms: int
    kind: EntryKind


@dataclass(frozen=True)
class CalendarVersion:
    """One versioned, hashed calendar artifact.

    Inside ``[coverage_start_ms, coverage_end_ms)`` the calendar is authoritative: time in a
    SESSION_OPEN entry (union of overlapping sessions) is open, everything else is closed, and
    EXCEPTION_* entries override regular sessions. Outside coverage the calendar says nothing.
    Times are in ``time_basis``; VENUE_LOCAL times are wall-clock ms and need ``tz_rule``.
    """

    identity: ArtifactIdentity
    time_basis: TimeBasis
    coverage_start_ms: int
    coverage_end_ms: int
    entries: tuple[CalendarEntry, ...]
    tz_rule: OffsetTransitionTable | None = None


@dataclass(frozen=True)
class DisconnectEvidence:
    """D05 artifact. ``intervals`` are UTC half-open disconnect intervals."""

    identity: ArtifactIdentity
    intervals: tuple[tuple[int, int], ...]


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    label: Label
    reason: Reason
    calendar: ArtifactIdentity | None
    tz_rule: ArtifactIdentity | None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class Defect:
    kind: DefectKind
    start_ms: int
    end_ms: int
    detail: str


@dataclass(frozen=True)
class Partition:
    start_ms: int
    end_ms: int
    status: PartitionStatus
    segments: tuple[Segment, ...]
    defects: tuple[Defect, ...]
    open_ms: int
    closed_ms: int
    unknown_ms: int
    longest_open_run_ms: int
    calendars: tuple[ArtifactIdentity, ...]
    tz_rules: tuple[ArtifactIdentity, ...]


@dataclass(frozen=True)
class GapJudgement:
    gap_class: GapClass
    partition: Partition
    measure: OpenMeasure
    measured_open_ms: int
    disconnect_open_overlap_ms: int
    disconnect_evidence: ArtifactIdentity | None


@dataclass(frozen=True)
class WindowCoverage:
    """A-06 and D12 quantities as integer ms. No comparison with any limit is made here."""

    status: Literal[
        "VERIFIED", "UNVERIFIED", "NO_TICKS", "REFUSED_NON_MONOTONIC", "NO_VERIFIED_OPEN_TIME"
    ]
    verified_open_ms: int  # A-06 denominator and D12 denominator
    unexplained_open_ms: int  # A-06 numerator
    source_fault_open_ms: int
    covered_open_ms: int  # D12 numerator
    unknown_ms: int
    judgements: tuple[GapJudgement, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Time-zone conversion (D07): never guessed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolved:
    utc_ms: int


@dataclass(frozen=True)
class _Uncertain:
    lo_ms: int
    hi_ms: int


@dataclass(frozen=True)
class _OutOfCoverage:
    pass


_Conversion = _Resolved | _Uncertain | _OutOfCoverage


def _tz_rule_admissible(rule: OffsetTransitionTable | None) -> bool:
    if rule is None or not rule.evidence_ref or not rule.segments:
        return False
    starts = [s.utc_start_ms for s in rule.segments]
    if any(b <= a for a, b in zip(starts, starts[1:], strict=False)):
        return False
    return rule.utc_coverage_end_ms > starts[-1]


def local_to_utc(local_ms: int, rule: OffsetTransitionTable) -> _Conversion:
    """Convert a venue-local wall-clock instant using an evidenced transition table.

    Exactly one consistent candidate: resolved. Two or more (ambiguous, fall-back) or none inside
    a transition (nonexistent, spring-forward): uncertain span between the readings. Outside the
    table: out of coverage (evidence missing).
    """
    segs = rule.segments
    bounds = [s.utc_start_ms for s in segs] + [rule.utc_coverage_end_ms]
    candidates = sorted(
        local_ms - s.offset_ms
        for i, s in enumerate(segs)
        if bounds[i] <= local_ms - s.offset_ms < bounds[i + 1]
    )
    if len(candidates) == 1:
        return _Resolved(candidates[0])
    if len(candidates) >= 2:
        return _Uncertain(candidates[0], candidates[-1])
    for i in range(1, len(segs)):
        t = segs[i].utc_start_ms
        a, b = segs[i - 1].offset_ms, segs[i].offset_ms
        if t + min(a, b) <= local_ms < t + max(a, b):
            return _Uncertain(local_ms - max(a, b), local_ms - min(a, b))
    return _OutOfCoverage()


# ---------------------------------------------------------------------------
# Calendar claims
# ---------------------------------------------------------------------------

# Claim kinds, in addition to EntryKind: "COVERAGE" (authority span) and "TZ_UNKNOWN".


@dataclass(frozen=True)
class _Claim:
    start_ms: int
    end_ms: int
    kind: str


@dataclass(frozen=True)
class _VersionClaims:
    version: CalendarVersion
    claims: tuple[_Claim, ...]


def _validate_version(v: CalendarVersion) -> list[str]:
    problems: list[str] = []
    ident = v.identity
    if not (ident.artifact_id and ident.content_sha256 and ident.version):
        problems.append("calendar identity incomplete")
    if v.coverage_start_ms >= v.coverage_end_ms:
        problems.append("coverage start >= end")
    for e in v.entries:
        if e.start_ms >= e.end_ms:
            problems.append(f"entry {e.kind} [{e.start_ms},{e.end_ms}) has start >= end")
    return problems


def _convert_interval(
    start: int, end: int, kind: str, rule: OffsetTransitionTable
) -> list[_Claim] | None:
    """Convert a local interval. ``None`` means a boundary lies outside offset evidence."""
    cs, ce = local_to_utc(start, rule), local_to_utc(end, rule)
    if isinstance(cs, _OutOfCoverage) or isinstance(ce, _OutOfCoverage):
        return None
    out: list[_Claim] = []
    if isinstance(cs, _Resolved):
        lo = cs.utc_ms
    else:
        out.append(_Claim(cs.lo_ms, cs.hi_ms, "TZ_UNKNOWN"))
        lo = cs.hi_ms
    if isinstance(ce, _Resolved):
        hi = ce.utc_ms
    else:
        out.append(_Claim(ce.lo_ms, ce.hi_ms, "TZ_UNKNOWN"))
        hi = ce.lo_ms
    if kind == "COVERAGE":
        # Authority spans the whole uncertain range; the TZ_UNKNOWN claims override inside it.
        lo = cs.lo_ms if isinstance(cs, _Uncertain) else lo
        hi = ce.hi_ms if isinstance(ce, _Uncertain) else hi
    if lo < hi:
        out.append(_Claim(lo, hi, kind))
    return out


def _version_claims(v: CalendarVersion) -> _VersionClaims | None:
    raw = [(v.coverage_start_ms, v.coverage_end_ms, "COVERAGE")] + [
        (e.start_ms, e.end_ms, e.kind) for e in v.entries
    ]
    if v.time_basis == "UTC":
        return _VersionClaims(v, tuple(_Claim(s, e, k) for s, e, k in raw))
    rule = v.tz_rule
    assert rule is not None  # validated earlier
    claims: list[_Claim] = []
    for s, e, k in raw:
        converted = _convert_interval(s, e, k, rule)
        if converted is None:
            return None
        claims.extend(converted)
    return _VersionClaims(v, tuple(claims))


def _covers(c: _Claim, x: int) -> bool:
    return c.start_ms <= x < c.end_ms


def _version_label(vc: _VersionClaims, x: int) -> tuple[Label, Reason] | None:
    """Label of instant ``x`` (an elementary piece start) according to one version."""
    active = [c for c in vc.claims if _covers(c, x)]
    kinds = {c.kind for c in active}
    if "COVERAGE" not in kinds:
        return None
    if "TZ_UNKNOWN" in kinds:
        return Label.UNKNOWN, Reason.TZ_AMBIGUOUS_OR_NONEXISTENT
    if "EXCEPTION_OPEN" in kinds and "EXCEPTION_CLOSED" in kinds:
        return Label.UNKNOWN, Reason.CONFLICTING_EVIDENCE
    if "EXCEPTION_CLOSED" in kinds:
        return Label.VERIFIED_CLOSED, Reason.EXCEPTION_CLOSED
    if "EXCEPTION_OPEN" in kinds:
        return Label.VERIFIED_OPEN, Reason.EXCEPTION_OPEN
    if "SESSION_OPEN" in kinds:
        return Label.VERIFIED_OPEN, Reason.SESSION_OPEN
    return Label.VERIFIED_CLOSED, Reason.OUTSIDE_SESSIONS


def _overlap_defects(vc: _VersionClaims) -> list[Defect]:
    sessions = sorted(
        (c for c in vc.claims if c.kind == "SESSION_OPEN"), key=lambda c: (c.start_ms, c.end_ms)
    )
    out: list[Defect] = []
    reach: int | None = None
    for c in sessions:
        if reach is not None and c.start_ms < reach:
            out.append(
                Defect(
                    DefectKind.OVERLAPPING_OPEN_SESSIONS,
                    c.start_ms,
                    min(reach, c.end_ms),
                    f"{vc.version.identity.artifact_id}: overlapping sessions unioned",
                )
            )
        reach = c.end_ms if reach is None else max(reach, c.end_ms)
    return out


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------


def _require_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer millisecond value")


@dataclass
class _Piece:
    start_ms: int
    end_ms: int
    label: Label
    reason: Reason
    calendar: ArtifactIdentity | None
    tz_rule: ArtifactIdentity | None


def _whole_unknown(start: int, end: int, reason: Reason, defects: list[Defect]) -> Partition:
    seg = Segment(start, end, Label.UNKNOWN, reason, None, None)
    return _finish(start, end, [seg], defects, (), ())


def _finish(
    start: int,
    end: int,
    segments: list[Segment],
    defects: list[Defect],
    calendars: tuple[ArtifactIdentity, ...],
    tz_rules: tuple[ArtifactIdentity, ...],
) -> Partition:
    open_ms = sum(s.duration_ms for s in segments if s.label is Label.VERIFIED_OPEN)
    closed_ms = sum(s.duration_ms for s in segments if s.label is Label.VERIFIED_CLOSED)
    unknown_ms = sum(s.duration_ms for s in segments if s.label is Label.UNKNOWN)
    longest = 0
    run = 0
    for s in segments:
        run = run + s.duration_ms if s.label is Label.VERIFIED_OPEN else 0
        longest = max(longest, run)
    status = PartitionStatus.UNVERIFIED if unknown_ms > 0 else PartitionStatus.VERIFIED
    ordered_defects = tuple(
        sorted(defects, key=lambda d: (d.start_ms, d.end_ms, d.kind.value, d.detail))
    )
    return Partition(
        start,
        end,
        status,
        tuple(segments),
        ordered_defects,
        open_ms,
        closed_ms,
        unknown_ms,
        longest,
        calendars,
        tz_rules,
    )


def partition_span(
    start_ms: int,
    end_ms: int,
    *,
    calendars: tuple[CalendarVersion, ...],
    tick_utc_verified: bool,
    observed_ticks_ms: tuple[int, ...],
) -> Partition:
    """Partition ``[start_ms, end_ms)`` by calendar evidence.

    ``observed_ticks_ms`` are the observed ticks at the span ends (each in ``[start, end]``).
    A tick lying in a VERIFIED_CLOSED run turns that whole run UNKNOWN and records a
    CALENDAR_DATA_CONTRADICTION defect.
    """
    _require_int("start_ms", start_ms)
    _require_int("end_ms", end_ms)
    for t in observed_ticks_ms:
        _require_int("observed tick", t)
    if end_ms < start_ms:
        return Partition(
            start_ms, end_ms, PartitionStatus.REFUSED_NON_MONOTONIC, (), (), 0, 0, 0, 0, (), ()
        )
    if end_ms == start_ms:
        return Partition(start_ms, end_ms, PartitionStatus.NO_GAP, (), (), 0, 0, 0, 0, (), ())
    if any(not start_ms <= t <= end_ms for t in observed_ticks_ms):
        raise ValueError("observed ticks must lie inside [start_ms, end_ms]")

    defects: list[Defect] = []
    if not tick_utc_verified:
        return _whole_unknown(start_ms, end_ms, Reason.TICK_UTC_UNVERIFIED, defects)

    # Validate every calendar version; one invalid version rejects the calendar input.
    for v in calendars:
        for problem in _validate_version(v):
            defects.append(
                Defect(
                    DefectKind.CALENDAR_INVALID,
                    start_ms,
                    end_ms,
                    f"{v.identity.artifact_id}: {problem}",
                )
            )
    if defects:
        return _whole_unknown(start_ms, end_ms, Reason.CALENDAR_INVALID, defects)

    # Offset evidence for venue-local calendars; missing evidence makes the whole span UNKNOWN.
    version_claims: list[_VersionClaims] = []
    for v in calendars:
        if v.time_basis == "VENUE_LOCAL" and not _tz_rule_admissible(v.tz_rule):
            defects.append(
                Defect(
                    DefectKind.TZ_EVIDENCE_MISSING,
                    start_ms,
                    end_ms,
                    f"{v.identity.artifact_id}: no admissible evidenced offset rule",
                )
            )
            continue
        vc = _version_claims(v)
        if vc is None:
            defects.append(
                Defect(
                    DefectKind.TZ_EVIDENCE_MISSING,
                    start_ms,
                    end_ms,
                    f"{v.identity.artifact_id}: boundary outside offset evidence",
                )
            )
            continue
        version_claims.append(vc)
    if defects:
        return _whole_unknown(start_ms, end_ms, Reason.TZ_EVIDENCE_MISSING, defects)

    for vc in version_claims:
        defects.extend(_overlap_defects(vc))
        for c in vc.claims:
            if c.kind == "TZ_UNKNOWN":
                defects.append(
                    Defect(
                        DefectKind.TZ_BOUNDARY_UNRESOLVED,
                        c.start_ms,
                        c.end_ms,
                        f"{vc.version.identity.artifact_id}: ambiguous or nonexistent local time",
                    )
                )

    # Label elementary pieces over [start, end + 1) so the label at ``end`` (a tick) is known.
    hi = end_ms + 1
    points = {start_ms, hi}
    for vc in version_claims:
        for c in vc.claims:
            for cut in (c.start_ms, c.end_ms):
                if start_ms < cut < hi:
                    points.add(cut)
    cuts = sorted(points)
    pieces: list[_Piece] = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        votes: list[tuple[_VersionClaims, tuple[Label, Reason]]] = []
        for vc in version_claims:
            lab = _version_label(vc, a)
            if lab is not None:
                votes.append((vc, lab))
        if not votes:
            pieces.append(_Piece(a, b, Label.UNKNOWN, Reason.NO_CALENDAR_COVERAGE, None, None))
        elif len(votes) > 1:
            pieces.append(
                _Piece(a, b, Label.UNKNOWN, Reason.CALENDAR_AUTHORITY_CONFLICT, None, None)
            )
        else:
            vc, (label, reason) = votes[0]
            rule = vc.version.tz_rule
            tz = (
                rule.identity
                if rule is not None and vc.version.time_basis == "VENUE_LOCAL"
                else None
            )
            pieces.append(_Piece(a, b, label, reason, vc.version.identity, tz))

    # Ticks observed inside VERIFIED_CLOSED: the calendar is contradicted for the whole run.
    snapshot = [p.label for p in pieces]
    contradicted: set[int] = set()
    for t in sorted(set(observed_ticks_ms)):
        idx = next(i for i, p in enumerate(pieces) if p.start_ms <= t < p.end_ms)
        if snapshot[idx] is not Label.VERIFIED_CLOSED:
            continue
        lo = idx
        while lo > 0 and snapshot[lo - 1] is Label.VERIFIED_CLOSED:
            lo -= 1
        up = idx
        while up + 1 < len(pieces) and snapshot[up + 1] is Label.VERIFIED_CLOSED:
            up += 1
        defects.append(
            Defect(
                DefectKind.CALENDAR_DATA_CONTRADICTION,
                pieces[lo].start_ms,
                min(pieces[up].end_ms, end_ms),
                f"tick at {t} inside VERIFIED_CLOSED",
            )
        )
        contradicted.update(range(lo, up + 1))
    for i in sorted(contradicted):
        p = pieces[i]
        pieces[i] = _Piece(
            p.start_ms,
            p.end_ms,
            Label.UNKNOWN,
            Reason.TICK_INSIDE_VERIFIED_CLOSED,
            p.calendar,
            p.tz_rule,
        )

    # Clip to [start, end) and merge adjacent pieces with the same key into maximal segments.
    segments: list[Segment] = []
    for p in pieces:
        a, b = p.start_ms, min(p.end_ms, end_ms)
        if a >= b:
            continue
        if segments:
            last = segments[-1]
            if (last.label, last.reason, last.calendar, last.tz_rule) == (
                p.label,
                p.reason,
                p.calendar,
                p.tz_rule,
            ):
                segments[-1] = Segment(last.start_ms, b, p.label, p.reason, p.calendar, p.tz_rule)
                continue
        segments.append(Segment(a, b, p.label, p.reason, p.calendar, p.tz_rule))

    cal_ids = tuple(sorted({s.calendar for s in segments if s.calendar is not None}))
    tz_ids = tuple(sorted({s.tz_rule for s in segments if s.tz_rule is not None}))
    return _finish(start_ms, end_ms, segments, defects, cal_ids, tz_ids)


def partition_gap(
    prev_ms: int,
    next_ms: int,
    *,
    calendars: tuple[CalendarVersion, ...],
    tick_utc_verified: bool,
) -> Partition:
    """Partition the gap between two consecutive observed ticks ``[prev, next)``."""
    return partition_span(
        prev_ms,
        next_ms,
        calendars=calendars,
        tick_utc_verified=tick_utc_verified,
        observed_ticks_ms=(prev_ms, next_ms) if next_ms >= prev_ms else (),
    )


# ---------------------------------------------------------------------------
# Judging (threshold, direction, measure are required; values are OPEN)
# ---------------------------------------------------------------------------


def _exceeds(value: int, threshold_ms: int, direction: Direction) -> bool:
    return value > threshold_ms if direction == "GT" else value >= threshold_ms


def _open_overlap(partition: Partition, intervals: tuple[tuple[int, int], ...]) -> int:
    total = 0
    for s in partition.segments:
        if s.label is not Label.VERIFIED_OPEN:
            continue
        # Union the disconnect intervals inside this segment so overlaps never double count.
        clipped = sorted(
            (max(a, s.start_ms), min(b, s.end_ms))
            for a, b in intervals
            if max(a, s.start_ms) < min(b, s.end_ms)
        )
        reach = s.start_ms
        for a, b in clipped:
            a = max(a, reach)
            if a < b:
                total += b - a
                reach = b
    return total


def judge_gap(
    partition: Partition,
    *,
    threshold_ms: int,
    direction: Direction,
    measure: OpenMeasure,
    disconnects: DisconnectEvidence | None,
) -> GapJudgement:
    """Classify one partitioned gap. Partition first, then evidence (finding m3)."""
    _require_int("threshold_ms", threshold_ms)
    measured = (
        partition.open_ms
        if measure is OpenMeasure.TOTAL_VERIFIED_OPEN
        else partition.longest_open_run_ms
    )
    ident = disconnects.identity if disconnects is not None else None
    overlap = _open_overlap(partition, disconnects.intervals) if disconnects is not None else 0

    def out(cls: GapClass) -> GapJudgement:
        return GapJudgement(cls, partition, measure, measured, overlap, ident)

    if partition.status is PartitionStatus.REFUSED_NON_MONOTONIC:
        return out(GapClass.REFUSED_NON_MONOTONIC)
    if partition.status is PartitionStatus.NO_GAP:
        return out(GapClass.NO_GAP)
    if partition.status is PartitionStatus.UNVERIFIED:
        return out(GapClass.UNVERIFIED)
    if partition.open_ms == 0:
        return out(GapClass.EXPECTED_CLOSED)
    if not _exceeds(measured, threshold_ms, direction):
        return out(GapClass.WITHIN_THRESHOLD)
    if overlap > 0:
        return out(GapClass.SOURCE_FAULT_EVIDENCED)
    return out(GapClass.UNEXPLAINED)


def window_coverage(
    window_from_ms: int,
    window_to_ms: int,
    ticks_ms: tuple[int, ...],
    *,
    calendars: tuple[CalendarVersion, ...],
    tick_utc_verified: bool,
    threshold_ms: int,
    edge_threshold_ms: int,
    direction: Direction,
    measure: OpenMeasure,
    disconnects: DisconnectEvidence | None,
) -> WindowCoverage:
    """A-06 and D12 quantities for a half-open window. Ticks are in stored order, never sorted."""
    _require_int("window_from_ms", window_from_ms)
    _require_int("window_to_ms", window_to_ms)
    if window_to_ms <= window_from_ms:
        raise ValueError("window must have from < to")
    for t in ticks_ms:
        _require_int("tick", t)
        if not window_from_ms <= t < window_to_ms:
            raise ValueError("tick outside half-open window")
    if any(b < a for a, b in zip(ticks_ms, ticks_ms[1:], strict=False)):
        return WindowCoverage("REFUSED_NON_MONOTONIC", 0, 0, 0, 0, 0)

    def span(lo: int, hi: int, ticks: tuple[int, ...]) -> Partition:
        return partition_span(
            lo,
            hi,
            calendars=calendars,
            tick_utc_verified=tick_utc_verified,
            observed_ticks_ms=ticks,
        )

    if not ticks_ms:
        whole = span(window_from_ms, window_to_ms, ())
        return WindowCoverage("NO_TICKS", whole.open_ms, 0, 0, 0, whole.unknown_ms)

    # Leading edge, inter-tick gaps, trailing edge: these tile [from, to) exactly (D06).
    spans: list[tuple[Partition, int]] = [
        (span(window_from_ms, ticks_ms[0], (ticks_ms[0],)), edge_threshold_ms)
    ]
    for a, b in zip(ticks_ms, ticks_ms[1:], strict=False):
        if a != b:
            spans.append((span(a, b, (a, b)), threshold_ms))
    spans.append((span(ticks_ms[-1], window_to_ms, (ticks_ms[-1],)), edge_threshold_ms))
    judgements = tuple(
        judge_gap(
            p, threshold_ms=thr, direction=direction, measure=measure, disconnects=disconnects
        )
        for p, thr in spans
        if p.status is not PartitionStatus.NO_GAP
    )
    verified_open = sum(j.partition.open_ms for j in judgements)
    unknown = sum(j.partition.unknown_ms for j in judgements)
    unexplained = sum(
        j.partition.open_ms for j in judgements if j.gap_class is GapClass.UNEXPLAINED
    )
    fault = sum(
        j.partition.open_ms for j in judgements if j.gap_class is GapClass.SOURCE_FAULT_EVIDENCED
    )
    covered = verified_open - unexplained - fault
    status: Literal[
        "VERIFIED", "UNVERIFIED", "NO_TICKS", "REFUSED_NON_MONOTONIC", "NO_VERIFIED_OPEN_TIME"
    ]
    if unknown > 0:
        status = "UNVERIFIED"
    elif verified_open == 0:
        status = "NO_VERIFIED_OPEN_TIME"
    else:
        status = "VERIFIED"
    return WindowCoverage(status, verified_open, unexplained, fault, covered, unknown, judgements)


# ---------------------------------------------------------------------------
# OLD rule at PR head 3eaf57cb, encoded literally for regression comparison only
# ---------------------------------------------------------------------------


def old_rule_classify_gap(
    prev_ms: int,
    next_ms: int,
    *,
    closed_intervals: tuple[tuple[int, int], ...],
    disconnect_intervals: tuple[tuple[int, int], ...],
    threshold_ms: int,
    direction: Direction,
) -> tuple[str, int]:
    """D03 as written: expected_closed only if the whole gap is inside one closed interval.

    Returns (class, duration counted). Kept to demonstrate finding M1; not a policy.
    """
    duration = next_ms - prev_ms
    if not _exceeds(duration, threshold_ms, direction):
        return "within_threshold", duration
    if any(a <= prev_ms and next_ms <= b for a, b in closed_intervals):
        return "expected_closed", duration
    if any(max(a, prev_ms) < min(b, next_ms) for a, b in disconnect_intervals):
        return "source_fault_evidenced", duration
    return "unexplained", duration


# ---------------------------------------------------------------------------
# Canonical serialization (determinism checks)
# ---------------------------------------------------------------------------


def _plain(obj: object) -> object:
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, tuple | list):
        return [_plain(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _plain(getattr(obj, k)) for k in sorted(obj.__dataclass_fields__)}
    return obj


def canonical_json(obj: object) -> str:
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"))


def canonical_sha256(obj: object) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
