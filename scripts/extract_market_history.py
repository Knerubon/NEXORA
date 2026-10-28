"""Operator-started, READ-ONLY historical tick extraction (WINDOWS-INTEGRATION).

Attaches to an already-running, already-logged-in terminal; accepts no credentials; sends no
orders. Requires an explicit time-conversion JSON (segments with evidence_ref) - there is no
default offset. Output is written to --out (keep it outside Git; raw ticks are not committed).
The dataset is born UNTRUSTED; this tool can only emit an anchor CANDIDATE, signed with the
operator's key. Approval is a separate act by an authorized custodian, a different principal
(`custody_market_history.py approve`).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from nexora.market_history.clock import ClockAttestation
from nexora.market_history.convert import TimeConversion, TimeSegment
from nexora.market_history.custody import make_candidate
from nexora.market_history.extract import (
    DEFAULT_MAX_CHUNK_TICKS,
    ExtractionRequest,
    dataset_status,
    extract,
    make_anchor,
    preflight,
    usability_blockers,
    verify_dataset,
)
from nexora.market_history.models import HistoryError
from nexora.market_history.provider import Mt5ReadOnlyHistorySource
from nexora.market_history.quality import QualityConfig
from nexora.market_history.signing import load_signer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--from-raw-msc", type=int, required=True)
    ap.add_argument("--to-raw-msc", type=int, required=True)
    ap.add_argument("--chunk-ms", type=int, required=True)
    ap.add_argument("--max-gap-ms", type=int, required=True)
    ap.add_argument("--max-chunk-ticks", type=int, default=DEFAULT_MAX_CHUNK_TICKS)
    ap.add_argument("--time-conversion", type=Path, required=True)
    ap.add_argument(
        "--clock-attestation",
        type=Path,
        required=True,
        help="JSON ClockAttestation (evidence_ref, attested_by, window, ambiguous_raw_ranges)",
    )
    guard = ap.add_mutually_exclusive_group(required=True)
    guard.add_argument(
        "--protected-utc-ranges",
        type=Path,
        help="JSON [[start_utc_msc, end_utc_msc], ...] (e.g. locked holdout); overlap is refused",
    )
    guard.add_argument(
        "--no-protected-ranges",
        action="store_true",
        help="explicit statement that no range is protected",
    )
    ap.add_argument("--protected-margin-ms", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--operator",
        help="principal id of the person running this extraction (canonical lower-case ASCII; "
        "recorded in the manifest; a dataset without one can never be TRUSTED; the custodian "
        "must be a different principal)",
    )
    ap.add_argument(
        "--operator-key",
        type=Path,
        help="the operator's Ed25519 private key file (PEM, mode 0600, outside the dataset): "
        "signs the candidate so the operator identity is authenticated, not declared",
    )
    ap.add_argument(
        "--candidate-out",
        type=Path,
        help="write an anchor CANDIDATE for a custodian to approve (outside the dataset folder). "
        "A candidate grants nothing: only a custodian approval in the evidence store does.",
    )
    anchor = ap.add_argument_group(
        "anchor (optional; all three together)",
        "Write the external anchor record. It MUST be stored outside the dataset folder and "
        "outside this machine's working copy (ticket, PR comment, another host). A dataset "
        "without an independently stored anchor stays UNTRUSTED.",
    )
    anchor.add_argument("--anchor-out", type=Path)
    anchor.add_argument("--anchor-recorded-by")
    anchor.add_argument("--anchor-storage-ref", help="where the anchor is kept (e.g. PR comment)")
    args = ap.parse_args()
    anchor_args = (args.anchor_out, args.anchor_recorded_by, args.anchor_storage_ref)
    if any(anchor_args) and not all(anchor_args):
        ap.error("--anchor-out, --anchor-recorded-by and --anchor-storage-ref go together")
    for flag, target in (("anchor", args.anchor_out), ("candidate", args.candidate_out)):
        if target is None:
            continue
        out_abs, target_abs = args.out.resolve(), target.resolve()
        if target_abs == out_abs or out_abs in target_abs.parents:
            raise HistoryError(f"{flag}_inside_dataset")
        if target.exists() or target.is_symlink():
            raise HistoryError(f"{flag}_file_exists")
    if args.candidate_out is not None and not (args.operator and args.operator_key):
        ap.error("--candidate-out needs --operator and --operator-key (a signed attestation)")
    signer = load_signer(args.operator_key) if args.candidate_out is not None else None

    # Everything below up to the source is validated BEFORE attaching to the terminal.
    raw = json.loads(args.time_conversion.read_text())
    conversion = TimeConversion(tuple(TimeSegment(**s) for s in raw["segments"]))
    attestation = ClockAttestation.from_json(json.loads(args.clock_attestation.read_text()))
    protected_in = (
        [] if args.no_protected_ranges else json.loads(args.protected_utc_ranges.read_text())
    )
    request = ExtractionRequest(
        args.symbol,
        args.from_raw_msc,
        args.to_raw_msc,
        args.chunk_ms,
        max_chunk_ticks=args.max_chunk_ticks,
    )
    quality = QualityConfig(args.max_gap_ms)
    protected, margin = preflight(
        request, conversion, attestation, protected_in, args.protected_margin_ms
    )

    source = Mt5ReadOnlyHistorySource()
    try:
        manifest = extract(
            source,
            request,
            conversion,
            quality,
            args.out,
            retrieved_at=datetime.now(UTC),
            clock_attestation=attestation,
            protected_utc_ranges=protected,
            protected_margin_ms=margin,
            operator=args.operator,
        )
    finally:
        source.close()
    problems = verify_dataset(args.out)
    blockers = usability_blockers(args.out)
    record = None
    if args.anchor_out is not None:
        record = make_anchor(
            manifest,
            recorded_by=args.anchor_recorded_by,
            storage_ref=args.anchor_storage_ref,
            recorded_at=datetime.now(UTC),
        )
        with args.anchor_out.open("x") as fh:  # exclusive: never overwrite an anchor
            fh.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    if args.candidate_out is not None:
        assert signer is not None
        candidate = make_candidate(manifest, signer=signer, created_at=datetime.now(UTC))
        with args.candidate_out.open("x") as fh:
            fh.write(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    # Writing the anchor next to the data on the same disk proves nothing on its own: the operator
    # must copy it to its independent home. Until then, and without it, the status is UNTRUSTED.
    status = dataset_status(args.out, record)  # no store/registry here: never TRUSTED at extraction
    print(
        json.dumps(
            {
                "dataset_id": manifest["dataset_id"],
                "content_sha256": manifest["content_sha256"],
                "manifest_sha256": manifest["manifest_sha256"],  # record this OUTSIDE the folder
                "verify_problems": problems,
                "usability_blockers": blockers,
                "status": status.status,
                "status_reasons": list(status.reasons),
            }
        )
    )
    return 1 if problems or blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
