"""Chunked, write-once, crash-safe extraction plus an immutable manifest and verification.

Memory is bounded by one chunk: ticks are written to disk per chunk, then re-read lazily for
the quality pass. Every file is created atomically (temp file + fsync + no-overwrite link), so a
crash can leave only ignorable temp files, never a truncated final file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nexora.market_history import SCHEMA_VERSION
from nexora.market_history.clock import ClockAttestation, check_attestation
from nexora.market_history.convert import TimeConversion, TimeSegment, normalize
from nexora.market_history.guards import (
    overlaps,
    validate_margin,
    validate_protected_ranges,
)
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

EXTRACTOR_VERSION = "nexora.market-history-extractor/3"
CHUNK_NAME = re.compile(r"chunk-(\d+)-(\d+)\.jsonl")
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
        ints = (self.from_raw_msc, self.to_raw_msc, self.chunk_ms, self.max_retries)
        if not all(type(v) is int for v in (*ints, self.max_chunk_ticks)) or not (
            isinstance(self.symbol, str) and self.symbol.strip()
        ):
            raise HistoryError("invalid_extraction_request")
        if (
            self.from_raw_msc < 0
            or self.to_raw_msc <= self.from_raw_msc
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


def preflight(
    request: ExtractionRequest,
    conversion: TimeConversion,
    clock_attestation: ClockAttestation | None,
    protected_utc_ranges: Any,
    protected_margin_ms: Any,
) -> tuple[tuple[tuple[int, int], ...], int]:
    """Every check that needs no provider. Callers (the CLI) run this BEFORE attaching to a
    terminal; `extract` runs it again. Raises HistoryError; returns validated (ranges, margin)."""
    protected = validate_protected_ranges(protected_utc_ranges)
    margin = validate_margin(protected_margin_ms)
    if not conversion.covers(request.from_raw_msc, request.to_raw_msc):
        raise HistoryError("time_conversion_does_not_cover_window")
    check_attestation(clock_attestation, conversion, request.from_raw_msc, request.to_raw_msc)
    lo, hi = _utc_span(conversion, request.from_raw_msc, request.to_raw_msc)
    if overlaps(lo, hi, protected, margin):
        raise HistoryError("window_overlaps_protected_range")
    return protected, margin


def extract(
    source: HistoricalTickSource,
    request: ExtractionRequest,
    conversion: TimeConversion,
    quality: QualityConfig,
    out_dir: Path,
    *,
    retrieved_at: datetime,
    clock_attestation: ClockAttestation | None,
    protected_utc_ranges: Sequence[tuple[int, int]],
    protected_margin_ms: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Pull ticks chunk by chunk, write each chunk once, and write the manifest last.

    ALL refusals below happen before the first call into `source`. `clock_attestation` and
    `protected_utc_ranges` have no defaults: omitting them is an error, and `()` is the explicit
    statement that nothing is protected. Nothing the source returned is edited; resident memory
    is one chunk."""
    if retrieved_at.tzinfo is None:
        raise HistoryError("retrieved_at_must_be_tz_aware")
    protected, margin = preflight(
        request, conversion, clock_attestation, protected_utc_ranges, protected_margin_ms
    )
    assert clock_attestation is not None
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / MANIFEST_NAME).exists():
        raise HistoryError("manifest_exists_dataset_is_immutable")
    clean_stale_temp(out_dir)

    instrument = source.instrument(request.symbol)
    profile = source.capability_profile()
    chunks: list[dict[str, Any]] = []
    content = hashlib.sha256()
    total = 0
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
        content.update(body)
        total += len(inside)
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
    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "symbol": request.symbol,
        "window_raw_msc": [request.from_raw_msc, request.to_raw_msc],
        "time_conversion": conversion.to_json(),
        "clock_attestation": clock_attestation.to_json(),
        "content_sha256": content.hexdigest(),
        "tick_count": total,
        "source": source.descriptor().to_json(),
        "capability_profile": profile.to_json(),
        "instrument": instrument.to_json(),
        "chunk_ms": request.chunk_ms,
        "max_chunk_ticks": request.max_chunk_ticks,
        "chunks": chunks,
        "quality": report,
        "quality_hard_defects": hard_defects(report),
    }
    manifest["dataset_id"] = sha256_hex(canonical_json(_identity(manifest)))
    manifest["immutable"] = True
    manifest["provenance"] = {
        "extractor": EXTRACTOR_VERSION,
        "retrieved_at": retrieved_at.isoformat(),
        "retrieval_note": RETRIEVAL_NOTE,
        "orders_submitted": 0,
        "read_only_source_allowlist": True,
        "protected_utc_ranges": [list(r) for r in protected],
        "protected_margin_ms": margin,
    }
    manifest["manifest_sha256"] = sha256_hex(canonical_json(manifest))
    write_once(
        out_dir / MANIFEST_NAME, json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    )
    return manifest


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    """What the dataset IS: its ticks and how raw time maps to UTC. Retries, chunk layout,
    out-of-window drops, source version, terminal state and instrument metadata (all of which can
    differ between two extractions of the same ticks) are in the manifest hash, not here."""
    return {
        "schema": manifest["schema"],
        "symbol": manifest["symbol"],
        "window_raw_msc": manifest["window_raw_msc"],
        "time_conversion": manifest["time_conversion"],
        "clock_attestation": manifest["clock_attestation"],
        "content_sha256": manifest["content_sha256"],
        "tick_count": manifest["tick_count"],
        "source_kind": manifest["source"]["kind"],
    }


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
            return list(source.ticks(req.symbol, a, b, max_ticks=req.max_chunk_ticks)), attempts
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


