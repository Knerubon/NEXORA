"""ENGINEERING tests (synthetic events): dataset manifest, provenance, integrity, quality."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from nexora.edge import (
    EdgeDatasetError,
    Provenance,
    audit_events,
    build_edge_manifest,
    verify_edge_dataset,
)
from nexora.edge.dataset import DataClass
from nexora.market_data.models import NormalizedPriceEvent

from tests.edge_fixtures import BAR, T0, flat_bars, make_bar

# bars: event_time is the bar open, so allow the 5m bar length plus 30s tolerance
LATENCY = BAR + timedelta(seconds=30)


def prov(data_class: DataClass = "synthetic_fixture", tz: str = "UTC") -> Provenance:
    return Provenance(
        data_class=data_class,
        source="synthetic-test",
        retrieved_at=datetime(2026, 1, 6, tzinfo=UTC),
        timezone=tz,
        tool_version="edge-test-1",
        capability_profile=(("digits", "2"),),
    )


def test_clean_dataset_builds_verifies_and_is_deterministic() -> None:
    events = flat_bars(20)
    a = build_edge_manifest(events, prov(), max_latency=LATENCY, expected_gap=BAR)
    b = build_edge_manifest(events, prov(), max_latency=LATENCY, expected_gap=BAR)
    assert a == b and a.manifest_hash == b.manifest_hash
    assert a.quality.total == 20 and a.quality.blocking() == ()
    verify_edge_dataset(a, events, expected_manifest_hash=a.manifest_hash)


def test_no_label_makes_a_dataset_statistical_evidence() -> None:
    synthetic = build_edge_manifest(flat_bars(5), prov(), max_latency=LATENCY)
    assert not synthetic.statistical_evidence_eligible and not synthetic.declared_real_market
    real_events = tuple(
        replace(
            e,
            source="adapter-feed",
            identity_key=f"feed:{i}",
            source_event_id=f"feed:{i}",
        )
        for i, e in enumerate(flat_bars(5))
    )
    real_prov = replace(
        prov("real_market"), source="adapter-feed", capability_profile_ref="profile:abc"
    )
    real = build_edge_manifest(real_events, real_prov, max_latency=LATENCY)
    # The label is recorded, but it can never make the data eligible (nexora.edge.evidence).
    assert real.declared_real_market
    assert not real.statistical_evidence_eligible


def test_provenance_change_changes_manifest_hash() -> None:
    events = flat_bars(5)
    a = build_edge_manifest(events, prov(), max_latency=LATENCY)
    b = build_edge_manifest(
        events, replace(prov(), tool_version="edge-test-2"), max_latency=LATENCY
    )
    assert a.manifest_hash != b.manifest_hash and a.base_hash == b.base_hash


def test_tampered_event_detected() -> None:
    events = flat_bars(10)
    m = build_edge_manifest(events, prov(), max_latency=LATENCY)
    tampered = (*events[:4], make_bar(4, "100", "100", "100", "101"), *events[5:])
    with pytest.raises(EdgeDatasetError):
        verify_edge_dataset(m, tampered, expected_manifest_hash=m.manifest_hash)


def test_dropped_row_detected() -> None:
    events = flat_bars(10)
    m = build_edge_manifest(events, prov(), max_latency=LATENCY)
    with pytest.raises(EdgeDatasetError):
        verify_edge_dataset(m, events[:-1], expected_manifest_hash=m.manifest_hash)


def test_wrong_expected_manifest_hash_rejected() -> None:
    events = flat_bars(5)
    m = build_edge_manifest(events, prov(), max_latency=LATENCY)
    with pytest.raises(EdgeDatasetError, match="manifest_hash_mismatch"):
        verify_edge_dataset(m, events, expected_manifest_hash="0" * 64)


def test_audit_reports_dirty_data_without_raising() -> None:
    bars = [make_bar(i, "100", "100", "100", "100") for i in range(6)]
    bars.append(make_bar(5, "100", "100", "100", "100"))  # duplicate identity + sequence
    bars.append(make_bar(8, "100", "100", "100", "100"))  # sequence jump
    report = audit_events(bars, max_latency=LATENCY, expected_gap=BAR)
    assert report.count("duplicate") == 1
    assert report.count("out_of_order") >= 1
    assert report.count("missing_sequence") == 1
    assert report.count("gap") >= 1
    assert report.total == 8
    assert set(report.blocking()) >= {"duplicate", "missing_sequence", "gap"}


def _blocked(events: Sequence[NormalizedPriceEvent], code: str, **kw: Any) -> None:
    with pytest.raises(EdgeDatasetError) as err:
        build_edge_manifest(tuple(events), prov(), max_latency=LATENCY, **kw)
    assert code in err.value.detail or err.value.code == code


def test_duplicate_rows_fail_closed() -> None:
    bars = [*flat_bars(4), make_bar(3, "100", "100", "100", "100")]
    _blocked(bars, "duplicate")


def test_out_of_order_rows_fail_closed() -> None:
    bars = [*flat_bars(3), make_bar(4, "1", "1", "1", "1"), make_bar(3, "1", "1", "1", "1")]
    _blocked(bars, "out_of_order")


def test_stale_rows_fail_closed() -> None:
    bars = [*flat_bars(3), make_bar(3, "100", "100", "100", "100", latency=timedelta(minutes=5))]
    _blocked(bars, "stale")


def test_missing_sequence_fails_closed() -> None:
    bars = [*flat_bars(3), make_bar(3, "100", "100", "100", "100", seq=9)]
    _blocked(bars, "missing_sequence")


def test_time_gap_fails_closed_when_expected_gap_given() -> None:
    bars = [*flat_bars(3), make_bar(7, "100", "100", "100", "100", seq=3)]
    _blocked(bars, "gap", expected_gap=BAR)


def test_crossed_quote_fails_closed() -> None:
    bars = [*flat_bars(2), make_bar(2, "100", "100", "100", "100", bid="101", ask="100")]
    _blocked(bars, "crossed_quote")


def test_non_positive_price_fails_closed() -> None:
    bars = [*flat_bars(2), make_bar(2, "0", "0", "0", "0")]
    _blocked(bars, "non_positive_price")


def test_received_before_event_is_a_timestamp_anomaly() -> None:
    good = flat_bars(2)
    bad = replace(make_bar(2, "100", "100", "100", "100"), received_at=T0 - timedelta(days=1))
    _blocked([*good, bad], "timestamp_anomaly")


def test_naive_timestamp_is_a_timestamp_anomaly() -> None:
    bad = replace(make_bar(1, "100", "100", "100", "100"), event_time=datetime(2026, 1, 5))
    report = audit_events([flat_bars(1)[0], bad], max_latency=LATENCY)
    assert report.count("timestamp_anomaly") == 1


def test_mixed_symbol_fails_closed() -> None:
    other = replace(make_bar(2, "100", "100", "100", "100"), symbol="OTHER")
    _blocked([*flat_bars(2), other], "mixed_symbol_or_source")


def test_issue_can_be_accepted_explicitly_and_is_recorded() -> None:
    bars = [*flat_bars(3), make_bar(7, "100", "100", "100", "100", seq=3)]
    m = build_edge_manifest(
        tuple(bars),
        prov(),
        max_latency=LATENCY,
        expected_gap=BAR,
        accepted_issues=frozenset({"gap"}),
    )
    assert m.accepted_issues == ("gap",)
    assert m.quality.count("gap") == 1
    assert m.base.quality_status == "partial"
    verify_edge_dataset(m, tuple(bars), expected_manifest_hash=m.manifest_hash)


def test_unknown_accepted_issue_rejected() -> None:
    with pytest.raises(EdgeDatasetError, match="unknown_accepted_issue"):
        build_edge_manifest(
            flat_bars(3), prov(), max_latency=LATENCY, accepted_issues=frozenset({"bogus"})
        )


def test_empty_dataset_and_non_utc_provenance_rejected() -> None:
    with pytest.raises(EdgeDatasetError, match="empty_dataset"):
        build_edge_manifest((), prov(), max_latency=LATENCY)
    with pytest.raises(EdgeDatasetError, match="non_utc_timezone"):
        build_edge_manifest(flat_bars(3), prov(tz="Asia/Bangkok"), max_latency=LATENCY)


def test_quoted_spread_events_are_counted() -> None:
    bars = (
        make_bar(0, "100", "100", "100", "100", bid="99.9", ask="100.1"),
        make_bar(1, "100", "100", "100", "100"),
    )
    assert audit_events(bars, max_latency=LATENCY).quoted_spread_events == 1


def test_missing_bid_ask_is_not_fabricated() -> None:
    report = audit_events(flat_bars(5), max_latency=LATENCY)
    assert report.quoted_spread_events == 0
    assert Decimal("0") == Decimal(report.quoted_spread_events)
