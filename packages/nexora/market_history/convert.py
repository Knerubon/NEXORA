"""Raw broker time -> UTC through an explicit, evidence-referenced declaration only.

The offset is never inferred from the tick data itself and never applied outside the raw-time
interval it was declared for. A tick outside every declared segment is an error, not a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from nexora.market_history.models import HistoryError, NormalizedTick, RawTick, exact_sub

CONVERSION_VERSION = "nexora.time-conversion/2"
_BASES = ("operator_declared", "documented_by_source")


@dataclass(frozen=True, slots=True)
class TimeSegment:
    """utc_ms = raw_ms - offset_seconds * 1000 for raw_ms in [from_raw_msc, to_raw_msc)."""

    from_raw_msc: int
    to_raw_msc: int | None  # exclusive; None = open ended
    offset_seconds: int
    basis: str
    evidence_ref: str  # where the claim comes from (doc/ticket/measurement report); required

    def __post_init__(self) -> None:
        ints = (self.from_raw_msc, self.offset_seconds)
        if not all(type(v) is int for v in ints) or not (
            self.to_raw_msc is None or type(self.to_raw_msc) is int
        ):
            raise HistoryError("time_segment_malformed", "bounds and offset must be ints")
        if type(self.basis) is not str or type(self.evidence_ref) is not str:
            raise HistoryError("time_segment_malformed", "basis and evidence_ref must be text")
        if self.basis not in _BASES:
            raise HistoryError("unsupported_offset_basis", self.basis)
        if not self.evidence_ref.strip():
            raise HistoryError("offset_evidence_required")
        if self.to_raw_msc is not None and self.to_raw_msc <= self.from_raw_msc:
            raise HistoryError("empty_segment")

    def to_json(self) -> dict[str, Any]:
        return {
            "from_raw_msc": self.from_raw_msc,
            "to_raw_msc": self.to_raw_msc,
            "offset_seconds": self.offset_seconds,
            "basis": self.basis,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class TimeConversion:
    segments: tuple[TimeSegment, ...]
    version: str = CONVERSION_VERSION

    def __post_init__(self) -> None:
        if not self.segments:
            raise HistoryError("time_conversion_required")
        ordered = sorted(self.segments, key=lambda s: s.from_raw_msc)
        if tuple(ordered) != self.segments:
            raise HistoryError("segments_not_sorted")
        for prev, nxt in zip(ordered, ordered[1:], strict=False):
            if prev.to_raw_msc is None or prev.to_raw_msc > nxt.from_raw_msc:
                raise HistoryError("segments_overlap")

    def segment_for(self, raw_msc: int) -> TimeSegment:
        for seg in self.segments:
            if raw_msc >= seg.from_raw_msc and (seg.to_raw_msc is None or raw_msc < seg.to_raw_msc):
                return seg
        raise HistoryError("no_declared_offset_for_time", str(raw_msc))

    def to_utc_msc(self, raw_msc: int) -> int:
        return raw_msc - self.segment_for(raw_msc).offset_seconds * 1000

    def covers(self, from_raw: int, to_raw: int) -> bool:
        """True when every raw instant in [from_raw, to_raw) has a declared offset."""
        cursor = from_raw
        for seg in self.segments:
            if seg.to_raw_msc is not None and seg.to_raw_msc <= cursor:
                continue
            if seg.from_raw_msc > cursor:
                return False
            cursor = to_raw if seg.to_raw_msc is None else seg.to_raw_msc
            if cursor >= to_raw:
                return True
        return cursor >= to_raw

    def derived_fold_ranges(self) -> tuple[tuple[int, int], ...]:
        """RAW ranges that cannot be mapped to one UTC instant, derived from the segments alone.

        A falling offset between two segments is a clock fall-back: raw time repeats by the size
        of the drop. A raw-keyed segment boundary cannot say whether the repeated stretch sits
        just before or just after the boundary, so BOTH sides are ambiguous:
        [boundary_end - drop, boundary_start + drop). The operator cannot waive or shrink this."""
        out: list[tuple[int, int]] = []
        for prev, nxt in zip(self.segments, self.segments[1:], strict=False):
            drop_ms = (prev.offset_seconds - nxt.offset_seconds) * 1000
            if drop_ms > 0 and prev.to_raw_msc is not None:
                out.append((prev.to_raw_msc - drop_ms, nxt.from_raw_msc + drop_ms))
        return tuple(out)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "segments": [s.to_json() for s in self.segments],
            "derived_fold_ranges": [list(r) for r in self.derived_fold_ranges()],
        }


def normalize(ticks: Iterable[RawTick], conversion: TimeConversion) -> Iterator[NormalizedTick]:
    """Order-preserving; drops nothing, reorders nothing (quality checks report disorder)."""
    for t in ticks:
        yield NormalizedTick(
            conversion.to_utc_msc(t.time_msc), t.time_msc, t.bid, t.ask, exact_sub(t.ask, t.bid),
            t.flags,
        )