@dataclass(frozen=True, slots=True)
class Problem:
    """One structured verification failure. `str(problem)` is the stable text form."""

    code: str
    subject: str = ""
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}:{self.subject}" if self.subject else self.code


_REQUIRED = (
    "schema",
    "symbol",
    "window_raw_msc",
    "time_conversion",
    "clock_attestation",
    "content_sha256",
    "tick_count",
    "source",
    "chunks",
    "quality",
    "quality_hard_defects",
    "dataset_id",
    "provenance",
)
_CHUNK_KEYS = {
    "file": str,
    "from_raw_msc": int,
    "to_raw_msc": int,
    "tick_count": int,
    "sha256": str,
}


def verify_dataset_report(
    directory: Path,
    conversion_check: bool = True,
    *,
    expected_manifest_sha256: str | None = None,
) -> list[Problem]:
    """Independent re-verification from files alone. NEVER raises: malformed manifests, malformed
    chunks and unexpected errors all come back as structured problems (fail closed)."""
    try:
        return _verify(directory, conversion_check, expected_manifest_sha256)
    except Exception as exc:  # defensive: a verifier must not crash on hostile input
        return [Problem("verify_internal_error", type(exc).__name__, str(exc)[:200])]


def verify_dataset(
    directory: Path,
    conversion_check: bool = True,
    *,
    expected_manifest_sha256: str | None = None,
) -> list[str]:
    """Text form of `verify_dataset_report`. Empty list = consistent."""
    report = verify_dataset_report(
        directory, conversion_check, expected_manifest_sha256=expected_manifest_sha256
    )
    return [str(p) for p in report]


