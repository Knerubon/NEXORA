"""Edge-validation dataset manifest: provenance declaration, integrity hashes, quality audit.

Builds on the existing `nexora.backtest` DatasetManifest and `verify_events`. Missing market
data is reported, never filled in.

Verification means INTEGRITY, not authenticity. `verify_edge_dataset` re-applies the same
acceptance policy that `build_edge_manifest` enforces, so a hand-forged or edited manifest
cannot pass under weaker rules than a built one. It cannot establish that the data came from
the declared market source; see `nexora.edge.evidence`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from nexora.artifacts import canonical_hash
from nexora.backtest.datasets import manifest_for, verify_events
from nexora.backtest.models import DatasetManifest
from nexora.edge.evidence import EvidenceGates, evaluate_gates
from nexora.market_data.models import NormalizedPriceEvent

DataClass = Literal["synthetic_fixture", "real_market"]
_MICROSECOND = timedelta(microseconds=1)

# Every issue the audit can raise. Any of them blocks a dataset unless it is also listed in
# ACCEPTABLE_CODES AND explicitly accepted in the manifest.
BLOCKING_CODES = frozenset(
    {
        "duplicate",
        "out_of_order",
        "non_positive_price",
        "non_finite_value",
        "invalid_ohlc",
        "crossed_quote",
        "timestamp_anomaly",
        "stale",
        "gap",
        "missing_sequence",
        "mixed_symbol_or_source",
    }
)
# Conditions that describe MISSING or LATE data and may be knowingly accepted. Everything else
# (corrupt, duplicated, mis-ordered or inconsistent records) can never be accepted.
ACCEPTABLE_CODES = frozenset({"gap", "missing_sequence", "stale"})


class EdgeDatasetError(ValueError):
    def __init__(self, code: str, detail: tuple[str, ...] = ()) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {','.join(detail)}")


@dataclass(frozen=True, slots=True)
class Provenance:
    """What the caller DECLARES about the data. None of it is verified here."""

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

    def present(self) -> frozenset[str]:
        return frozenset(c for c, n in self.counts if n > 0)

    def blocking(self, accepted: frozenset[str] = frozenset()) -> tuple[str, ...]:
        return tuple(sorted(c for c in self.present() if c in BLOCKING_CODES and c not in accepted))


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
    rows = tuple(events)  # a one-shot iterator must not be consumed twice
    counts: dict[str, int] = {}
    first: dict[str, int] = {}

    def note(code: str, index: int) -> None:
        counts[code] = counts.get(code, 0) + 1
        first.setdefault(code, index)

    seen: set[str] = set()
    previous: NormalizedPriceEvent | None = None
    spread_events = 0
    for index, e in enumerate(rows):
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
        elif any(v <= 0 for v in numeric if v is not None):
            note("non_positive_price", index)
        if finite and e.bid is not None and e.ask is not None:
            if e.bid > e.ask:
                note("crossed_quote", index)
            else:
                spread_events += 1
        if e.kind == "bar":
            o, h, lo, c = e.open_price, e.high, e.low, e.close
            if o is None or h is None or lo is None or c is None:
                note("invalid_ohlc", index)
            elif finite and not (lo <= min(o, c) and max(o, c) <= h):
                note("invalid_ohlc", index)
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
        total=len(rows),
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
    # Durations are stored in whole microseconds so the policy is re-applied exactly.
    max_latency_us: int
    expected_gap_us: int | None
    accepted_issues: tuple[str, ...]

    @property
    def manifest_hash(self) -> str:
        return canonical_hash(self)

    @property
    def declared_real_market(self) -> bool:
        """The caller's LABEL only. It is not evidence that the data is real."""
        return self.provenance.data_class == "real_market"

    def evidence_gates(self, *, dataset_integrity_verified: bool) -> EvidenceGates:
        return evaluate_gates(dataset_integrity_verified=dataset_integrity_verified)

    @property
    def statistical_evidence_eligible(self) -> bool:
        """Always False while no independent provenance or holdout-authorization mechanism
        exists (`nexora.edge.evidence`). A real-market label cannot change this."""
        return self.evidence_gates(dataset_integrity_verified=False).statistical_evidence_eligible


def _check_real_market_attestation(
    events: Sequence[NormalizedPriceEvent], provenance: Provenance
) -> None:
    """Defence in depth against careless mislabelling. It cannot prove data is real; it
    rejects the obvious cases: no adapter-supplied capability reference, or synthetic markers
    in the provenance source or in any event's source / identity."""
    if not (provenance.capability_profile_ref or "").strip():
        raise EdgeDatasetError("real_market_requires_capability_ref")
    markers = [provenance.source, *(e.source for e in events)]
    markers += [e.identity_key for e in events] + [e.source_event_id for e in events]
    if any("synthetic" in m.lower() or "fixture" in m.lower() for m in markers):
        raise EdgeDatasetError("synthetic_marker_in_real_market_data")


