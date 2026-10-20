"""Provider-neutral ingestion of operator-supplied Dukascopy-style hourly `.bi5` tick files.

This module never touches the network. It ingests ORIGINAL files the data owner already holds,
preserves their bytes, and records URL/retrieval provenance supplied by the owner. The record
layout, month indexing and time base are community-documented only (see docs FINDINGS.md), so
everything unverified is a REQUIRED declared parameter and is written into the manifest.

All Decimal values are built from integers with the tuple constructor, so nothing here depends
on the ambient `decimal` context.
"""

from __future__ import annotations

import json
import lzma
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from nexora.market_history.models import (
    HistoryError,
    NormalizedTick,
    canonical_json,
    sha256_hex,
)
from nexora.market_history.quality import QualityConfig, hard_defects, quality_report

SCHEMA = "nexora.dukascopy-pilot/1"
LAYOUT_ID = "bi5-community-layout/1 (UNVERIFIED): >IIIff = ms_offset, ask, bid, ask_vol, bid_vol"
_REC = struct.Struct(">IIIff")
HOUR_MS = 3_600_000
MANIFEST = "manifest.json"


def exact_decimal(units: int, scale: int) -> Decimal:
    """units * 10**-scale, exactly, independent of any decimal context."""
    sign = 1 if units < 0 else 0
    return Decimal((sign, tuple(int(c) for c in str(abs(units))), -scale))


def scale_of(divisor: int) -> int:
    """Divisor must be an exact power of ten (e.g. 1000 -> 3). No default exists."""
    if isinstance(divisor, bool) or not isinstance(divisor, int) or divisor < 1:
        raise HistoryError("invalid_price_divisor")
    k = len(str(divisor)) - 1
    if 10**k != divisor:
        raise HistoryError("price_divisor_not_power_of_ten", str(divisor))
    return k


@dataclass(frozen=True, slots=True)
class DeclaredSemantics:
    """What the DATA OWNER asserts about the files. None of it is inferred here."""

    file_hour_clock_offset_seconds: int  # utc = file_hour_label_time - offset; 0 = labels are UTC
    clock_evidence_ref: str  # documentation/ticket supporting the offset (required)
    price_divisor: int
    month_index_base: int  # 0 or 1 in the directory names
    layout_confirmed_by: str  # who/what confirmed the 20-byte layout (required, may say 'none')

    def __post_init__(self) -> None:
        scale_of(self.price_divisor)
        if self.month_index_base not in (0, 1):
            raise HistoryError("invalid_month_index_base")
        if not self.clock_evidence_ref.strip() or not self.layout_confirmed_by.strip():
            raise HistoryError("declared_semantics_evidence_required")

    def to_json(self) -> dict[str, Any]:
        return {
            "file_hour_clock_offset_seconds": self.file_hour_clock_offset_seconds,
            "clock_evidence_ref": self.clock_evidence_ref,
            "price_divisor": self.price_divisor,
            "month_index_base": self.month_index_base,
            "layout_confirmed_by": self.layout_confirmed_by,
            "layout": LAYOUT_ID,
        }


def slots(start_day: date, days: int) -> list[datetime]:
    if days < 1:
        raise HistoryError("invalid_days")
    first = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    return [first + timedelta(hours=h) for h in range(days * 24)]


def rel_path(symbol: str, slot: datetime, month_base: int) -> str:
    if not symbol.isalnum():
        raise HistoryError("invalid_symbol")
    month = slot.month - 1 + month_base
    return f"{symbol}/{slot.year:04d}/{month:02d}/{slot.day:02d}/{slot.hour:02d}h_ticks.bi5"


@dataclass(frozen=True, slots=True)
class HourTick:
    label_msc: int  # hour label epoch ms + record offset, in the files' own clock
    ask_units: int
    bid_units: int


def decode_hour(data: bytes, slot: datetime) -> list[HourTick]:
    """Decode one file. Raises HistoryError with a stable code; never guesses."""
    if not data:
        return []
    try:
        raw = lzma.decompress(data)
    except lzma.LZMAError as exc:
        raise HistoryError("lzma_decode_failed", type(exc).__name__) from None
    if len(raw) % _REC.size:
        raise HistoryError("record_size_mismatch", str(len(raw)))
    base = int(slot.timestamp()) * 1000
    out: list[HourTick] = []
    for off, ask, bid, _av, _bv in _REC.iter_unpack(raw):
        if off >= HOUR_MS:
            raise HistoryError("record_offset_outside_hour", str(off))
        out.append(HourTick(base + off, ask, bid))
    return out


