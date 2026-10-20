"""Chunked, write-once, crash-safe extraction plus an immutable manifest and verification.

Memory is bounded by one chunk: ticks are written to disk per chunk, then re-read lazily for
the quality pass. Every file is created atomically (temp file + fsync + no-overwrite link), so a
crash can leave only ignorable temp files, never a truncated final file.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nexora.market_history import SCHEMA_VERSION
from nexora.market_history.convert import TimeConversion, TimeSegment, normalize
from nexora.market_history.models import (
    HistoryError,
    NormalizedTick,
    RawTick,
    canonical_json,
    decimal_str,
    sha256_hex,
)
from nexora.market_history.provider import HistoricalTickSource, TransientSourceError
from nexora.market_history.quality import QualityConfig, hard_defects, quality_report

EXTRACTOR_VERSION = "nexora.market-history-extractor/2"
MANIFEST_NAME = "manifest.json"
TMP_MARKER = ".tmp-"
DEFAULT_MAX_CHUNK_TICKS = 1_000_000
RETRIEVAL_NOTE = (
    "historical_retrieval: retrieved_at is when this extraction ran, not when ticks were "
    "originally received; it must never be used as point-in-time knowledge time"
)


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    symbol: str
    from_raw_msc: int
    to_raw_msc: int  # exclusive
    chunk_ms: int
    max_retries: int = 3
    # Memory bound: a chunk returning more ticks than this is refused (use a smaller chunk_ms).
    max_chunk_ticks: int = DEFAULT_MAX_CHUNK_TICKS

    def __post_init__(self) -> None:
        if (
            self.to_raw_msc <= self.from_raw_msc
            or self.chunk_ms <= 0
            or self.max_retries < 0
            or self.max_chunk_ticks < 1
        ):
            raise HistoryError("invalid_extraction_request")


def _encode_tick(t: RawTick) -> str:
    row: dict[str, Any] = {"t": t.time_msc, "b": _quote(t.bid), "a": _quote(t.ask)}
    if t.flags is not None:
        row["f"] = t.flags
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _quote(value: Decimal) -> str:
    """Finite values use the canonical text; non-finite values are PRESERVED (and later counted
    as a hard defect) instead of aborting the extraction or being dropped."""
    return decimal_str(value) if value.is_finite() else str(value)


def read_chunk(path: Path) -> Iterator[RawTick]:
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


# ---- atomic, write-once file creation -------------------------------------------------------
def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # e.g. Windows cannot open directories
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_once(path: Path, data: bytes) -> None:
    """Create `path` with `data` atomically and never overwrite. Raises FileExistsError if present.

    Temp file in the same directory -> fsync -> hard link to the final name (atomic and fails if the
    name exists) -> unlink temp -> read-only. A crash before the link leaves only a temp file."""
    tmp = path.with_name(f"{path.name}{TMP_MARKER}{os.getpid()}")
    with tmp.open("xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    os.chmod(path, 0o444)
    _fsync_dir(path.parent)


def clean_stale_temp(directory: Path) -> int:
    """Remove leftovers of interrupted runs (only names containing the temp marker)."""
    n = 0
    for p in directory.glob(f"*{TMP_MARKER}*"):
        p.unlink(missing_ok=True)
        n += 1
    return n


def _utc_span(conversion: TimeConversion, lo: int, hi: int) -> tuple[int, int]:
    """Smallest UTC interval containing the raw window [lo, hi), segment by segment."""
    points: list[int] = []
    for seg in conversion.segments:
        a = max(lo, seg.from_raw_msc)
        b = hi if seg.to_raw_msc is None else min(hi, seg.to_raw_msc)
        if a < b:
            points += [a - seg.offset_seconds * 1000, (b - 1) - seg.offset_seconds * 1000]
    return min(points), max(points)


def extract(
    source: HistoricalTickSource,
    request: ExtractionRequest,
    conversion: TimeConversion,
    quality: QualityConfig,
    out_dir: Path,
    *,
    retrieved_at: datetime,
    protected_utc_ranges: Sequence[tuple[int, int]] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Pull ticks chunk by chunk, write each chunk once, and write the manifest last.

    Fails closed: refuses when the conversion does not cover the window, when the window overlaps
    a protected (e.g. holdout) UTC range supplied by the caller, and never overwrites a chunk or
    manifest. Nothing the source returned is edited. Resident memory is one chunk."""
    if retrieved_at.tzinfo is None:
        raise HistoryError("retrieved_at_must_be_tz_aware")
    if not conversion.covers(request.from_raw_msc, request.to_raw_msc):
        raise HistoryError("time_conversion_does_not_cover_window")
    lo, hi = _utc_span(conversion, request.from_raw_msc, request.to_raw_msc)
    for p_lo, p_hi in protected_utc_ranges:  # inclusive-exclusive [p_lo, p_hi)
        if lo < p_hi and p_lo <= hi:
            raise HistoryError("window_overlaps_protected_range")
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / MANIFEST_NAME).exists():
        raise HistoryError("manifest_exists_dataset_is_immutable")
    clean_stale_temp(out_dir)

    instrument = source.instrument(request.symbol)
    profile = source.capability_profile()
    chunks: list[dict[str, Any]] = []
    for a, b in _windows(request):
        ticks, attempts = _fetch(source, request, a, b, sleep)
        if len(ticks) > request.max_chunk_ticks:
            raise HistoryError("chunk_exceeds_max_ticks", f"{a}-{b}: reduce chunk_ms")
        inside = [t for t in ticks if a <= t.time_msc < b]
        outside = len(ticks) - len(inside)
        body = "".join(_encode_tick(t) + "\n" for t in inside).encode()
        name = f"chunk-{a}-{b}.jsonl"
        path = out_dir / name
        try:
            write_once(path, body)
        except FileExistsError:
            if _file_sha256(path) != sha256_hex(body):
                raise HistoryError("chunk_exists_with_different_content", name) from None
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
        del ticks, inside, body

    report = quality_report(_normalized_stream(out_dir, chunks, conversion), quality)
    defects = hard_defects(report)
    body_for_id: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "symbol": request.symbol,
        "window_raw_msc": [request.from_raw_msc, request.to_raw_msc],
        "chunk_ms": request.chunk_ms,
        "max_chunk_ticks": request.max_chunk_ticks,
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
    write_once(
        out_dir / MANIFEST_NAME, json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    )
    return manifest


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _normalized_stream(
    directory: Path, chunks: Iterable[dict[str, Any]], conversion: TimeConversion
) -> Iterator[NormalizedTick]:
    for c in chunks:
        yield from normalize(read_chunk(directory / c["file"]), conversion)


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


