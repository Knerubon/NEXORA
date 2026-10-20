"""Chunked, write-once extraction plus an immutable manifest and independent verification."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nexora.market_history import SCHEMA_VERSION
from nexora.market_history.convert import TimeConversion, normalize
from nexora.market_history.models import (
    HistoryError,
    RawTick,
    canonical_json,
    decimal_str,
    sha256_hex,
)
from nexora.market_history.provider import HistoricalTickSource, TransientSourceError
from nexora.market_history.quality import QualityConfig, hard_defects, quality_report

EXTRACTOR_VERSION = "nexora.market-history-extractor/1"
MANIFEST_NAME = "manifest.json"
RETRIEVAL_NOTE = (
    "historical_retrieval: retrieved_at is when this extraction ran, not when ticks were "
    "originally "
    "received; it must never be used as point-in-time knowledge time"
)


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    symbol: str
    from_raw_msc: int
    to_raw_msc: int  # exclusive
    chunk_ms: int
    max_retries: int = 3

    def __post_init__(self) -> None:
        if self.to_raw_msc <= self.from_raw_msc or self.chunk_ms <= 0 or self.max_retries < 0:
            raise HistoryError("invalid_extraction_request")


def _encode_tick(t: RawTick) -> str:
    row: dict[str, Any] = {"t": t.time_msc, "b": decimal_str(t.bid), "a": decimal_str(t.ask)}
    if t.flags is not None:
        row["f"] = t.flags
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def read_chunk(path: Path) -> Iterator[RawTick]:
    from decimal import Decimal

    with path.open("rb") as fh:
        for line in fh:
            row = json.loads(line)
            yield RawTick(row["t"], Decimal(row["b"]), Decimal(row["a"]), row.get("f"))


def _windows(req: ExtractionRequest) -> Iterator[tuple[int, int]]:
    a = req.from_raw_msc
    while a < req.to_raw_msc:
        b = min(a + req.chunk_ms, req.to_raw_msc)
        yield a, b
        a = b


def extract(
    source: HistoricalTickSource,
    request: ExtractionRequest,
    conversion: TimeConversion,
    quality: QualityConfig,
    out_dir: Path,
    *,
    retrieved_at: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Pull ticks chunk by chunk, write each chunk once, and write the manifest last.

    Fails closed: refuses to run unless the conversion covers the whole requested raw window,
    never overwrites an existing chunk or manifest, and never edits what the source returned."""
    if retrieved_at.tzinfo is None:
        raise HistoryError("retrieved_at_must_be_tz_aware")
    if not conversion.covers(request.from_raw_msc, request.to_raw_msc):
        raise HistoryError("time_conversion_does_not_cover_window")
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / MANIFEST_NAME).exists():
        raise HistoryError("manifest_exists_dataset_is_immutable")

    instrument = source.instrument(request.symbol)
    profile = source.capability_profile()
    chunks: list[dict[str, Any]] = []
    all_ticks: list[RawTick] = []
    for a, b in _windows(request):
        ticks, attempts = _fetch(source, request, a, b, sleep)
        inside = [t for t in ticks if a <= t.time_msc < b]
        outside = len(ticks) - len(inside)
        body = "".join(_encode_tick(t) + "\n" for t in inside).encode()
        name = f"chunk-{a}-{b}.jsonl"
        path = out_dir / name
        if path.exists():
            if sha256_hex(path.read_bytes()) != sha256_hex(body):
                raise HistoryError("chunk_exists_with_different_content", name)
        else:
            with path.open("xb") as fh:
                fh.write(body)
        chunks.append(
            {
                "file": name,
                "from_raw_msc": a,
                "to_raw_msc": b,
                "tick_count": len(inside),
                "sha256": sha256_hex(body),
                "attempts": attempts,
                "out_of_window_dropped": outside,
                "empty": not inside,
            }
        )
        all_ticks.extend(inside)

    report = quality_report(normalize(all_ticks, conversion), quality)
    defects = hard_defects(report)
    body_for_id: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "symbol": request.symbol,
        "window_raw_msc": [request.from_raw_msc, request.to_raw_msc],
        "chunk_ms": request.chunk_ms,
        "source": source.descriptor().to_json(),
        "capability_profile": profile.to_json(),
        "instrument": instrument.to_json(),
        "time_conversion": conversion.to_json(),
        "chunks": chunks,
        "quality": report,
        "quality_hard_defects": defects,
    }
    dataset_id = sha256_hex(canonical_json(body_for_id))
    manifest = {
        **body_for_id,
        "dataset_id": dataset_id,
        "immutable": True,
        "provenance": {
            "extractor": EXTRACTOR_VERSION,
            "retrieved_at": retrieved_at.isoformat(),
            "retrieval_note": RETRIEVAL_NOTE,
            "orders_submitted": 0,
            "read_only_source_allowlist": True,
        },
    }
    manifest["manifest_sha256"] = sha256_hex(canonical_json(manifest))
    with (out_dir / MANIFEST_NAME).open("xb") as fh:
        fh.write(json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n")
    return manifest


def _fetch(
    source: HistoricalTickSource,
    req: ExtractionRequest,
    a: int,
    b: int,
    sleep: Callable[[float], None],
) -> tuple[list[RawTick], int]:
    attempts = 0
    while True:
        attempts += 1
        try:
            return list(source.ticks(req.symbol, a, b)), attempts
        except TransientSourceError:
            if attempts > req.max_retries:
                raise
            sleep(min(2.0 ** (attempts - 1), 30.0))


def verify_dataset(directory: Path, conversion_check: bool = True) -> list[str]:
    """Independent re-verification from files alone. Returns problems; empty list = consistent.

    Checks manifest self-hash, dataset_id, every chunk hash and count, absence of unlisted files,
    chunk ordering/contiguity, and (re)computes the quality report."""
    problems: list[str] = []
    mpath = directory / MANIFEST_NAME
    if not mpath.exists():
        return ["manifest_missing"]
    manifest = json.loads(mpath.read_text())
    stored = manifest.pop("manifest_sha256", None)
    if stored != sha256_hex(canonical_json(manifest)):
        problems.append("manifest_hash_mismatch")
    body = {k: v for k, v in manifest.items() if k not in ("dataset_id", "immutable", "provenance")}
    if manifest.get("dataset_id") != sha256_hex(canonical_json(body)):
        problems.append("dataset_id_mismatch")
    listed = {c["file"] for c in manifest["chunks"]}
    present = {p.name for p in directory.glob("chunk-*.jsonl")}
    problems += [f"chunk_missing:{n}" for n in sorted(listed - present)]
    problems += [f"chunk_unlisted:{n}" for n in sorted(present - listed)]
    cursor = manifest["window_raw_msc"][0]
    ticks: list[RawTick] = []
    for c in manifest["chunks"]:
        if c["from_raw_msc"] != cursor:
            problems.append(f"chunk_not_contiguous:{c['file']}")
        cursor = c["to_raw_msc"]
        path = directory / c["file"]
        if not path.exists():
            continue
        data = path.read_bytes()
        if sha256_hex(data) != c["sha256"]:
            problems.append(f"chunk_hash_mismatch:{c['file']}")
            continue
        rows = list(read_chunk(path))
        if len(rows) != c["tick_count"]:
            problems.append(f"chunk_count_mismatch:{c['file']}")
        if any(not (c["from_raw_msc"] <= t.time_msc < c["to_raw_msc"]) for t in rows):
            problems.append(f"chunk_tick_outside_window:{c['file']}")
        ticks.extend(rows)
    if cursor != manifest["window_raw_msc"][1]:
        problems.append("chunks_do_not_cover_window")
    if conversion_check and not problems:
        from nexora.market_history.convert import TimeSegment

        conv = TimeConversion(
            tuple(
                TimeSegment(
                    s["from_raw_msc"],
                    s["to_raw_msc"],
                    s["offset_seconds"],
                    s["basis"],
                    s["evidence_ref"],
                )
                for s in manifest["time_conversion"]["segments"]
            )
        )
        recomputed = quality_report(
            normalize(ticks, conv), QualityConfig(manifest["quality"]["config"]["max_gap_ms"])
        )
        if canonical_json(recomputed) != canonical_json(manifest["quality"]):
            problems.append("quality_report_not_reproducible")
    return problems
