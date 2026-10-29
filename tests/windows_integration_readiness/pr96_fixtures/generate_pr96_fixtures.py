# mypy: ignore-errors
"""Generate PUBLIC contract fixtures with the real PR #96 custody code (never run by pytest).

Usage (offline, read-only export of the pinned candidate; needs `cryptography`):

    git archive <pinned head> | tar -x -C <export dir>
    PYTHONPATH=<export dir>/packages python generate_pr96_fixtures.py <out dir>

It creates THROWAWAY Ed25519 keys in memory, builds a synthetic dataset binding (no ticks, no
terminal, no broker), appends real signed approval/revocation entries to a real
`LocalAppendOnlyStore` in a temp dir, signs head pins with the real `make_pin`, and runs the real
`evaluate_custody`. Only public material is written: registry (public keys), candidate, store
entries, pins and the verdict. Private keys are discarded and never written. The output proves what
shapes #96 emits; it grants no trust to anything.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nexora.market_history import custody as c
from nexora.market_history.signing import generate_signer

T = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def main(out: Path) -> None:
    op, cust, pinner = generate_signer(), generate_signer(), generate_signer()
    ids = {"operator": "synthetic-operator", "custodian": "synthetic-custodian"}
    ids["pin"] = "synthetic-pin-authority"
    registry_json = {
        "schema": c.REGISTRY_SCHEMA,
        "principals": [
            {"id": ids["operator"], "roles": ["operator"], "keys": [{"ed25519": op.public_hex}]},
            {
                "id": ids["custodian"],
                "roles": ["custodian"],
                "keys": [{"ed25519": cust.public_hex}],
            },
            {
                "id": ids["pin"],
                "roles": ["pin_authority"],
                "keys": [{"ed25519": pinner.public_hex}],
            },
        ],
    }
    registry = c.PrincipalRegistry.from_json(registry_json)
    manifest = {
        "dataset_id": "a" * 64,
        "content_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "symbol": "SYN",
        "window_raw_msc": [1700000000000, 1700000005000],
        "provenance": {"operator": ids["operator"], "retrieved_at": T.isoformat()},
    }
    scenarios = {}
    with tempfile.TemporaryDirectory() as tmp:
        # --- A: one approval, one initial pin -----------------------------------------------
        store = c.LocalAppendOnlyStore(Path(tmp) / "a")
        cand = c.make_candidate(manifest, signer=op, created_at=T + timedelta(minutes=1))
        body = c.build_approval_body(
            manifest,
            cand,
            signer=cust,
            approver=ids["custodian"],
            evidence_ref="synthetic-ticket-1",
            approved_at=T + timedelta(minutes=2),
        )
        store.append(body)
        entries = store.entries()
        pin = c.make_pin(
            entries,
            signer=pinner,
            pinned_by=ids["pin"],
            captured_at=T + timedelta(minutes=3),
            initial=True,
        )
        now = T + timedelta(minutes=10)
        verdict = c.evaluate_custody(
            manifest, entries, registry, pin=pin, max_pin_age=timedelta(hours=1), now=now
        )
        scenarios["A_single_approval"] = _pack(entries, [pin], verdict, now, 3600, cand)
        # --- B: approve, revoke, re-approve acknowledging the revocation --------------------
        store = c.LocalAppendOnlyStore(Path(tmp) / "b")
        cand1 = c.make_candidate(manifest, signer=op, created_at=T + timedelta(minutes=1))
        store.append(
            c.build_approval_body(
                manifest,
                cand1,
                signer=cust,
                approver=ids["custodian"],
                evidence_ref="t-1",
                approved_at=T + timedelta(minutes=2),
            )
        )
        rev = {
            "schema": c.APPROVAL_SCHEMA,
            "decision": "revoke",
            **c._bindings(manifest),
            "revocation_id": c._nonce(),
            "revoker": ids["custodian"],
            "revoker_key_id": cust.key_id,
            "revoked_at": (T + timedelta(minutes=4)).isoformat(),
            "evidence_ref": "t-2",
        }
        rev["signature"] = cust.sign("custody-revocation", rev)
        store.append(rev)
        cand2 = c.make_candidate(manifest, signer=op, created_at=T + timedelta(minutes=5))
        store.append(
            c.build_approval_body(
                manifest,
                cand2,
                signer=cust,
                approver=ids["custodian"],
                evidence_ref="t-3",
                approved_at=T + timedelta(minutes=6),
                acknowledged_revocations=c.revocations_of(store.entries(), manifest["dataset_id"]),
            )
        )
        entries_b = store.entries()
        pin_b = c.make_pin(
            entries_b,
            signer=pinner,
            pinned_by=ids["pin"],
            captured_at=T + timedelta(minutes=7),
            initial=True,
        )
        now_b = T + timedelta(minutes=20)
        verdict_b = c.evaluate_custody(
            manifest, entries_b, registry, pin=pin_b, max_pin_age=timedelta(hours=1), now=now_b
        )
        scenarios["B_revoke_then_reapprove"] = _pack(
            entries_b, [pin_b], verdict_b, now_b, 3600, cand2
        )
        # --- C: replay of the first approval after the revocation (F2) ----------------------
        store = c.LocalAppendOnlyStore(Path(tmp) / "c")
        first = c.build_approval_body(
            manifest,
            cand1,
            signer=cust,
            approver=ids["custodian"],
            evidence_ref="t-1",
            approved_at=T + timedelta(minutes=2),
        )
        store.append(first)
        store.append(rev)
        store.append(first)  # byte-identical replay of the old decision
        entries_c = store.entries()
        pin_c = c.make_pin(
            entries_c,
            signer=pinner,
            pinned_by=ids["pin"],
            captured_at=T + timedelta(minutes=7),
            initial=True,
        )
        verdict_c = c.evaluate_custody(
            manifest, entries_c, registry, pin=pin_c, max_pin_age=timedelta(hours=1), now=now_b
        )
        scenarios["C_replay_after_revocation"] = _pack(
            entries_c, [pin_c], verdict_c, now_b, 3600, cand1
        )
    doc = {
        "generated_for_head": "see dependency-manifest.json pr96_candidate.head_sha",
        "generator": "tests/windows_integration_readiness/pr96_fixtures/generate_pr96_fixtures.py",
        "note": (
            "Public material produced by the real #96 custody code with throwaway keys. "
            "Synthetic dataset binding; no ticks, terminal, broker or real identity. "
            "Grants nothing."
        ),
        "registry": registry_json,
        "manifest": manifest,
        "scenarios": scenarios,
    }
    (out / "pr96-custody-fixtures.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n"
    )


def _pack(entries, pins, verdict, now, max_age, candidate):
    return {
        "entries": entries,
        "pins": pins,
        "candidate": candidate,
        "evaluated_at": now.isoformat(),
        "max_pin_age_seconds": max_age,
        "verdict": {
            "approved": verdict.approved,
            "reasons": list(verdict.reasons),
            "approval_seq": verdict.approval_seq,
        },
    }


if __name__ == "__main__":
    main(Path(sys.argv[1]))