def conversion_from_manifest(manifest: dict[str, Any]) -> TimeConversion:
    return TimeConversion(
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


def verify_dataset(
    directory: Path,
    conversion_check: bool = True,
    *,
    expected_manifest_sha256: str | None = None,
) -> list[str]:
    """Independent re-verification from files alone, streaming (memory = one line).

    Checks manifest self-hash, optional EXTERNAL anchor, dataset_id, every chunk hash/count/window,
    absence of unlisted files, contiguity, and recomputes the quality report. A self-consistent
    forgery is only detectable through `expected_manifest_sha256` recorded outside the directory."""
    problems: list[str] = []
    mpath = directory / MANIFEST_NAME
    if not mpath.exists():
        return ["manifest_missing"]
    manifest = json.loads(mpath.read_text())
    stored = manifest.pop("manifest_sha256", None)
    if stored != sha256_hex(canonical_json(manifest)):
        problems.append("manifest_hash_mismatch")
    if expected_manifest_sha256 is not None and stored != expected_manifest_sha256:
        problems.append("manifest_anchor_mismatch")
    body = {k: v for k, v in manifest.items() if k not in ("dataset_id", "immutable", "provenance")}
    if manifest.get("dataset_id") != sha256_hex(canonical_json(body)):
        problems.append("dataset_id_mismatch")
    listed = {c["file"] for c in manifest["chunks"]}
    present = {p.name for p in directory.glob("chunk-*.jsonl")}
    problems += [f"chunk_missing:{n}" for n in sorted(listed - present)]
    problems += [f"chunk_unlisted:{n}" for n in sorted(present - listed)]
    problems += [f"stale_temp_file:{p.name}" for p in sorted(directory.glob(f"*{TMP_MARKER}*"))]
    cursor = manifest["window_raw_msc"][0]
    for c in manifest["chunks"]:
        if c["from_raw_msc"] != cursor:
            problems.append(f"chunk_not_contiguous:{c['file']}")
        cursor = c["to_raw_msc"]
        path = directory / c["file"]
        if not path.exists():
            continue
        if _file_sha256(path) != c["sha256"]:
            problems.append(f"chunk_hash_mismatch:{c['file']}")
            continue
        count = 0
        outside = False
        for t in read_chunk(path):
            count += 1
            outside = outside or not (c["from_raw_msc"] <= t.time_msc < c["to_raw_msc"])
        if count != c["tick_count"]:
            problems.append(f"chunk_count_mismatch:{c['file']}")
        if outside:
            problems.append(f"chunk_tick_outside_window:{c['file']}")
    if cursor != manifest["window_raw_msc"][1]:
        problems.append("chunks_do_not_cover_window")
    if conversion_check and not problems:
        conv = conversion_from_manifest(manifest)
        cfg = QualityConfig(manifest["quality"]["config"]["max_gap_ms"])
        recomputed = quality_report(_normalized_stream(directory, manifest["chunks"], conv), cfg)
        if canonical_json(recomputed) != canonical_json(manifest["quality"]):
            problems.append("quality_report_not_reproducible")
    return problems


def usability_blockers(
    directory: Path, *, expected_manifest_sha256: str | None = None
) -> list[str]:
    """Everything that makes a dataset unfit as evidence: verification problems plus hard data
    defects. Gaps are reported by the quality report, not judged here (Quant decision MD-6)."""
    problems = verify_dataset(directory, expected_manifest_sha256=expected_manifest_sha256)
    if problems:
        return problems
    manifest = json.loads((directory / MANIFEST_NAME).read_text())
    return [f"hard_defect:{d}" for d in manifest["quality_hard_defects"]]