def _verify(
    directory: Path, conversion_check: bool, expected_manifest_sha256: str | None
) -> list[Problem]:
    mpath = directory / MANIFEST_NAME
    if not mpath.exists():
        return [Problem("manifest_missing")]
    try:
        manifest = json.loads(mpath.read_text())
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return [Problem("manifest_unreadable", type(exc).__name__)]
    if not isinstance(manifest, dict):
        return [Problem("manifest_malformed", "not_an_object")]
    missing = [k for k in _REQUIRED if k not in manifest]
    if missing:
        return [Problem("manifest_malformed", f"missing_{k}") for k in missing]
    if manifest["schema"] != SCHEMA_VERSION:
        return [Problem("schema_unsupported", str(manifest["schema"])[:60])]
    shape = _check_shape(manifest)
    if shape:
        return shape

    problems: list[Problem] = []
    stored = manifest.pop("manifest_sha256", None)
    if stored != sha256_hex(canonical_json(manifest)):
        problems.append(Problem("manifest_hash_mismatch"))
    if expected_manifest_sha256 is not None and stored != expected_manifest_sha256:
        problems.append(Problem("manifest_anchor_mismatch"))
    if manifest["dataset_id"] != sha256_hex(canonical_json(_identity(manifest))):
        problems.append(Problem("dataset_id_mismatch"))

    chunks = manifest["chunks"]
    listed = {c["file"] for c in chunks}
    present = {p.name for p in directory.glob("chunk-*.jsonl")}
    problems += [Problem("chunk_missing", n) for n in sorted(listed - present)]
    problems += [Problem("chunk_unlisted", n) for n in sorted(present - listed)]
    problems += [
        Problem("stale_temp_file", p.name) for p in sorted(directory.glob(f"*{TMP_MARKER}*"))
    ]

    cursor = manifest["window_raw_msc"][0]
    content = hashlib.sha256()
    total = 0
    parse_ok = True
    for c in chunks:
        name = c["file"]
        m = CHUNK_NAME.fullmatch(name)
        if not m or (int(m.group(1)), int(m.group(2))) != (c["from_raw_msc"], c["to_raw_msc"]):
            problems.append(Problem("chunk_name_invalid", name[:80]))
            parse_ok = False
            continue
        if c["from_raw_msc"] != cursor:
            problems.append(Problem("chunk_not_contiguous", name))
        cursor = c["to_raw_msc"]
        path = directory / name
        if not path.is_file() or path.is_symlink():
            parse_ok = False
            if path.is_symlink():
                problems.append(Problem("chunk_is_symlink", name))
            continue
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
                content.update(block)
        if digest.hexdigest() != c["sha256"]:
            problems.append(Problem("chunk_hash_mismatch", name))
            parse_ok = False
            continue
        count, outside, error = _scan_chunk(path, c["from_raw_msc"], c["to_raw_msc"])
        total += count
        if error:
            problems.append(Problem("chunk_malformed", name, error))
            parse_ok = False
            continue
        if count != c["tick_count"]:
            problems.append(Problem("chunk_count_mismatch", name))
        if outside:
            problems.append(Problem("chunk_tick_outside_window", name))
    if cursor != manifest["window_raw_msc"][1]:
        problems.append(Problem("chunks_do_not_cover_window"))
    if parse_ok and not problems:
        if content.hexdigest() != manifest["content_sha256"]:
            problems.append(Problem("content_sha256_mismatch"))
        if total != manifest["tick_count"]:
            problems.append(Problem("tick_count_mismatch"))

    try:
        conv = conversion_from_manifest(manifest)
        att = ClockAttestation.from_json(manifest["clock_attestation"])
        check_attestation(att, conv, *manifest["window_raw_msc"])
    except (HistoryError, KeyError, TypeError, ValueError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        problems.append(Problem("clock_attestation_invalid", code))
        return problems
    try:
        prot = validate_protected_ranges(manifest["provenance"].get("protected_utc_ranges"))
        margin = validate_margin(manifest["provenance"].get("protected_margin_ms"))
    except HistoryError as exc:
        problems.append(Problem("protected_ranges_invalid", exc.code))
        return problems
    lo, hi = _utc_span(conv, *manifest["window_raw_msc"])
    if overlaps(lo, hi, prot, margin):
        problems.append(Problem("window_overlaps_recorded_protected_range"))

    if conversion_check and parse_ok and not problems:
        try:
            cfg = QualityConfig(manifest["quality"]["config"]["max_gap_ms"])
            recomputed = quality_report(_normalized_stream(directory, chunks, conv), cfg)
        except (HistoryError, KeyError, TypeError, ValueError) as exc:
            problems.append(Problem("quality_recompute_failed", type(exc).__name__))
        else:
            if canonical_json(recomputed) != canonical_json(manifest["quality"]):
                problems.append(Problem("quality_report_not_reproducible"))
    return problems


def _check_shape(manifest: dict[str, Any]) -> list[Problem]:
    out: list[Problem] = []
    w = manifest["window_raw_msc"]
    if not (isinstance(w, list) and len(w) == 2 and all(type(x) is int for x in w)):
        out.append(Problem("manifest_malformed", "window_raw_msc"))
    if not isinstance(manifest["chunks"], list) or not manifest["chunks"]:
        out.append(Problem("manifest_malformed", "chunks"))
    else:
        for i, c in enumerate(manifest["chunks"]):
            if not isinstance(c, dict) or any(
                type(c.get(k)) is not t for k, t in _CHUNK_KEYS.items()
            ):
                out.append(Problem("chunk_entry_malformed", str(i)))
    for key, kind in (
        ("content_sha256", str),
        ("tick_count", int),
        ("dataset_id", str),
        ("symbol", str),
        ("time_conversion", dict),
        ("clock_attestation", dict),
        ("source", dict),
        ("quality", dict),
        ("provenance", dict),
        ("quality_hard_defects", list),
    ):
        if type(manifest[key]) is not kind:
            out.append(Problem("manifest_malformed", key))
    if not out and not isinstance(manifest["source"].get("kind"), str):
        out.append(Problem("manifest_malformed", "source.kind"))
    return out


def _scan_chunk(path: Path, lo: int, hi: int) -> tuple[int, bool, str]:
    """(rows, any row outside [lo, hi), error text). Streams; a bad row stops the scan."""
    count = 0
    outside = False
    try:
        with path.open("rb") as fh:
            for lineno, line in enumerate(fh, 1):
                row = json.loads(line)
                t = RawTick(row["t"], Decimal(row["b"]), Decimal(row["a"]), row.get("f"))
                if type(t.time_msc) is not int or (
                    t.flags is not None and type(t.flags) is not int
                ):
                    return count, outside, f"line {lineno}: bad field type"
                count += 1
                outside = outside or not (lo <= t.time_msc < hi)
    except (ValueError, KeyError, TypeError, ArithmeticError, UnicodeDecodeError) as exc:
        return count, outside, f"line {count + 1}: {type(exc).__name__}"
    return count, outside, ""


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
