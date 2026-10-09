"""SYNTHETIC fixtures for edge-validation ENGINEERING tests only.

Nothing built here is market data. Results computed from it test code correctness and must
never be presented as evidence of trading profitability.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.edge.dataset import (
    EdgeDatasetManifest,
    Provenance,
    build_edge_manifest,
)
from nexora.market_data.models import NormalizedPriceEvent

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
BAR = timedelta(minutes=5)


def make_bar(
    i: int,
    o: str,
    h: str,
    low: str,
    c: str,
    *,
    seq: int | None = None,
    bid: str | None = None,
    ask: str | None = None,
    start: datetime = T0,
    latency: timedelta = timedelta(0),
) -> NormalizedPriceEvent:
    opened = start + BAR * i
    sequence = i if seq is None else seq
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=f"synthetic:{sequence}",
        source="synthetic-test",
        symbol="SYNTH",
        kind="bar",
        event_time=opened,
        received_at=opened + BAR + latency,
        source_sequence=sequence,
        source_order=sequence,
        source_event_id=f"synthetic:{sequence}",
        price_source="close",
        units="SYNTH-UNIT",
        precision=2,
        price=Decimal(c),
        bid=None if bid is None else Decimal(bid),
        ask=None if ask is None else Decimal(ask),
        open_price=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def flat_bars(count: int, price: str = "100") -> tuple[NormalizedPriceEvent, ...]:
    return tuple(make_bar(i, price, price, price, price) for i in range(count))


def synthetic_manifest(
    events: tuple[NormalizedPriceEvent, ...],
) -> tuple[EdgeDatasetManifest, str]:
    """Manifest + its hash for synthetic events (bars: allow bar length + 30s latency)."""
    manifest = build_edge_manifest(
        events,
        Provenance(
            data_class="synthetic_fixture",
            source="synthetic-test",
            retrieved_at=T0,
            timezone="UTC",
            tool_version="edge-test",
        ),
        max_latency=BAR + timedelta(seconds=30),
    )
    return manifest, manifest.manifest_hash
