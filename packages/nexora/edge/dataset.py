"""Edge-validation dataset manifest: provenance, integrity hashes and quality audit.

Builds on the existing `nexora.backtest` DatasetManifest and `verify_events`; it adds the
provenance, data-class and quality evidence the edge study needs. Missing market data is
reported, never filled in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from nexora.artifacts import canonical_hash
from nexora.backtest.datasets import manifest_for, verify_events
from nexora.backtest.models import DatasetManifest
from nexora.market_data.models import NormalizedPriceEvent

DataClass = Literal["synthetic_fixture", "real_market"]

# Issue codes that make a dataset unusable unless explicitly accepted in the manifest.
BLOCKING_CODES = frozenset(
    {
        "duplicate",
        "out_of_order",
        "non_positive_price",
        "non_finite_value",
        "crossed_quote",
        "timestamp_anomaly",
        "stale",
        "gap",
        "missing_sequence",
        "mixed_symbol_or_source",
    }
)


class EdgeDatasetError(ValueError):
    def __init__(self, code: str, detail: tuple[str, ...] = ()) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {','.join(detail)}")


@dataclass(frozen=True, slots=True)
class Provenance:
    data_class: DataClass
    source: str
    retrieved_at: datetime
    timezone: str
    tool_version: str
    # Opaque, adapter-supplied capability facts (tick size, digits, contract size, ...).
    # Never hard-coded here; may be empty when the source did not provide them.
    capability_profile: tuple[tuple[str, str], ...] = ()
    capability_profile_ref: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QualityReport:
    total: int
    counts: tuple[tuple[str, int], ...]
    quoted_spread_events: int
    first_issue_index: tuple[tuple[str, int], ...]

    def count(self, code: str) -> int:
        return dict(self.counts).get(code, 0)

    def blocking(self, accepted: frozenset[str] = frozenset()) -> tuple[str, ...]:
        return tuple(
            sorted(c for c, n in self.counts if n > 0 and c in BLOCKING_CODES and c not in accepted)
        )


def audit_events(
    events: Sequence[NormalizedPriceEvent],
    *,
    max_latency: timedelta,
    expected_gap: timedelta | None = None,
) -> QualityReport:
    """Report quality issues without raising, so dirty data can be inspected and counted.

    `max_latency` bounds `received_at - event_time`. For completed bars `event_time` is the
    bar OPEN, so callers must allow the bar length plus a tolerance (e.g. 5m bars: 5m30s).
    `expected_gap` flags consecutive event_time jumps above it (None disables the check).
    """
    counts: dict[str, int] = {}
    first: dict[str, int] = {}

    def note(code: str, index: int) -> None:
        counts[code] = counts.get(code, 0) + 1
        first.setdefault(code, index)

    seen: set[str] = set()
    previous: NormalizedPriceEvent | None = None
    spread_events = 0
    for index, e in enumerate(events):
        aware = all(
            t.tzinfo is not None and t.utcoffset() is not None
            for t in (e.event_time, e.received_at)
        )
        if not aware or e.received_at < e.event_time:
            note("timestamp_anomaly", index)
        elif e.received_at - e.event_time > max_latency:
            note("stale", index)
        if e.identity_key in seen or e.is_duplicate:
            note("duplicate", index)
        seen.add(e.identity_key)
        if e.is_out_of_order:
            note("out_of_order", index)
        numeric = (e.price, e.bid, e.ask, e.last, e.open_price, e.high, e.low, e.close)
        finite = all(v.is_finite() for v in numeric if v is not None)
        if not finite:
            # NaN/inf must be counted, never compared: Decimal ordering on NaN raises.
            note("non_finite_value", index)
        elif e.price <= 0:
            note("non_positive_price", index)
        if finite and e.bid is not None and e.ask is not None:
            if e.bid > e.ask:
                note("crossed_quote", index)
            else:
                spread_events += 1
        if e.is_gap:
            note("gap", index)
        if previous is not None and aware:
            if e.symbol != previous.symbol or e.source != previous.source:
                note("mixed_symbol_or_source", index)
            if e.source_sequence <= previous.source_sequence or e.event_time < previous.event_time:
                note("out_of_order", index)
            elif e.source_sequence - previous.source_sequence > 1:
                note("missing_sequence", index)
            if expected_gap is not None and e.event_time - previous.event_time > expected_gap:
                note("gap", index)
        if aware:  # a naive timestamp can't be ordered against its neighbours
            previous = e
    return QualityReport(
        total=len(events),
        counts=tuple(sorted(counts.items())),
        quoted_spread_events=spread_events,
        first_issue_index=tuple(sorted(first.items())),
    )


@dataclass(frozen=True, slots=True)
class EdgeDatasetManifest:
    base: DatasetManifest
    base_hash: str
    provenance: Provenance
    quality: QualityReport
    max_latency_seconds: int
    expected_gap_seconds: int | None
    accepted_issues: tuple[str, ...]

    @property
    def manifest_hash(self) -> str:
        return canonical_hash(self)

    @property
    def statistical_evidence_eligible(self) -> bool:
        """Necessary, not sufficient, for a statistical claim: the data must be declared
        real-market AND pass the attestation checks in `build_edge_manifest`. The class is
        still a declaration; a reviewer must confirm the source. Synthetic fixtures are
        engineering inputs and never eligible."""
        return self.provenance.data_class == "real_market"


def _check_real_market_attestation(
    events: Sequence[NormalizedPriceEvent], provenance: Provenance
) -> None:
    """Defence in depth against mislabelling. It cannot prove data is real; it rejects the
    obvious cases: no adapter-supplied capability reference, or synthetic markers in the
    provenance source or in any event's source / identity."""
    if not (provenance.capability_profile_ref or "").strip():
        raise EdgeDatasetError("real_market_requires_capability_ref")
    markers = [provenance.source, *(e.source for e in events)]
    markers += [e.identity_key for e in events] + [e.source_event_id for e in events]
    if any("synthetic" in m.lower() or "fixture" in m.lower() for m in markers):
        raise EdgeDatasetError("synthetic_marker_in_real_market_data")


