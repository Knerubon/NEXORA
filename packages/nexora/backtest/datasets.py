"""Immutable, content-verified event datasets."""

from __future__ import annotations

import json
from pathlib import Path

from nexora.artifacts import canonical_hash, canonical_serialize, decode
from nexora.backtest.models import DatasetManifest, DatasetPartition
from nexora.market_data.models import NormalizedPriceEvent


def manifest_for(
    events: tuple[NormalizedPriceEvent, ...], *, quality: str, parent: str | None = None
) -> DatasetManifest:
    if not events:
        raise ValueError("empty_dataset")
    first = events[0]
    digest = canonical_hash(events)
    return DatasetManifest(
        dataset_id=f"dataset:{digest}",
        parent_dataset_id=parent,
        source=first.source,
        symbol=first.symbol,
        price_source=first.price_source,
        units=first.units,
        timezone="UTC",
        range_start=first.event_time,
        range_end=events[-1].received_at,
        schema_version=1,
        normalizer_version="p2-v1",
        order_policy="source_sequence",
        quality_status=quality,
        quality_snapshot_ref=f"coverage:{quality}",
        partitions=(DatasetPartition("normalized", digest, len(events)),),
    )


def verify_events(manifest: DatasetManifest, events: tuple[NormalizedPriceEvent, ...]) -> None:
    if not events or len(manifest.partitions) != 1 or manifest.partitions[0].name != "normalized":
        raise ValueError("missing_or_unsupported_partitions")
    partition = manifest.partitions[0]
    if partition.rows != len(events) or partition.content_hash != canonical_hash(events):
        raise ValueError("partition_hash_mismatch")
    seen: set[str] = set()
    previous: NormalizedPriceEvent | None = None
    for event in events:
        if (
            event.identity_key in seen
            or event.is_duplicate
            or event.is_out_of_order
            or not event.price.is_finite()
            or event.price <= 0
            or event.symbol != manifest.symbol
            or event.source != manifest.source
            or event.price_source != manifest.price_source
            or event.units != manifest.units
            or event.event_time < manifest.range_start
            or event.received_at > manifest.range_end
            or event.received_at < event.event_time
        ):
            raise ValueError("invalid_dataset_event")
        if previous and (
            event.source_sequence <= previous.source_sequence
            or event.event_time < previous.event_time
            or event.received_at < previous.received_at
        ):
            raise ValueError("dataset_order_violation")
        seen.add(event.identity_key)
        previous = event


def save_dataset(
    directory: Path, manifest: DatasetManifest, events: tuple[NormalizedPriceEvent, ...]
) -> None:
    verify_events(manifest, events)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "normalized.json").write_text(
        json.dumps(canonical_serialize(events), sort_keys=True), encoding="utf-8"
    )
    (directory / "manifest.json").write_text(
        json.dumps(canonical_serialize(manifest), sort_keys=True), encoding="utf-8"
    )


def load_dataset(directory: Path) -> tuple[DatasetManifest, tuple[NormalizedPriceEvent, ...]]:
    manifest = decode(
        DatasetManifest, json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    )
    events = tuple(
        decode(NormalizedPriceEvent, row)
        for row in json.loads((directory / "normalized.json").read_text(encoding="utf-8"))
    )
    verify_events(manifest, events)
    return manifest, events
