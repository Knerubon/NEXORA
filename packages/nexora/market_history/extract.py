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
import socket
import stat
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO

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

EXTRACTOR_VERSION = "nexora.market-history-extractor/4"
CHUNK_NAME = re.compile(r"chunk-(\d+)-(\d+)\.jsonl")
MANIFEST_NAME = "manifest.json"
TMP_MARKER = ".tmp-"
LOCK_NAME = ".writer.lock"
ANCHOR_SCHEMA = "nexora.market-history-anchor/1"
# Every dataset is born UNTRUSTED. The manifest can never grant trust: TRUSTED is only ever the
# result of `dataset_status` against an independently stored anchor.
DEVELOPMENT_STATUS = "UNTRUSTED"
# Instrument facts that define what a price/quantity MEANS. They are bound into `dataset_id`.
# Trading-rule fields (swaps, volume limits, stop levels) are not: they churn without changing
# what the ticks are. Everything is still covered by `manifest_sha256` and the external anchor.
UNIT_FIELDS = (
    "digits",
    "point",
    "trade_tick_size",
    "trade_tick_value",
    "trade_tick_value_profit",
    "trade_tick_value_loss",
    "trade_contract_size",
    "currency_base",
    "currency_profit",
    "currency_margin",
)
TIME_UNIT = "utc_msc: integer milliseconds since the Unix epoch, derived only via time_conversion"
PRICE_UNIT = "decimal string of the source's float exactly as received; quote currency per unit"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # never hang on a FIFO swapped in after the check
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


def _kind(path: Path) -> str:
    """'missing' | 'symlink' | 'regular' | 'other' without following links."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "regular" if stat.S_ISREG(mode) else "other"


def _open_regular(path: Path) -> BinaryIO:
    """Open a regular file for reading, never through a symlink (O_NOFOLLOW + fstat, so a swap
    between check and open cannot redirect the read). Raises HistoryError otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
    except OSError as exc:
        raise HistoryError("file_unreadable", f"{path.name}: {type(exc).__name__}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or _kind(path) != "regular":
            raise HistoryError("not_a_regular_file", path.name)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def read_chunk(path: Path) -> Iterator[RawTick]:
    with _open_regular(path) as fh:
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
    tmp = path.with_name(f"{path.name}{TMP_MARKER}{os.getpid()}-{os.urandom(4).hex()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    except FileExistsError:
        raise HistoryError("temp_file_collision", tmp.name) from None
    with os.fdopen(fd, "wb") as fh:
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
    """Remove leftovers of interrupted runs (only names containing the temp marker). Callers hold
    the writer lock, so no live writer can own any of these."""
    n = 0
    for p in directory.glob(f"*{TMP_MARKER}*"):
        if _kind(p) == "missing":
            continue
        p.unlink(missing_ok=True)
        n += 1
    return n


# ---- single-writer ownership ---------------------------------------------------------------
def _pid_alive(pid: int) -> bool | None:
    """True/False when known, None when this platform cannot tell (treated as alive)."""
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    if os.name == "posix":  # os.kill(pid, 0) on Windows would TERMINATE the process
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    return None


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        with _open_regular(path) as fh:
            data = json.loads(fh.read(4096))
    except (HistoryError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _reap_stale_lock(path: Path) -> bool:
    """Remove a lock only when it is provably dead: same host and the owner pid is gone. Anything
    unreadable, foreign or ambiguous is left for the operator (fail closed). The rename is the
    atomic claim: of two racing reapers exactly one wins."""
    info = _read_lock(path)
    if info is None or info.get("host") != socket.gethostname():
        return False
    pid = info.get("pid")
    if type(pid) is not int or _pid_alive(pid) is not False:
        return False
    grave = path.with_name(f"{LOCK_NAME}.dead-{os.urandom(4).hex()}")
    try:
        os.rename(path, grave)
    except OSError:
        return False
    grave.unlink(missing_ok=True)
    return True


def acquire_writer_lock(directory: Path) -> str:
    """Exclusive ownership of an output folder (O_EXCL lock file with pid/host/token).
    Returns the token needed to release it."""
    token = os.urandom(8).hex()
    body = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "token": token}).encode()
    path = directory / LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644)
        except FileExistsError:
            if _reap_stale_lock(path):
                continue
            raise HistoryError("writer_lock_held", LOCK_NAME) from None
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        return token
    raise HistoryError("writer_lock_held", LOCK_NAME)