def build_edge_manifest(
    events: tuple[NormalizedPriceEvent, ...],
    provenance: Provenance,
    *,
    max_latency: timedelta,
    expected_gap: timedelta | None = None,
    accepted_issues: frozenset[str] = frozenset(),
) -> EdgeDatasetManifest:
    """Fail closed: blocking issues that are not explicitly accepted raise."""
    if not events:
        raise EdgeDatasetError("empty_dataset")
    if provenance.timezone != "UTC":
        raise EdgeDatasetError("non_utc_timezone")
    if provenance.data_class == "real_market":
        _check_real_market_attestation(events, provenance)
    unknown_accepted = accepted_issues - BLOCKING_CODES
    if unknown_accepted:
        raise EdgeDatasetError("unknown_accepted_issue", tuple(sorted(unknown_accepted)))
    report = audit_events(events, max_latency=max_latency, expected_gap=expected_gap)
    blocking = report.blocking(accepted_issues)
    if blocking:
        raise EdgeDatasetError("dataset_quality_blocking", blocking)
    # Duplicates / disorder accepted in the manifest would still break the base contract.
    try:
        base = manifest_for(events, quality="partial" if accepted_issues else "complete")
    except ValueError as exc:
        raise EdgeDatasetError(str(exc)) from exc
    return EdgeDatasetManifest(
        base=base,
        base_hash=canonical_hash(base),
        provenance=provenance,
        quality=report,
        max_latency_seconds=int(max_latency.total_seconds()),
        expected_gap_seconds=None if expected_gap is None else int(expected_gap.total_seconds()),
        accepted_issues=tuple(sorted(accepted_issues)),
    )


def verify_edge_dataset(
    manifest: EdgeDatasetManifest,
    events: tuple[NormalizedPriceEvent, ...],
    *,
    expected_manifest_hash: str,
) -> None:
    """Recompute every hash and the quality report; any mismatch raises."""
    if manifest.manifest_hash != expected_manifest_hash:
        raise EdgeDatasetError("manifest_hash_mismatch")
    if canonical_hash(manifest.base) != manifest.base_hash:
        raise EdgeDatasetError("base_hash_mismatch")
    try:
        verify_events(manifest.base, events)
    except ValueError as exc:
        raise EdgeDatasetError(str(exc)) from exc
    gap = (
        None
        if manifest.expected_gap_seconds is None
        else timedelta(seconds=manifest.expected_gap_seconds)
    )
    report = audit_events(
        events, max_latency=timedelta(seconds=manifest.max_latency_seconds), expected_gap=gap
    )
    if report != manifest.quality:
        raise EdgeDatasetError("quality_report_mismatch")