def _read_log(path: Path) -> dict[str, dict[str, str]]:
    entries = json.loads(path.read_text())["files"]
    out: dict[str, dict[str, str]] = {}
    for e in entries:
        if e["file"] in out:
            raise HistoryError("duplicate_retrieval_record", e["file"])
        at = datetime.fromisoformat(e["retrieved_at"])
        if at.tzinfo is None:
            raise HistoryError("retrieved_at_must_be_tz_aware", e["file"])
        if not e.get("url", "").strip():
            raise HistoryError("source_url_required", e["file"])
        out[e["file"]] = {"url": e["url"], "retrieved_at": e["retrieved_at"]}
    return out


def _normalized(
    out_dir: Path, rows: list[dict[str, Any]], sem: DeclaredSemantics, symbol: str
) -> Iterator[NormalizedTick]:
    scale = scale_of(sem.price_divisor)
    for row in rows:
        if row["state"] != "present_nonempty" or row.get("decode_error"):
            continue
        slot = datetime.fromisoformat(row["slot_utc_label"])
        for t in decode_hour((out_dir / "raw" / row["file"]).read_bytes(), slot):
            utc = t.label_msc - sem.file_hour_clock_offset_seconds * 1000
            yield NormalizedTick(
                utc,
                t.label_msc,
                exact_decimal(t.bid_units, scale),
                exact_decimal(t.ask_units, scale),
                exact_decimal(t.ask_units - t.bid_units, scale),
            )


def _stream_hash(ticks: Iterator[NormalizedTick]) -> str:
    import hashlib

    h = hashlib.sha256()
    for t in ticks:
        h.update(f"{t.utc_msc},{t.raw_msc},{t.bid},{t.ask}\n".encode())
    return h.hexdigest()


def _decode_count(path: Path, slot: datetime) -> tuple[int, str | None]:
    try:
        return len(decode_hour(path.read_bytes(), slot)), None
    except HistoryError as exc:
        return 0, exc.code