def release_writer_lock(directory: Path, token: str) -> None:
    """Remove the lock only if it is still ours."""
    path = directory / LOCK_NAME
    info = _read_lock(path)
    if info is not None and info.get("token") == token:
        path.unlink(missing_ok=True)


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
    if out_dir.is_symlink():
        raise HistoryError("out_dir_is_symlink", out_dir.name)
    if out_dir.exists() and not out_dir.is_dir():
        raise HistoryError("out_dir_not_a_directory", out_dir.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = acquire_writer_lock(out_dir)
    try:
        if os.path.lexists(out_dir / MANIFEST_NAME):
            raise HistoryError("manifest_exists_dataset_is_immutable")
        clean_stale_temp(out_dir)
        return _extract_locked(
            source, request, conversion, quality, out_dir, retrieved_at, clock_attestation,
            protected, margin, sleep,
        )  # fmt: skip
    finally:
        release_writer_lock(out_dir, token)


def _extract_locked(
    source: HistoricalTickSource,
    request: ExtractionRequest,
    conversion: TimeConversion,
    quality: QualityConfig,
    out_dir: Path,
    retrieved_at: datetime,
    clock_attestation: ClockAttestation,
    protected: tuple[tuple[int, int], ...],
    margin: int,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
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
            if _kind(path) != "regular":
                raise HistoryError("chunk_exists_not_a_regular_file", name) from None
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
        "units": _units(instrument.to_json()),
        "development_status": DEVELOPMENT_STATUS,
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
    try:
        write_once(
            out_dir / MANIFEST_NAME,
            json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n",
        )
    except FileExistsError:
        raise HistoryError("manifest_exists_dataset_is_immutable") from None
    return manifest


def _units(instrument_json: dict[str, Any]) -> dict[str, Any]:
    """What a price and a timestamp mean: declared units plus the instrument facts that fix them.
    Absent facts are recorded as such (never defaulted)."""
    raw = instrument_json.get("fields") if isinstance(instrument_json, dict) else None
    fields: dict[str, dict[str, Any]] = {}
    for f in raw if isinstance(raw, list) else []:
        if isinstance(f, dict) and isinstance(f.get("name"), str):
            fields[f["name"]] = f
    absent: dict[str, Any] = {"state": "unknown", "value": None}
    return {
        "time": TIME_UNIT,
        "price": PRICE_UNIT,
        "instrument_fields": {
            n: {
                "state": fields.get(n, absent).get("state"),
                "value": fields.get(n, absent).get("value"),
            }
            for n in UNIT_FIELDS
        },
    }


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    """The research dataset identity. `content_sha256` is the canonical tick bytes only; this adds
    everything that decides what those bytes MEAN: the symbol and window, the raw->UTC conversion
    (with its derived fold ranges) and clock attestation, the source, and the declared units with
    the instrument facts that fix them. Retries, retrieval time, chunk layout, out-of-window
    drops, terminal state and trading-rule metadata are NOT identity (they are in the manifest
    hash and the external anchor)."""
    return {
        "schema": manifest["schema"],
        "symbol": manifest["symbol"],
        "window_raw_msc": manifest["window_raw_msc"],
        "time_conversion": manifest["time_conversion"],
        "clock_attestation": manifest["clock_attestation"],
        "content_sha256": manifest["content_sha256"],
        "tick_count": manifest["tick_count"],
        "source": manifest["source"],
        "units": manifest["units"],
    }


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with _open_regular(path) as fh:
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
    "instrument",
    "units",
    "development_status",
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
    kind = _kind(mpath)
    if kind == "missing":
        return [Problem("manifest_missing")]
    if kind != "regular":
        return [
            Problem("manifest_is_symlink" if kind == "symlink" else "manifest_not_regular_file")
        ]
    try:
        with _open_regular(mpath) as mfh:
            manifest = json.loads(mfh.read())
    except (HistoryError, OSError, ValueError, UnicodeDecodeError) as exc:
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
    if manifest["development_status"] != DEVELOPMENT_STATUS:
        # A manifest can never grant trust to itself.
        problems.append(Problem("manifest_claims_trust", str(manifest["development_status"])[:40]))
    prov = manifest["provenance"]
    orders = prov.get("orders_submitted")
    if (
        type(orders) is not int
        or orders != 0
        or prov.get("read_only_source_allowlist") is not True
        or manifest.get("immutable") is not True
    ):
        problems.append(Problem("provenance_safety_claims_invalid"))
    if manifest["units"] != _units(manifest["instrument"]):
        problems.append(Problem("units_binding_mismatch"))
    try:
        if manifest["quality_hard_defects"] != hard_defects(manifest["quality"]):
            problems.append(Problem("hard_defects_mismatch"))
    except (KeyError, TypeError, ValueError):
        problems.append(Problem("quality_malformed"))

    chunks = manifest["chunks"]
    listed = {c["file"] for c in chunks}
    entries = {p.name for p in directory.iterdir()}
    present = {n for n in entries if n.startswith("chunk-") and TMP_MARKER not in n}
    problems += [Problem("chunk_missing", n) for n in sorted(listed - entries)]
    problems += [Problem("chunk_unlisted", n) for n in sorted(present - listed)]
    problems += [Problem("stale_temp_file", n) for n in sorted(entries) if TMP_MARKER in n]
    problems += [Problem("writer_lock_present", n) for n in sorted(entries) if n == LOCK_NAME]
    problems += [
        Problem("unexpected_entry", n)
        for n in sorted(entries - listed - present - {MANIFEST_NAME, LOCK_NAME})
        if TMP_MARKER not in n
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
        kind = _kind(path)
        if kind != "regular":
            # Only a regular file can be evidence: a directory, FIFO, device or symlink is a
            # failure, never a silent skip. (A missing file was reported as chunk_missing above.)
            parse_ok = False
            if kind == "symlink":
                problems.append(Problem("chunk_is_symlink", name))
            elif kind == "other":
                problems.append(Problem("chunk_not_regular_file", name))
            continue
        digest = hashlib.sha256()
        try:
            with _open_regular(path) as cfh:
                for block in iter(lambda: cfh.read(1 << 20), b""):
                    digest.update(block)
                    content.update(block)
        except HistoryError as exc:
            problems.append(Problem("chunk_unreadable", name, exc.code))
            parse_ok = False
            continue
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
    if manifest["time_conversion"] != conv.to_json():
        # derived_fold_ranges (and the version) are recomputed, never trusted from the file
        problems.append(Problem("time_conversion_not_canonical"))
    try:
        prot = validate_protected_ranges(manifest["provenance"].get("protected_utc_ranges"))
        margin = validate_margin(manifest["provenance"].get("protected_margin_ms"))
    except HistoryError as exc:
        problems.append(Problem("protected_ranges_invalid", exc.code))
        return problems
    if not conv.covers(*manifest["window_raw_msc"]):
        problems.append(Problem("time_conversion_does_not_cover_window"))
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
    if not (
        isinstance(w, list) and len(w) == 2 and all(type(x) is int for x in w) and 0 <= w[0] < w[1]
    ):
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
        ("instrument", dict),
        ("units", dict),
        ("development_status", str),
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
        with _open_regular(path) as fh:
            for lineno, line in enumerate(fh, 1):
                row = json.loads(line)
                t = RawTick(row["t"], Decimal(row["b"]), Decimal(row["a"]), row.get("f"))
                if type(t.time_msc) is not int or (
                    t.flags is not None and type(t.flags) is not int
                ):
                    return count, outside, f"line {lineno}: bad field type"
                count += 1
                outside = outside or not (lo <= t.time_msc < hi)
    except (
        HistoryError,
        ValueError,
        KeyError,
        TypeError,
        ArithmeticError,
        UnicodeDecodeError,
    ) as exc:
        return count, outside, f"line {count + 1}: {type(exc).__name__}"
    return count, outside, ""


def usability_blockers(
    directory: Path, *, expected_manifest_sha256: str | None = None
) -> list[str]:
    """Everything that makes a dataset unfit for development use: verification problems plus hard
    data defects. An empty list does NOT mean TRUSTED: that needs `dataset_status` and an
    independently stored anchor. Gaps are reported by the quality report, not judged here (Quant
    decision MD-6)."""
    problems = verify_dataset(directory, expected_manifest_sha256=expected_manifest_sha256)
    if problems:
        return problems
    with _open_regular(directory / MANIFEST_NAME) as fh:
        manifest = json.loads(fh.read())
    return [f"hard_defect:{d}" for d in manifest["quality_hard_defects"]]


# ---- trust: integrity is not provenance ----------------------------------------------------
@dataclass(frozen=True, slots=True)
class DatasetStatus:
    """`TRUSTED` only when integrity, defects AND an independently stored anchor all agree.
    Everything else is `UNTRUSTED`: usable for development, never as research evidence."""

    status: str
    reasons: tuple[str, ...]

    @property
    def trusted(self) -> bool:
        return self.status == "TRUSTED"


def make_anchor(
    manifest: Mapping[str, Any], *, recorded_by: str, storage_ref: str, recorded_at: datetime
) -> dict[str, Any]:
    """The record an operator stores OUTSIDE the dataset folder and outside this machine's
    working copy (ticket, PR comment, another host). `storage_ref` says where it lives."""
    if recorded_at.tzinfo is None:
        raise HistoryError("anchor_recorded_at_must_be_tz_aware")
    for text in (recorded_by, storage_ref):
        if type(text) is not str or not text.strip():
            raise HistoryError("anchor_fields_required")
    return {
        "schema": ANCHOR_SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "dataset_id": manifest["dataset_id"],
        "content_sha256": manifest["content_sha256"],
        "symbol": manifest["symbol"],
        "recorded_by": recorded_by,
        "storage_ref": storage_ref,
        "recorded_at": recorded_at.isoformat(),
    }


def _inside(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def load_anchor(path: Path, *, dataset_dir: Path) -> dict[str, Any]:
    """Read and validate an anchor file. It must live outside the dataset folder."""
    if _inside(path, dataset_dir):
        raise HistoryError("anchor_inside_dataset")
    try:
        with _open_regular(path) as fh:
            data = json.loads(fh.read(65536))
    except (HistoryError, ValueError, UnicodeDecodeError):
        raise HistoryError("anchor_unreadable") from None
    problem = _anchor_problem(data)
    if problem:
        raise HistoryError("anchor_malformed", problem)
    assert isinstance(data, dict)
    return data


def _anchor_problem(data: Any) -> str:
    if not isinstance(data, dict) or data.get("schema") != ANCHOR_SCHEMA:
        return "schema"
    for key in ("manifest_sha256", "dataset_id", "content_sha256"):
        v = data.get(key)
        if type(v) is not str or not _HEX64.fullmatch(v):
            return key
    for key in ("symbol", "recorded_by", "storage_ref"):
        if type(data.get(key)) is not str or not data[key].strip():
            return key
    try:
        when = datetime.fromisoformat(str(data.get("recorded_at")))
    except ValueError:
        return "recorded_at"
    return "" if when.tzinfo is not None else "recorded_at"


def dataset_status(directory: Path, anchor: Mapping[str, Any] | None = None) -> DatasetStatus:
    """Never raises. TRUSTED needs a valid anchor whose manifest hash, dataset id, content hash and
    symbol all match, a fully verifying dataset and no hard data defects."""
    reasons: list[str] = []
    expected: str | None = None
    if anchor is None:
        reasons.append("no_independent_anchor")
    else:
        bad = _anchor_problem(dict(anchor) if isinstance(anchor, Mapping) else None)
        if bad:
            reasons.append(f"anchor_malformed:{bad}")
        else:
            expected = str(anchor["manifest_sha256"])
    problems = verify_dataset_report(directory, expected_manifest_sha256=expected)
    reasons += [f"integrity:{p}" for p in problems]
    if not problems:
        try:
            with _open_regular(directory / MANIFEST_NAME) as fh:
                manifest = json.loads(fh.read())
            reasons += [f"hard_defect:{d}" for d in manifest["quality_hard_defects"]]
            if anchor is not None and expected is not None:
                for key in ("dataset_id", "content_sha256", "symbol"):
                    if anchor[key] != manifest[key]:
                        reasons.append(f"anchor_mismatch:{key}")
        except (HistoryError, ValueError, KeyError, TypeError):
            reasons.append("integrity:manifest_unreadable_after_verify")
    return DatasetStatus("TRUSTED" if not reasons else "UNTRUSTED", tuple(reasons))


def require_trusted(directory: Path, anchor: Mapping[str, Any] | None) -> DatasetStatus:
    """Gate for any consumer that treats a dataset as evidence (G1-G5, baseline freeze)."""
    status = dataset_status(directory, anchor)
    if not status.trusted:
        raise HistoryError("dataset_untrusted", ",".join(status.reasons)[:300])
    return status
