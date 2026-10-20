"""Custodian tool for market-history trust anchors (offline; never touches a terminal).

Run by an AUTHORIZED CUSTODIAN, a different identity from the extractor and the dataset operator,
on a machine they control. `approve` re-verifies the dataset itself, applies the same checks the
verifier will apply (registry membership, independence, binding to the exact dataset) and appends
a hash-chained entry to the append-only evidence store. `revoke` withdraws trust. `head` prints the
current chain head so it can be pinned in an independent channel (`verify --store-head`).
No credentials, no network, no signing keys: authenticity of the custodian's declared identity is a
process control (store access control + registry), see THREAT-MODEL-V4.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from nexora.market_history.custody import (
    LocalAppendOnlyStore,
    approve_dataset,
    head_of,
    load_registry,
    revoke_dataset,
    verify_chain,
)
from nexora.market_history.extract import _open_regular
from nexora.market_history.models import HistoryError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("approve", "revoke"):
        p = sub.add_parser(name)
        p.add_argument("--dataset", type=Path, required=True)
        p.add_argument("--store", type=Path, required=True)
        p.add_argument("--registry", type=Path, required=True)
        p.add_argument("--custodian", required=True, help="declared custodian identity")
        p.add_argument("--evidence-ref", required=True, help="where the decision is recorded")
        if name == "approve":
            p.add_argument("--candidate", type=Path, required=True)
    h = sub.add_parser("head")
    h.add_argument("--store", type=Path, required=True)
    args = ap.parse_args()
    store = LocalAppendOnlyStore(args.store)
    try:
        if args.cmd == "head":
            entries = store.entries()
            problem = verify_chain(entries)
            head = head_of(entries)
            print(
                json.dumps(
                    {
                        "chain": problem or "ok",
                        "head": f"{head.seq}:{head.entry_sha256}" if head else None,
                    }
                )
            )
            return 1 if problem else 0
        registry = load_registry(args.registry, dataset_dir=args.dataset)
        now = datetime.now(UTC)
        if args.cmd == "approve":
            with _open_regular(args.candidate) as fh:
                candidate = json.loads(fh.read(1 << 20))
            entry = approve_dataset(
                args.dataset, candidate, store=store, registry=registry,
                approver=args.custodian, evidence_ref=args.evidence_ref, approved_at=now,
            )  # fmt: skip
        else:
            entry = revoke_dataset(
                args.dataset, store=store, registry=registry, revoker=args.custodian,
                evidence_ref=args.evidence_ref, revoked_at=now,
            )  # fmt: skip
    except HistoryError as exc:
        print(json.dumps({"refused": exc.code, "detail": str(exc)[:300]}))
        return 2
    print(json.dumps({"seq": entry["seq"], "entry_sha256": entry["entry_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