def ingest(
    source_dir: Path,
    out_dir: Path,
    *,
    symbol: str,
    start_day: date,
    days: int,
    semantics: DeclaredSemantics,
    retrieval_log: Path,
    selection_ref: str,
    quality: QualityConfig,
    ingested_at: datetime,
) -> dict[str, Any]:
    if ingested_at.tzinfo is None:
        raise HistoryError("ingested_at_must_be_tz_aware")
    if not selection_ref.strip():
        raise HistoryError("selection_ref_required")  # window must be documented beforehand
    if (out_dir / MANIFEST).exists():
        raise HistoryError("manifest_exists_dataset_is_immutable")
    log = _read_log(retrieval_log)
    all_slots = slots(start_day, days)
    expected = {rel_path(symbol, s, semantics.month_index_base) for s in all_slots}
    unknown = sorted(set(log) - expected)
    if unknown:
        raise HistoryError("retrieval_record_for_unplanned_file", unknown[0])
    rows: list[dict[str, Any]] = []
    for slot in all_slots:
        rel = rel_path(symbol, slot, semantics.month_index_base)
        row: dict[str, Any] = {"slot_utc_label": slot.isoformat(), "file": rel}
        src = source_dir / rel
        if not src.is_file():
            rows.append({**row, "state": "absent_not_supplied"})
            continue
        if rel not in log:
            raise HistoryError("retrieval_record_missing", rel)
        data = src.read_bytes()
        dst = out_dir / "raw" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if dst.read_bytes() != data:
                raise HistoryError("raw_exists_with_different_content", rel)
        else:
            with dst.open("xb") as fh:
                fh.write(data)
            os.chmod(dst, 0o444)
        count, err = _decode_count(dst, slot)
        state = "present_empty" if not data else "present_nonempty"
        rows.append(
            {
                **row,
                "state": state,
                "bytes": len(data),
                "sha256": sha256_hex(data),
                "source_url": log[rel]["url"],
                "retrieved_at": log[rel]["retrieved_at"],
                "decoded_tick_count": count,
                "decode_error": err,
            }
        )
    report = quality_report(_normalized(out_dir, rows, semantics, symbol), quality)
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "symbol": symbol,
        "window": {"start_day": start_day.isoformat(), "days": days},
        "selection_ref": selection_ref,
        "declared_semantics": semantics.to_json(),
        "slots": rows,
        "coverage": _coverage(rows),
        "quality": report,
        "quality_hard_defects": hard_defects(report),
        "decode_errors": [r["file"] for r in rows if r.get("decode_error")],
        "normalized_stream_sha256": _stream_hash(_normalized(out_dir, rows, semantics, symbol)),
    }
    manifest = {
        **body,
        "dataset_id": sha256_hex(canonical_json(body)),
        "immutable": True,
        "provenance": {
            "ingested_at": ingested_at.isoformat(),
            "note": "files were supplied by the data owner; this tool performed no network access",
            "network_access": False,
        },
    }
    manifest["manifest_sha256"] = sha256_hex(canonical_json(manifest))
    with (out_dir / MANIFEST).open("xb") as fh:
        fh.write(json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n")
    return manifest


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_day: dict[str, str] = {}
    for r in rows:
        day = r["slot_utc_label"][:10]
        per_day[day] = (
            per_day.get(day, "")
            + {
                "present_nonempty": "#",
                "present_empty": "o",
                "absent_not_supplied": ".",
            }[r["state"]]
        )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {
        "legend": "# ticks present, o empty file, . not supplied (hour 00..23 left to right)",
        "per_day": per_day,
        "state_counts": counts,
        "absent_or_empty_is_a_defect": False,
        "note": "closures and weekends are expected to be empty; interpretation is the owner's",
    }


def verify(directory: Path) -> list[str]:
    """Independent re-verification from raw bytes and the manifest alone."""
    mpath = directory / MANIFEST
    if not mpath.exists():
        return ["manifest_missing"]
    m = json.loads(mpath.read_text())
    problems: list[str] = []
    stored = m.pop("manifest_sha256", None)
    if stored != sha256_hex(canonical_json(m)):
        problems.append("manifest_hash_mismatch")
    body = {k: v for k, v in m.items() if k not in ("dataset_id", "immutable", "provenance")}
    if m.get("dataset_id") != sha256_hex(canonical_json(body)):
        problems.append("dataset_id_mismatch")
    sem = m["declared_semantics"]
    semantics = DeclaredSemantics(
        sem["file_hour_clock_offset_seconds"],
        sem["clock_evidence_ref"],
        sem["price_divisor"],
        sem["month_index_base"],
        sem["layout_confirmed_by"],
    )
    rows = m["slots"]
    start = date.fromisoformat(m["window"]["start_day"])
    planned = [
        rel_path(m["symbol"], s, semantics.month_index_base)
        for s in slots(start, m["window"]["days"])
    ]
    if [r["file"] for r in rows] != planned:
        problems.append("slot_plan_mismatch")
    listed = {r["file"] for r in rows if r["state"] != "absent_not_supplied"}
    present = {str(p.relative_to(directory / "raw")) for p in (directory / "raw").rglob("*.bi5")}
    problems += [f"raw_missing:{n}" for n in sorted(listed - present)]
    problems += [f"raw_unlisted:{n}" for n in sorted(present - listed)]
    for r in rows:
        if r["state"] == "absent_not_supplied" or r["file"] not in present:
            continue
        path = directory / "raw" / r["file"]
        data = path.read_bytes()
        if sha256_hex(data) != r["sha256"] or len(data) != r["bytes"]:
            problems.append(f"raw_hash_mismatch:{r['file']}")
            continue
        count, err = _decode_count(path, datetime.fromisoformat(r["slot_utc_label"]))
        if (count, err) != (r["decoded_tick_count"], r["decode_error"]):
            problems.append(f"decode_result_mismatch:{r['file']}")
        if (r["state"] == "present_empty") != (not data):
            problems.append(f"state_mismatch:{r['file']}")
    if not problems:
        qc = QualityConfig(m["quality"]["config"]["max_gap_ms"])
        if canonical_json(
            quality_report(_normalized(directory, rows, semantics, m["symbol"]), qc)
        ) != canonical_json(m["quality"]):
            problems.append("quality_report_not_reproducible")
        if (
            _stream_hash(_normalized(directory, rows, semantics, m["symbol"]))
            != m["normalized_stream_sha256"]
        ):
            problems.append("normalized_stream_hash_mismatch")
    return problems