def _to_us(value: timedelta) -> int:
    return value // _MICROSECOND


def apply_dataset_policy(
    events: Sequence[NormalizedPriceEvent],
    provenance: Provenance,
    *,
    max_latency: timedelta,
    expected_gap: timedelta | None,
    accepted_issues: frozenset[str],
) -> QualityReport:
    """The complete acceptance policy. Used by BOTH build and verify so they cannot diverge.

    Raises `EdgeDatasetError` on any violation; returns the quality report when acceptable."""
    rows = tuple(events)
    if not rows:
        raise EdgeDatasetError("empty_dataset")
    if provenance.timezone != "UTC":
        raise EdgeDatasetError("non_utc_timezone")
    if max_latency <= timedelta(0) or (expected_gap is not None and expected_gap <= timedelta(0)):
        raise EdgeDatasetError("invalid_policy_duration")
    unknown = accepted_issues - BLOCKING_CODES
    if unknown:
        raise EdgeDatasetError("unknown_accepted_issue", tuple(sorted(unknown)))
    never = accepted_issues - ACCEPTABLE_CODES
    if never:
        raise EdgeDatasetError("issue_not_acceptable", tuple(sorted(never)))
    if provenance.data_class == "real_market":
        _check_real_market_attestation(rows, provenance)
    report = audit_events(rows, max_latency=max_latency, expected_gap=expected_gap)
    blocking = report.blocking(accepted_issues)
    if blocking:
        raise EdgeDatasetError("dataset_quality_blocking", blocking)
    absent = accepted_issues - report.present()
    if absent:
        # Accepting a condition that is not there would pre-authorize a future problem.
        raise EdgeDatasetError("accepted_issue_not_present", tuple(sorted(absent)))
    return report


def build_edge_manifest(
    events: tuple[NormalizedPriceEvent, ...],
    provenance: Provenance,
    *,
    max_latency: timedelta,
    expected_gap: timedelta | None = None,
    accepted_issues: frozenset[str] = frozenset(),
) -> EdgeDatasetManifest:
    """Fail closed: any policy violation raises."""
    rows = tuple(events)
    report = apply_dataset_policy(
        rows,
        provenance,
        max_latency=max_latency,
        expected_gap=expected_gap,
        accepted_issues=accepted_issues,
    )
    try:
        base = manifest_for(rows, quality="partial" if accepted_issues else "complete")
    except ValueError as exc:
        raise EdgeDatasetError(str(exc)) from exc
    return EdgeDatasetManifest(
        base=base,
        base_hash=canonical_hash(base),
        provenance=provenance,
        quality=report,
        max_latency_us=_to_us(max_latency),
        expected_gap_us=None if expected_gap is None else _to_us(expected_gap),
        accepted_issues=tuple(sorted(accepted_issues)),
    )


def verify_edge_dataset(
    manifest: EdgeDatasetManifest,
    events: Sequence[NormalizedPriceEvent],
    *,
    expected_manifest_hash: str,
) -> None:
    """Re-verify integrity AND re-apply the full acceptance policy; any violation raises.

    `expected_manifest_hash` must come from a record independent of the manifest itself; a
    manifest cannot vouch for its own hash. Passing proves integrity only, never authenticity."""
    rows = tuple(events)
    if manifest.manifest_hash != expected_manifest_hash:
        raise EdgeDatasetError("manifest_hash_mismatch")
    if canonical_hash(manifest.base) != manifest.base_hash:
        raise EdgeDatasetError("base_hash_mismatch")
    try:
        verify_events(manifest.base, rows)
    except ValueError as exc:
        raise EdgeDatasetError(str(exc)) from exc

    accepted_tuple = manifest.accepted_issues
    accepted = frozenset(accepted_tuple)
    if accepted_tuple != tuple(sorted(accepted)):
        raise EdgeDatasetError("invalid_accepted_issues_state")
    expected_status = "partial" if accepted else "complete"
    if manifest.base.quality_status != expected_status:
        raise EdgeDatasetError("quality_status_mismatch")
    gap = (
        None
        if manifest.expected_gap_us is None
        else timedelta(microseconds=manifest.expected_gap_us)
    )
    report = apply_dataset_policy(
        rows,
        manifest.provenance,
        max_latency=timedelta(microseconds=manifest.max_latency_us),
        expected_gap=gap,
        accepted_issues=accepted,
    )
    if report != manifest.quality:
        raise EdgeDatasetError("quality_report_mismatch")
