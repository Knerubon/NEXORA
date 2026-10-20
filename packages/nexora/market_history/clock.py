"""Fail-closed handling of broker-clock ambiguity (DST fall-back / repeated raw time).

A time-range query on RAW broker time cannot separate the two passes of a repeated hour, and an
ascending provider interleaves them without leaving any trace in the data. Detection by ordering
is therefore NOT relied on. Instead extraction requires a recorded, evidence-referenced
`ClockAttestation`, machine-checks it against the declared `TimeConversion`, stores it in the
dataset identity, and `verify_dataset` re-checks it. An attestation proves nothing about the
broker by itself: it makes the claim explicit, attributable and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexora.market_history.convert import TimeConversion
from nexora.market_history.models import HistoryError

CLAIM = (
    "every raw broker-clock instant in the attested window maps to exactly one UTC instant: "
    "no repeated (fall-back) hour and no ambiguous raw time"
)


def _is_int(value: object) -> bool:
    return type(value) is int


@dataclass(frozen=True, slots=True)
class ClockAttestation:
    evidence_ref: str  # report/ticket establishing the claim for this window
    attested_by: str
    window_from_raw_msc: int
    window_to_raw_msc: int  # exclusive
    # Known ambiguous raw ranges [lo, hi) (e.g. a repeated hour). A window must not touch them.
    ambiguous_raw_ranges: tuple[tuple[int, int], ...] = ()
    claim: str = CLAIM

    def __post_init__(self) -> None:
        for text in (self.evidence_ref, self.attested_by):
            if type(text) is not str or not text.strip():
                raise HistoryError("attestation_evidence_required")
        if self.claim != CLAIM:
            raise HistoryError("attestation_claim_mismatch")
        if not (_is_int(self.window_from_raw_msc) and _is_int(self.window_to_raw_msc)):
            raise HistoryError("attestation_window_invalid")
        if self.window_to_raw_msc <= self.window_from_raw_msc:
            raise HistoryError("attestation_window_invalid")
        for rng in self.ambiguous_raw_ranges:
            if len(rng) != 2 or not all(_is_int(x) for x in rng) or rng[0] >= rng[1]:
                raise HistoryError("attestation_ambiguous_range_invalid")

    def to_json(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "evidence_ref": self.evidence_ref,
            "attested_by": self.attested_by,
            "window_from_raw_msc": self.window_from_raw_msc,
            "window_to_raw_msc": self.window_to_raw_msc,
            "ambiguous_raw_ranges": [list(r) for r in self.ambiguous_raw_ranges],
        }

    @classmethod
    def from_json(cls, data: Any) -> ClockAttestation:
        try:
            return cls(
                evidence_ref=data["evidence_ref"],
                attested_by=data["attested_by"],
                window_from_raw_msc=data["window_from_raw_msc"],
                window_to_raw_msc=data["window_to_raw_msc"],
                ambiguous_raw_ranges=tuple((r[0], r[1]) for r in data["ambiguous_raw_ranges"]),
                claim=data["claim"],
            )
        except (KeyError, TypeError, IndexError):
            raise HistoryError("attestation_malformed") from None


def check_attestation(
    attestation: ClockAttestation | None,
    conversion: TimeConversion,
    from_raw_msc: int,
    to_raw_msc: int,
) -> None:
    """Raise HistoryError unless the window is covered by a consistent attestation."""
    if attestation is None:
        raise HistoryError("clock_attestation_required")
    if attestation.window_from_raw_msc > from_raw_msc or attestation.window_to_raw_msc < to_raw_msc:
        raise HistoryError("attestation_does_not_cover_window")
    for lo, hi in attestation.ambiguous_raw_ranges:
        if lo < to_raw_msc and from_raw_msc < hi:
            raise HistoryError("window_touches_ambiguous_raw_range", f"{lo}-{hi}")
    # Machine-derived, not operator-declared: every falling offset in the conversion makes the
    # raw time around its boundary ambiguous, and no attestation can waive that. A window that
    # touches it must be split so the repeated stretch is excluded.
    for lo, hi in conversion.derived_fold_ranges():
        if lo < to_raw_msc and from_raw_msc < hi:
            raise HistoryError("window_touches_derived_fold_range", f"{lo}-{hi}")
