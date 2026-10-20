"""Verify a market-history dataset and report TRUSTED / UNTRUSTED (offline; reads files only).

TRUSTED needs independent custody evidence supplied by the VERIFIER, outside the dataset folder:
`--store` (an append-only evidence store directory) and `--registry` (custodian allowlist JSON).
A dataset that verifies cleanly without them is still UNTRUSTED: usable for development, never as
evidence (G1-G5, baseline freeze). A V3-style `--anchor` (hashes only) is cross-checked but never
grants trust. `--store-head SEQ:SHA256` pins the store head obtained from an independent channel
(detects truncation/rollback of the store).
Exit code: 0 = TRUSTED, or (without --require-trusted) cleanly verified; 1 = not; 2 = bad input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nexora.market_history.custody import ChainHead, LocalAppendOnlyStore, load_registry
from nexora.market_history.extract import dataset_status, load_anchor
from nexora.market_history.models import HistoryError

_FAILURE_PREFIXES = (
    "integrity:",
    "hard_defect:",
    "anchor_malformed:",
    "anchor_mismatch:",
    "custody:chain_invalid",
    "custody:binding_mismatch",
    "custody:store_unreadable",
    "custody:revoked",
)


def _head(text: str) -> ChainHead:
    seq, _, digest = text.partition(":")
    if not seq.isdigit() or int(seq) < 1 or len(digest) != 64:
        raise HistoryError("invalid_store_head", "expected SEQ:SHA256")
    return ChainHead(int(seq), digest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--anchor", type=Path, help="V3 hash-only anchor, OUTSIDE the dataset folder")
    ap.add_argument("--store", type=Path, help="append-only evidence store directory (outside)")
    ap.add_argument("--registry", type=Path, help="custodian registry JSON (outside the dataset)")
    ap.add_argument("--store-head", help="pinned store head SEQ:SHA256 from an independent channel")
    ap.add_argument("--require-trusted", action="store_true")
    args = ap.parse_args()
    if bool(args.store) != bool(args.registry):
        ap.error("--store and --registry go together")
    anchor = None
    store = registry = head = None
    try:
        if args.anchor is not None:
            anchor = load_anchor(args.anchor, dataset_dir=args.dataset)
        if args.store is not None:
            store = LocalAppendOnlyStore(args.store)
            registry = load_registry(args.registry, dataset_dir=args.dataset)
        if args.store_head is not None:
            head = _head(args.store_head)
    except HistoryError as exc:
        print(json.dumps({"status": "UNTRUSTED", "status_reasons": [f"{exc.code}"]}))
        return 2
    status = dataset_status(
        args.dataset, anchor, store=store, registry=registry, expected_head=head
    )
    print(json.dumps({"status": status.status, "status_reasons": list(status.reasons)}))
    if status.trusted:
        return 0
    cleanly_verified = not any(r.startswith(_FAILURE_PREFIXES) for r in status.reasons)
    return 0 if cleanly_verified and not args.require_trusted else 1


if __name__ == "__main__":
    raise SystemExit(main())
