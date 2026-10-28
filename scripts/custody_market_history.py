"""Custodian tool for market-history trust anchors (offline; never touches a terminal).

Run by an AUTHORIZED CUSTODIAN, a different principal from the dataset operator, on a machine they
control. `approve` re-verifies the dataset itself, applies the same checks the verifier will apply
(signed operator attestation, custodian key and role, independence of the authenticated principals,
binding to the exact dataset) and appends a signed, hash-chained entry to the append-only evidence
store. `revoke` withdraws trust. `head` prints the current chain head. `keygen` creates an Ed25519
key pair: the private key file stays with its holder (mode 0600, never in Git, never next to the
data); the printed public key goes into the principal registry.
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
from nexora.market_history.signing import generate_signer, load_signer, write_signer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("approve", "revoke"):
        p = sub.add_parser(name)
        p.add_argument("--dataset", type=Path, required=True)
        p.add_argument("--store", type=Path, required=True)
        p.add_argument("--registry", type=Path, required=True)
        p.add_argument("--signing-key", type=Path, required=True, help="custodian private key")
        p.add_argument("--evidence-ref", required=True, help="where the decision is recorded")
        if name == "approve":
            p.add_argument("--candidate", type=Path, required=True)
    h = sub.add_parser("head")
    h.add_argument("--store", type=Path, required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", type=Path, required=True, help="new private key file (0600)")
    args = ap.parse_args()
    try:
        if args.cmd == "keygen":
            signer = generate_signer()
            write_signer(signer, args.out)
            print(json.dumps({"ed25519": signer.public_hex, "key_id": signer.key_id}))
            return 0
        store = LocalAppendOnlyStore(args.store)
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
        signer = load_signer(args.signing_key)
        now = datetime.now(UTC)
        if args.cmd == "approve":
            with _open_regular(args.candidate) as fh:
                candidate = json.loads(fh.read(1 << 20))
            entry = approve_dataset(
                args.dataset, candidate, store=store, registry=registry, signer=signer,
                evidence_ref=args.evidence_ref, approved_at=now,
            )  # fmt: skip
        else:
            entry = revoke_dataset(
                args.dataset, store=store, registry=registry, signer=signer,
                evidence_ref=args.evidence_ref, revoked_at=now,
            )  # fmt: skip
    except (HistoryError, FileExistsError) as exc:
        code = exc.code if isinstance(exc, HistoryError) else "key_file_exists"
        print(json.dumps({"refused": code, "detail": str(exc)[:300]}))
        return 2
    print(json.dumps({"seq": entry["seq"], "entry_sha256": entry["entry_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
