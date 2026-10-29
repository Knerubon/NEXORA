"""Custodian tool for market-history trust anchors (offline; never touches a terminal).

Run by an AUTHORIZED CUSTODIAN, a different principal from the dataset operator, on a machine they
control. `approve` re-verifies the dataset itself, applies the same checks the verifier will apply
(signed operator attestation, custodian key and role, independence of the authenticated principals,
binding to the exact dataset) and appends a signed, hash-chained entry to the append-only evidence
store. `revoke` withdraws trust. `recover-store` finishes appends interrupted by a crash between
link and temp unlink (removes only temp names that are the same inode as their entry, and prints
them). `head` prints the current chain head (unsigned: it is NOT an
independent pin). `pin` is run by the PIN AUTHORITY, a principal independent of operators and
approving custodians, over the store as it reads it through its own channel: it signs the head and
must extend the previous pin (`--previous`), or state `--initial` / `--reanchor-ref` (where the
independent authorization to re-anchor a rewritten store is recorded). `keygen` creates an Ed25519
key pair: the private key file stays with its holder (mode 0600, never in Git, never next to the
data); the printed public key goes into the principal registry.

Exit codes: 0 done; 1 a store chain problem was reported (`head`, `recover-store`); 2 refused or
invalid input (`{"refused": <code>}`); 3 internal error (fail closed; nothing was appended).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from nexora.market_history import jsonio
from nexora.market_history.custody import (
    LocalAppendOnlyStore,
    approve_dataset,
    head_of,
    load_registry,
    make_pin,
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
    rs = sub.add_parser("recover-store", help="finish appends interrupted between link/unlink")
    rs.add_argument("--store", type=Path, required=True)
    pn = sub.add_parser("pin")
    pn.add_argument("--store", type=Path, required=True)
    pn.add_argument("--registry", type=Path, required=True)
    pn.add_argument("--signing-key", type=Path, required=True, help="pin authority private key")
    pn.add_argument("--out", type=Path, required=True, help="new pin file (never overwritten)")
    grp = pn.add_mutually_exclusive_group(required=True)
    grp.add_argument("--previous", type=Path, help="the last pin of this store")
    grp.add_argument("--initial", action="store_true", help="first pin of a new store")
    grp.add_argument("--reanchor-ref", help="where the re-anchoring authorization is recorded")
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
        if args.cmd == "recover-store":
            removed = store.recover()
            problem = verify_chain(store.entries())
            print(json.dumps({"removed_temp_names": removed, "chain": problem or "ok"}))
            return 1 if problem else 0
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
        if args.cmd == "pin":
            registry = load_registry(args.registry)
            signer = load_signer(args.signing_key)
            found = registry.key(signer.key_id)
            if found is None or "pin_authority" not in found[0].roles:
                raise HistoryError("pin_refused", "signer_is_not_a_pin_authority")
            previous = None
            if args.previous is not None:
                with _open_regular(args.previous) as fh:
                    previous = jsonio.loads(fh.read(1 << 16), "previous_pin")
            pin = make_pin(
                store.entries(), signer=signer, pinned_by=found[0].id,
                captured_at=datetime.now(UTC), previous=previous, initial=args.initial,
                reanchor_ref=args.reanchor_ref,
            )  # fmt: skip
            with args.out.open("x") as fh:
                fh.write(json.dumps(pin, indent=2, sort_keys=True) + "\n")
            head_text = f"{pin['seq']}:{pin['entry_sha256']}"
            print(json.dumps({"pin": head_text, "pin_id": pin["pin_id"]}))
            return 0
        registry = load_registry(args.registry, dataset_dir=args.dataset)
        signer = load_signer(args.signing_key)
        now = datetime.now(UTC)
        if args.cmd == "approve":
            with _open_regular(args.candidate) as fh:
                candidate = jsonio.loads(fh.read(1 << 20), "candidate")
            entry = approve_dataset(
                args.dataset, candidate, store=store, registry=registry, signer=signer,
                evidence_ref=args.evidence_ref, approved_at=now,
            )  # fmt: skip
        else:
            entry = revoke_dataset(
                args.dataset, store=store, registry=registry, signer=signer,
                evidence_ref=args.evidence_ref, revoked_at=now,
            )  # fmt: skip
    except (HistoryError, FileExistsError, ValueError) as exc:
        if isinstance(exc, HistoryError):
            code = exc.code
        else:
            code = "file_exists" if isinstance(exc, FileExistsError) else "input_unreadable"
        print(json.dumps({"refused": code, "detail": str(exc)[:300]}))
        return 2
    except Exception as exc:  # defensive: no uncontrolled traceback on hostile input (N-01)
        print(json.dumps({"refused": "internal_error", "detail": type(exc).__name__}))
        return 3
    result: dict[str, object] = {"seq": entry["seq"], "entry_sha256": entry["entry_sha256"]}
    if args.cmd == "revoke":
        # Process control, not enforcement (THREAT-MODEL-V4 section 9, R-1): a revocation newer
        # than the latest pin can be hidden by truncating the store until that pin goes stale.
        # The pin authority must pin a head that includes this entry; verifiers must then use it.
        result["repin_required"] = True
        result["repin_covers_seq"] = entry["seq"]
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
