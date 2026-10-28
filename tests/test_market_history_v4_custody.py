"""V4 / A3 (round 3): independent, AUTHENTICATED anchor custody. A matching hash is not trust and a
declared name is not an identity: TRUSTED needs a verifying approval chain in an external
append-only store, an operator attestation and a custodian approval each signed by a key that the
verifier's principal registry assigns to the right principal and role, and independence between
those authenticated principals. SYNTHETIC fixtures only; keys are generated per test run and never
written to the repository (see THREAT-MODEL-V4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import custody
from nexora.market_history.custody import (
    GENESIS,
    ChainHead,
    CustodianRegistry,
    EvidenceStore,
    LocalAppendOnlyStore,
    approve_dataset,
    build_approval_body,
    candidate_problem,
    evaluate_custody,
    head_of,
    load_registry,
    make_candidate,
    normalize_identity,
    revoke_dataset,
    seal_entry,
    verify_chain,
)
from nexora.market_history.extract import (
    LOCK_NAME,
    dataset_status,
    make_anchor,
    require_trusted,
    verify_dataset,
)
from nexora.market_history.models import HistoryError
from nexora.market_history.signing import Signer, generate_signer, write_signer

from tests.test_market_history_corrective import go, reseal
from tests.test_market_history_v3 import NOW, seal

ROOT = Path(__file__).resolve().parent.parent
LATER = NOW + timedelta(days=1)
CHECK = NOW + timedelta(days=30)
# One synthetic key per principal for the whole test run. "mallory" is never registered.
KEYS: dict[str, Signer] = {
    n: generate_signer() for n in ("ann", "carol", "dave", "erin", "pia", "mallory", "otto")
}


def principal(pid: str, roles: list[str], key: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"id": pid, "roles": roles, "keys": [{"ed25519": KEYS[key or pid].public_hex, **kw}]}


def registry_json(*extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": custody.REGISTRY_SCHEMA,
        "principals": [
            principal("ann", ["operator"]),
            principal("carol", ["custodian"]),
            principal(
                "dave",
                ["custodian"],
                valid_from="2026-01-01T00:00:00+00:00",
                valid_until="2026-12-31T00:00:00+00:00",
            ),
            principal("erin", ["operator", "custodian"]),  # may do either, never both at once
            principal("pia", ["pin_authority"]),
            *extra,
        ],
    }


REGISTRY_JSON: dict[str, Any] = registry_json()


@dataclass
class Env:
    root: Path
    ds: Path
    manifest: dict[str, Any]
    candidate: dict[str, Any]
    store: LocalAppendOnlyStore
    registry: CustodianRegistry
    extra: dict[str, Any] = field(default_factory=dict)


def make_env(tmp: Path, *, operator: str | None = "ann", creator: str | None = None) -> Env:
    """`operator` is recorded in the manifest; `creator` names the KEY that signs the candidate
    (default: the operator's own key)."""
    ds = tmp / "ds"
    m = go(ds, operator=operator)
    signer = KEYS[creator or operator or "ann"]
    if operator is None:  # attestation for a manifest that records nobody
        cand = make_candidate(
            {**m, "provenance": {**m["provenance"], "operator": creator or "ann"}},
            signer=signer,
            created_at=NOW,
        )
    else:
        cand = make_candidate(m, signer=signer, created_at=NOW)
    return Env(
        tmp,
        ds,
        m,
        cand,
        LocalAppendOnlyStore(tmp / "store"),
        CustodianRegistry.from_json(REGISTRY_JSON),
    )


def approve(e: Env, who: str = "carol", at: datetime = LATER, **kw: Any) -> dict[str, Any]:
    return approve_dataset(
        e.ds,
        kw.pop("candidate", e.candidate),
        store=kw.pop("store", e.store),
        registry=kw.pop("registry", e.registry),
        signer=KEYS[who],
        evidence_ref="ticket-1",
        approved_at=at,
    )


def status(e: Env, **kw: Any) -> Any:
    return dataset_status(
        e.ds,
        kw.pop("anchor", None),
        store=kw.pop("store", e.store),
        registry=kw.pop("registry", e.registry),
        now=kw.pop("now", CHECK),
        **kw,
    )


def body_for(e: Env, who: str = "carol", **over: Any) -> dict[str, Any]:
    """A correctly SIGNED approval body by `who`, with `over` applied and re-signed by `who`."""
    body = build_approval_body(
        e.manifest, e.candidate, signer=KEYS[who], approver=who, evidence_ref="t", approved_at=LATER
    )
    body.update(over)
    body.pop("signature")
    body["signature"] = KEYS[who].sign("custody-approval", body)
    return body


def raw_append(e: Env, who: str = "carol", **over: Any) -> dict[str, Any]:
    """An appender with APPEND access writes an entry directly (no tool checks)."""
    return e.store.append(body_for(e, who, **over))


# ======================= the policy: extraction never trusts =======================
def test_trusted_requires_the_full_custody_chain(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    assert e.manifest["development_status"] == "UNTRUSTED"
    assert not status(e).trusted  # empty store
    entry = approve(e)
    s = status(e)
    assert s.status == "TRUSTED" and s.reasons == () and entry["seq"] == 1
    assert require_trusted(e.ds, None, store=e.store, registry=e.registry, now=CHECK).trusted


def test_candidate_alone_or_a_hash_only_anchor_never_trusts(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    legacy = make_anchor(e.manifest, recorded_by="carol", storage_ref="PR", recorded_at=LATER)
    for kwargs in ({}, {"anchor": legacy}, {"store": None}, {"registry": None}):
        s = status(e, **kwargs)
        assert not s.trusted
    assert "legacy_anchor_is_not_custody_evidence" in dataset_status(e.ds, legacy).reasons
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        require_trusted(e.ds, legacy)
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        require_trusted(e.ds, None, store=e.store, registry=None)


def test_fail_closed_when_custody_cannot_be_independently_verified(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)

    class Broken:
        location = None

        def entries(self) -> list[dict[str, Any]]:
            raise HistoryError("store_unreadable", "offline")

        def append(self, body: Any) -> dict[str, Any]:
            raise AssertionError

    s = dataset_status(e.ds, None, store=Broken(), registry=e.registry, now=CHECK)
    assert not s.trusted and "custody:store_unreadable:store_unreadable" in s.reasons

    class Explodes(Broken):
        def entries(self) -> list[dict[str, Any]]:
            raise RuntimeError("bug")

    s = dataset_status(e.ds, None, store=Explodes(), registry=e.registry, now=CHECK)
    assert not s.trusted and s.reasons[0].startswith("status_internal_error")


# ======================= authentication, authorization and self-approval =======================
def test_unregistered_key_is_refused_by_the_tool_and_rejected_by_the_verifier(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    with pytest.raises(HistoryError, match="key_not_registered"):
        approve(e, "mallory")
    assert not (tmp_path / "store").exists()  # a refused approval wrote nothing
    raw_append(e, "mallory", approver="mallory")  # an appender bypasses the tool
    s = status(e)
    assert not s.trusted
    assert "custody:approval_1_rejected:approver_key_not_registered" in s.reasons


def test_a_declared_custodian_name_without_the_custodians_signature_is_rejected(
    tmp_path: Path,
) -> None:
    """F1: the appender writes approver="carol" and even carol's key id, but signs with its own
    key (or not at all). The chain verifies; the approval does not."""
    e = make_env(tmp_path)
    forged = body_for(e, "mallory", approver="carol", approver_key_id=KEYS["carol"].key_id)
    e.store.append(forged)
    unsigned = body_for(e)
    unsigned.pop("signature")
    e.store.append(unsigned)
    assert verify_chain(e.store.entries()) == ""
    s = status(e)
    assert not s.trusted
    assert "custody:approval_1_rejected:approval_signature_invalid" in s.reasons
    assert "custody:approval_2_rejected:approval_signature_invalid" in s.reasons


def test_a_principal_without_the_custodian_role_cannot_approve(tmp_path: Path) -> None:
    e = make_env(tmp_path, operator="erin")  # erin extracts; ann is an operator, not a custodian
    with pytest.raises(HistoryError, match="principal_lacks_role:custodian"):
        approve(e, "ann")
    raw_append(e, "ann", approver="ann", approver_key_id=KEYS["ann"].key_id)
    assert any("approver_principal_lacks_role:custodian" in r for r in status(e).reasons)
    raw_append(e, "pia", approver="pia", approver_key_id=KEYS["pia"].key_id)
    assert any("approver_principal_lacks_role:custodian" in r for r in status(e).reasons)


def test_self_approval_by_the_authenticated_operator_is_refused(tmp_path: Path) -> None:
    """erin holds both roles; she may extract or approve, never both for one dataset."""
    e = make_env(tmp_path, operator="erin")
    with pytest.raises(HistoryError, match="self_approval_by_operator"):
        approve(e, "erin")
    raw_append(e, "erin", approver="erin", approver_key_id=KEYS["erin"].key_id)
    s = status(e)
    assert not s.trusted and any("self_approval_by_operator" in r for r in s.reasons)
    approve(e, "carol")
    assert status(e).trusted


@pytest.mark.parametrize("variant", ["ANN", " ann", "ann ", "ａｎｎ", " ann", "a​nn", "аnn"])
def test_non_canonical_operator_spellings_are_refused_at_extraction(
    tmp_path: Path, variant: str
) -> None:
    """X5: ambiguous identity encodings are rejected, not normalised (the last one is Cyrillic)."""
    with pytest.raises(HistoryError, match="invalid_operator"):
        go(tmp_path / "ds", operator=variant)


def test_operator_attestation_must_be_signed_by_the_recorded_operators_own_key(
    tmp_path: Path,
) -> None:
    """N1: carol (a custodian) extracts, records operator "ann" and signs the candidate with her
    own key. The attestation does not verify: carol's key is not ann's."""
    e = make_env(tmp_path, operator="ann", creator="carol")
    with pytest.raises(HistoryError, match="operator_key_belongs_to_another_principal"):
        approve(e, "dave")
    raw_append(e, "dave", approver="dave", approver_key_id=KEYS["dave"].key_id)
    assert any("operator_key_belongs_to_another_principal" in r for r in status(e).reasons)


def test_operator_principal_needs_the_operator_role(tmp_path: Path) -> None:
    e = make_env(tmp_path, operator="carol")  # carol is registered as custodian only
    with pytest.raises(HistoryError, match="operator_principal_lacks_role:operator"):
        approve(e, "dave")


def test_a_dataset_without_a_recorded_operator_can_never_be_approved_or_trusted(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path, operator=None)
    with pytest.raises(HistoryError, match="operator_not_recorded"):
        approve(e)
    raw_append(e)
    s = status(e)
    assert not s.trusted and "custody:operator_not_recorded" in s.reasons
    with pytest.raises(HistoryError, match="operator_not_recorded"):
        make_candidate(e.manifest, signer=KEYS["ann"], created_at=NOW)


def test_key_validity_is_checked_at_signing_time_and_at_verification_time(
    tmp_path: Path,
) -> None:
    """F4: dave's key is valid during 2026 only. An approval dated inside the window still stops
    counting once the verifier's own clock is past the window (stale authorization)."""
    e = make_env(tmp_path)
    with pytest.raises(HistoryError, match="key_not_valid_at_time"):
        approve(e, "dave", at=datetime(2027, 6, 1, tzinfo=UTC))
    approve(e, "dave", at=LATER)
    assert status(e).trusted
    late = status(e, now=datetime(2027, 6, 1, tzinfo=UTC))
    assert not late.trusted and any("approver_key_not_valid_at_time" in r for r in late.reasons)


@pytest.mark.parametrize(
    ("at", "reason"),
    [
        (NOW - timedelta(days=1), "approved_before"),
        (NOW + timedelta(days=200), "approved_in_the_future"),
    ],
)
def test_approval_time_must_follow_retrieval_and_not_be_in_the_future(
    tmp_path: Path, at: datetime, reason: str
) -> None:
    e = make_env(tmp_path)
    raw_append(e, approved_at=at.isoformat())
    s = status(e)
    assert not s.trusted and any(reason in r for r in s.reasons)


def test_approval_before_the_candidate_existed_is_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    e.candidate = make_candidate(
        e.manifest, signer=KEYS["ann"], created_at=LATER + timedelta(days=2)
    )
    raw_append(e, approved_at=(LATER + timedelta(days=1)).isoformat())
    assert any("approved_before_candidate_created" in r for r in status(e).reasons)


# ======================= forged, replayed, reused =======================
def test_editing_a_stored_entry_breaks_the_chain(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    raw_append(e, "mallory", approver="mallory")
    p = tmp_path / "store" / "entry-00000001.json"
    forged = json.loads(p.read_text())
    forged["approver"] = "carol"
    p.chmod(0o644)
    p.write_text(json.dumps(forged))
    s = status(e)
    assert not s.trusted and "custody:chain_invalid:entry_1_hash" in s.reasons


def test_resealing_a_replaced_first_entry_breaks_the_next_link(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    approve(e, "dave")
    p = tmp_path / "store" / "entry-00000001.json"
    first = json.loads(p.read_text())
    first["evidence_ref"] = "rewritten history"
    first["entry_sha256"] = custody._hash_of(first, "entry_sha256")  # perfectly self-consistent
    p.chmod(0o644)
    p.write_text(json.dumps(first))
    assert "custody:chain_invalid:entry_2_prev" in status(e).reasons


def test_an_unsigned_head_pin_detects_truncation_only(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    revoke_dataset(
        e.ds, store=e.store, registry=e.registry, signer=KEYS["carol"],
        evidence_ref="t-2", revoked_at=LATER,
    )  # fmt: skip
    pinned = head_of(e.store.entries())
    assert pinned and pinned.seq == 2 and not status(e).trusted  # revoked
    (tmp_path / "store" / "entry-00000002.json").unlink()  # attacker truncates the revocation
    s = status(e, expected_head=pinned)
    assert not s.trusted and "custody:chain_invalid:truncated_before_pinned_head" in s.reasons
    wrong = ChainHead(1, "0" * 64)
    assert "custody:chain_invalid:pinned_head_mismatch" in status(e, expected_head=wrong).reasons
    for bad in ((0, "0" * 64), (-1, "0" * 64), (1, "zz")):
        with pytest.raises(HistoryError, match="invalid_store_head"):
            ChainHead(*bad)


def test_approval_for_one_dataset_cannot_be_replayed_for_another(tmp_path: Path) -> None:
    a = make_env(tmp_path / "a")
    approve(a)
    b_ds = tmp_path / "b" / "ds"
    from nexora.market_history.clock import ClockAttestation

    mb = go(b_ds, operator="ann", clock_attestation=ClockAttestation("other", "me", 0, 10**9))
    assert mb["dataset_id"] != a.manifest["dataset_id"]
    s = dataset_status(b_ds, None, store=a.store, registry=a.registry, now=CHECK)
    assert not s.trusted and "custody:no_valid_approval" in s.reasons


def test_approval_is_bound_to_the_exact_manifest_so_a_resealed_forgery_loses_trust(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    reseal(e.ds, lambda m: m["provenance"].update(operator="erin"))  # same dataset_id
    s = status(e)
    assert not s.trusted
    assert "custody:binding_mismatch:manifest_sha256" in s.reasons
    reseal(e.ds, lambda m: m.update(symbol="OTHER"))  # new identity: no approval at all
    s = status(e)
    assert not s.trusted and "custody:no_valid_approval" in s.reasons


def test_chunk_tampering_after_approval_is_an_integrity_failure(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    name = e.manifest["chunks"][0]["file"]
    (e.ds / name).chmod(0o644)
    (e.ds / name).write_bytes((e.ds / name).read_bytes() + b"x")
    s = status(e)
    assert not s.trusted and any(r.startswith("integrity:") for r in s.reasons)


def test_a_candidate_for_another_dataset_cannot_be_smuggled_into_an_approval(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    foreign = make_candidate(
        {**e.manifest, "manifest_sha256": "a" * 64}, signer=KEYS["ann"], created_at=NOW
    )
    with pytest.raises(HistoryError, match="candidate_binding_mismatch"):
        approve(e, candidate=foreign)
    raw_append(e, candidate=foreign, candidate_sha256=foreign["candidate_sha256"])
    assert any("candidate_binding_mismatch:manifest_sha256" in r for r in status(e).reasons)


def test_tampered_candidate_and_unbound_candidate_hash_are_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    bad = deepcopy(e.candidate)
    bad["created_at"] = LATER.isoformat()  # self hash no longer matches
    with pytest.raises(HistoryError, match="candidate_malformed:candidate_sha256"):
        approve(e, candidate=bad)
    bad["candidate_sha256"] = custody._hash_of(bad, "candidate_sha256", "operator_signature")
    with pytest.raises(HistoryError, match="operator_signature_invalid"):  # re-hashed only
        approve(e, candidate=bad)
    raw_append(e, candidate_sha256="f" * 64)
    assert any("candidate_hash_not_bound" in r for r in status(e).reasons)
    for mutate, fld in (
        (lambda c: c.update(nonce="short"), "nonce"),
        (lambda c: c.update(schema="x"), "schema"),
        (lambda c: c.update(window_raw_msc=[1.5, 2]), "window_raw_msc"),
        (lambda c: c.update(operator="Ann"), "operator_or_created_at"),
        (lambda c: c.update(operator_signature="00"), "operator_signature"),
        (lambda c: c.update(operator_key_id="x"), "operator_key_id"),
    ):
        c = deepcopy(e.candidate)
        mutate(c)  # type: ignore[no-untyped-call]
        assert candidate_problem(c) == fld
    assert candidate_problem("x") == "schema" and candidate_problem(None) == "schema"


def test_candidates_carry_a_fresh_nonce_and_bind_the_dataset(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    again = make_candidate(e.manifest, signer=KEYS["ann"], created_at=NOW)
    assert (
        again["nonce"] != e.candidate["nonce"]
        and again["manifest_sha256"] == e.manifest["manifest_sha256"]
    )
    assert candidate_problem(again) == ""


def test_unapprovable_datasets_are_refused_at_the_door(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    name = e.manifest["chunks"][0]["file"]
    (e.ds / name).chmod(0o644)
    (e.ds / name).write_bytes(b"garbage\n")
    with pytest.raises(HistoryError, match="dataset_not_approvable"):
        approve(e)


# ======================= revocation =======================
def test_revocation_withdraws_trust_and_even_a_rogue_revocation_fails_closed(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    with pytest.raises(HistoryError, match="key_not_registered"):
        revoke_dataset(
            e.ds, store=e.store, registry=e.registry, signer=KEYS["mallory"],
            evidence_ref="x", revoked_at=LATER,
        )  # fmt: skip
    e.store.append(
        {
            "schema": custody.APPROVAL_SCHEMA, "decision": "revoke",
            **custody._bindings(e.manifest), "revoker": "mallory",
        }  # fmt: skip
    )
    s = status(e)
    assert not s.trusted and any(r.startswith("custody:revoked_at_seq_2") for r in s.reasons)


# ======================= registry =======================
def _reg(*principals: Any) -> dict[str, Any]:
    return {"schema": custody.REGISTRY_SCHEMA, "principals": list(principals)}


_PK = KEYS["carol"].public_hex


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"schema": "nexora.market-history-custodians/1", "custodians": [{"id": "a"}]},
        _reg(),
        _reg({"id": "", "roles": ["custodian"], "keys": [{"ed25519": _PK}]}),
        _reg({"id": "Carol", "roles": ["custodian"], "keys": [{"ed25519": _PK}]}),
        _reg({"id": "cаrol", "roles": ["custodian"], "keys": [{"ed25519": _PK}]}),
        _reg({"id": "a", "roles": ["root"], "keys": [{"ed25519": _PK}]}),
        _reg({"id": "a", "roles": [], "keys": [{"ed25519": _PK}]}),
        _reg({"id": "a", "roles": ["custodian", "custodian"], "keys": [{"ed25519": _PK}]}),
        _reg({"id": "a", "roles": ["custodian"], "keys": []}),
        _reg({"id": "a", "roles": ["custodian"], "keys": [{"ed25519": "ab"}]}),
        _reg({"id": "a", "roles": ["custodian"], "keys": [{"ed25519": _PK, "revoked": "no"}]}),
        _reg({"id": "a", "roles": ["custodian"], "keys": [{"ed25519": _PK, "valid_from": "x"}]}),
        _reg(
            {"id": "a", "roles": ["custodian"],
             "keys": [{"ed25519": _PK, "valid_from": "2026-01-01T00:00:00"}]}
        ),  # fmt: skip
        _reg({"id": "a", "roles": ["custodian"], "keys": [{"ed25519": _PK}], "aliases": ["b"]}),
        _reg(
            {"id": "a", "roles": ["custodian"], "keys": [{"ed25519": _PK}]},
            {"id": "a", "roles": ["operator"], "keys": [{"ed25519": KEYS["ann"].public_hex}]},
        ),
        _reg(  # one key, two principals: who signed would be ambiguous
            {"id": "a", "roles": ["custodian"], "keys": [{"ed25519": _PK}]},
            {"id": "b", "roles": ["operator"], "keys": [{"ed25519": _PK}]},
        ),
    ],
)
def test_malformed_or_ambiguous_registries_are_refused(bad: Any) -> None:
    with pytest.raises(HistoryError, match="registry_malformed"):
        CustodianRegistry.from_json(bad)


def test_a_revoked_key_invalidates_everything_it_signed(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    revoked = registry_json()
    for p in revoked["principals"]:
        if p["id"] == "carol":
            p["keys"][0]["revoked"] = True
    s = status(e, registry=CustodianRegistry.from_json(revoked))
    assert not s.trusted and any("approver_key_revoked" in r for r in s.reasons)


def test_registry_must_live_outside_the_dataset_and_not_be_a_symlink(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    inside = e.ds / "registry.json"
    inside.write_text(json.dumps(REGISTRY_JSON))
    with pytest.raises(HistoryError, match="registry_inside_dataset"):
        load_registry(inside, dataset_dir=e.ds)
    ok = tmp_path / "registry.json"
    ok.write_text(json.dumps(REGISTRY_JSON))
    assert load_registry(ok, dataset_dir=e.ds).find("carol") is not None
    link = tmp_path / "link.json"
    try:
        os.symlink(ok, link)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(HistoryError, match="registry_unreadable"):
        load_registry(link)
    assert load_registry(ok).find("mallory") is None and load_registry(ok).find("CAROL") is None


def test_a_store_inside_the_dataset_folder_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    inside = LocalAppendOnlyStore(e.ds / "evidence")
    s = dataset_status(e.ds, None, store=inside, registry=e.registry, now=CHECK)
    assert not s.trusted and "custody:store_inside_dataset" in s.reasons
    with pytest.raises(HistoryError, match="store_inside_dataset"):
        approve(e, store=inside)


# ======================= the store =======================
def test_store_entries_are_read_only_hash_chained_and_never_overwritten(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    one, two = approve(e), approve(e, "dave")
    assert (one["seq"], two["seq"]) == (1, 2) and one["prev_sha256"] == GENESIS
    assert two["prev_sha256"] == one["entry_sha256"]
    assert verify_chain(e.store.entries()) == ""
    for p in (tmp_path / "store").iterdir():
        assert not p.stat().st_mode & 0o222
    from nexora.market_history.extract import write_once

    with pytest.raises(FileExistsError):  # replacing an existing sequence number is refused
        write_once(tmp_path / "store" / "entry-00000001.json", b"{}")


def test_store_rejects_stray_files_gaps_and_links(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    (tmp_path / "store" / "notes.txt").write_text("x")
    with pytest.raises(HistoryError, match="store_unexpected_entry"):
        e.store.entries()
    assert "custody:store_unreadable:store_unexpected_entry" in status(e).reasons
    (tmp_path / "store" / "notes.txt").unlink()
    os.link(tmp_path / "store" / "entry-00000001.json", tmp_path / "alias")
    with pytest.raises(HistoryError, match="store_unexpected_entry"):
        e.store.entries()
    os.unlink(tmp_path / "alias")
    os.rename(
        tmp_path / "store" / "entry-00000001.json", tmp_path / "store" / "entry-00000003.json"
    )
    with pytest.raises(HistoryError, match="store_unexpected_entry"):
        e.store.entries()


def test_symlinked_store_directory_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    os.symlink(tmp_path / "store", tmp_path / "store-link")
    with pytest.raises(HistoryError, match="store_unreadable"):
        LocalAppendOnlyStore(tmp_path / "store-link").entries()


def test_concurrent_appenders_get_distinct_sequence_numbers(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    results: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        entry = e.store.append(body_for(e))
        with guard:
            results.append(entry["seq"])

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == [1, 2, 3, 4, 5]
    assert verify_chain(e.store.entries()) == ""


def test_the_store_interface_is_provider_neutral(tmp_path: Path) -> None:
    """Any object with append()/entries() works: no filesystem, no cloud SDK."""

    class MemoryStore:
        def __init__(self) -> None:
            self.log: list[dict[str, Any]] = []

        def append(self, body: Any) -> dict[str, Any]:
            head = head_of(self.log)
            entry = seal_entry(body, len(self.log) + 1, head.entry_sha256 if head else GENESIS)
            self.log.append(entry)
            return entry

        def entries(self) -> list[dict[str, Any]]:
            return deepcopy(self.log)

    mem = MemoryStore()
    assert isinstance(mem, EvidenceStore)
    e = make_env(tmp_path)
    approve(e, store=mem)
    assert dataset_status(e.ds, None, store=mem, registry=e.registry, now=CHECK).trusted


@pytest.mark.parametrize(
    "bad_entries",
    [
        None,
        "x",
        [None],
        [{}],
        [{"schema": custody.APPROVAL_SCHEMA, "seq": 2}],
        [{"schema": custody.APPROVAL_SCHEMA, "seq": True, "prev_sha256": GENESIS}],
        [{"schema": "nexora.market-history-approval/1", "seq": 1, "prev_sha256": GENESIS}],
    ],
)
def test_malformed_chains_never_verify(bad_entries: Any) -> None:
    assert verify_chain(bad_entries) != ""


def test_principal_ids_are_canonical_or_refused() -> None:
    for ok in ("carol", "rin@example.org", "a.b-c_d", "x" * 128):
        assert normalize_identity(ok) == ok
    for bad in ("", " carol", "Carol", "cаrol", "ｃarol", "a\x00", "a​b", None, 5, "x" * 129,
                "carol smith", ".carol"):  # fmt: skip
        with pytest.raises(HistoryError, match="invalid_identity"):
            normalize_identity(bad)


# ======================= V3 compatibility / migration =======================
def downgrade_to_v3(ds: Path) -> None:
    m = json.loads((ds / "manifest.json").read_text())
    m["schema"] = "nexora.market-history/3"
    m.pop("filesystem_capabilities")
    m["provenance"].pop("operator")
    seal(ds, m, identity=True)


def test_v3_datasets_still_verify_but_can_never_be_trusted(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    downgrade_to_v3(e.ds)
    assert verify_dataset(e.ds) == []  # integrity still checks out
    m = json.loads((e.ds / "manifest.json").read_text())
    legacy_anchor = make_anchor(m, recorded_by="carol", storage_ref="PR #92", recorded_at=LATER)
    s = dataset_status(e.ds, legacy_anchor)
    assert not s.trusted
    assert "legacy_anchor_is_not_custody_evidence" in s.reasons
    assert "legacy_schema_cannot_be_trusted:nexora.market-history/3" in s.reasons
    # even a complete signed approval chain cannot lift a V3 dataset: migration = re-extract
    e.manifest = m
    e.candidate = make_candidate(
        {**m, "provenance": {**m["provenance"], "operator": "ann"}},
        signer=KEYS["ann"], created_at=NOW,
    )  # fmt: skip
    raw_append(e)
    s = status(e)
    assert not s.trusted and "capability:filesystem_capabilities_absent" in s.reasons
    assert "custody:operator_not_recorded" in s.reasons


def test_a_v4_manifest_missing_v4_fields_is_malformed_not_legacy(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    m = json.loads((e.ds / "manifest.json").read_text())
    m.pop("filesystem_capabilities")
    seal(e.ds, m, identity=False)
    assert "manifest_malformed:missing_filesystem_capabilities" in verify_dataset(e.ds)


def test_capability_record_in_the_manifest_gates_trust_even_with_valid_custody(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    reseal(
        e.ds, lambda m: m["filesystem_capabilities"].update(fs_class="network", platform="win32")
    )
    m = json.loads((e.ds / "manifest.json").read_text())
    e.manifest = m
    e.candidate = make_candidate(m, signer=KEYS["ann"], created_at=NOW)
    raw_append(e)
    s = status(e)
    assert not s.trusted
    assert "capability:platform_unproven:win32" in s.reasons
    assert "capability:filesystem_unproven:network" in s.reasons


def test_hard_defects_still_block_trust_with_valid_custody(tmp_path: Path) -> None:
    from decimal import Decimal

    from nexora.market_history.models import RawTick

    from tests.test_market_history import FakeSource

    ds = tmp_path / "ds"
    crossed = [RawTick(1000 + i * 100, Decimal("101"), Decimal("100")) for i in range(5)]
    m = go(ds, FakeSource(crossed), operator="ann")
    e = Env(
        tmp_path,
        ds,
        m,
        make_candidate(m, signer=KEYS["ann"], created_at=NOW),
        LocalAppendOnlyStore(tmp_path / "store"),
        CustodianRegistry.from_json(REGISTRY_JSON),
    )
    with pytest.raises(HistoryError, match="dataset_not_approvable"):
        approve(e)
    raw_append(e)
    assert any(r.startswith("hard_defect:") for r in status(e).reasons)


def test_evaluate_custody_is_pure_and_reports_every_rejected_approval(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    raw_append(e, "mallory", approver="mallory")
    raw_append(e, evidence_ref=" ")
    v = evaluate_custody(e.manifest, e.store.entries(), e.registry, now=CHECK)
    assert not v.approved and v.head is not None and v.head.seq == 2
    assert "custody:approval_1_rejected:approver_key_not_registered" in v.reasons
    assert "custody:approval_2_rejected:evidence_ref_missing" in v.reasons
    good = approve(e)
    v = evaluate_custody(e.manifest, e.store.entries(), e.registry, now=CHECK)
    assert v.approved and v.approval_seq == good["seq"]


# ======================= CLIs =======================
def run_cli(script: str, *args: str) -> tuple[int, dict[str, Any]]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "packages"), str(ROOT)])}
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
    )
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def key_file(tmp: Path, who: str) -> str:
    path = tmp / "keys" / f"{who}.pem"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        write_signer(KEYS[who], path)
    return str(path)


def test_cli_custody_and_verify_round_trip(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    (tmp_path / "registry.json").write_text(json.dumps(REGISTRY_JSON))
    (tmp_path / "candidate.json").write_text(json.dumps(e.candidate))
    base = ["--dataset", str(e.ds), "--store", str(tmp_path / "store")]
    reg = ["--registry", str(tmp_path / "registry.json")]
    cand = ["--candidate", str(tmp_path / "candidate.json")]
    code, out = run_cli(
        "custody_market_history.py", "approve", *base, *reg, *cand,
        "--signing-key", key_file(tmp_path, "ann"), "--evidence-ref", "t",
    )  # fmt: skip
    assert code == 2 and out["refused"] == "approval_refused"  # ann is not a custodian
    code, out = run_cli(
        "custody_market_history.py", "approve", *base, *reg, *cand,
        "--signing-key", key_file(tmp_path, "carol"), "--evidence-ref", "t",
    )  # fmt: skip
    assert code == 0 and out["seq"] == 1
    code, out = run_cli("custody_market_history.py", "head", "--store", str(tmp_path / "store"))
    assert code == 0 and out["chain"] == "ok"
    vargs = ["--dataset", str(e.ds), "--store", str(tmp_path / "store"), *reg]
    code, out = run_cli("verify_market_history.py", *vargs, "--require-trusted")
    assert (code, out["status"]) == (0, "TRUSTED")
    code, out = run_cli("verify_market_history.py", *vargs, "--store-head", f"1:{'0' * 64}")
    assert code == 1 and "custody:chain_invalid:pinned_head_mismatch" in out["status_reasons"]
    code, out = run_cli("verify_market_history.py", *vargs, "--store-head", "garbage")
    assert code == 2
    code, out = run_cli(
        "custody_market_history.py", "revoke", *base, *reg,
        "--signing-key", key_file(tmp_path, "carol"), "--evidence-ref", "t2",
    )  # fmt: skip
    assert code == 0
    code, out = run_cli("verify_market_history.py", *vargs, "--require-trusted")
    assert code == 1 and any("revoked" in r for r in out["status_reasons"])


def test_cli_keygen_never_overwrites_and_refuses_loose_key_files(tmp_path: Path) -> None:
    out_key = tmp_path / "k.pem"
    code, first = run_cli("custody_market_history.py", "keygen", "--out", str(out_key))
    assert code == 0 and len(first["ed25519"]) == 64
    assert oct(out_key.stat().st_mode & 0o777) == "0o600"
    code, out = run_cli("custody_market_history.py", "keygen", "--out", str(out_key))
    assert code == 2 and out["refused"] == "key_file_exists"
    from nexora.market_history.signing import load_signer

    assert load_signer(out_key).public_hex == first["ed25519"]
    out_key.chmod(0o644)
    with pytest.raises(HistoryError, match="signing_key_permissions"):
        load_signer(out_key)
    (tmp_path / "link.pem").symlink_to(out_key)
    with pytest.raises(HistoryError, match="signing_key_unreadable"):
        load_signer(tmp_path / "link.pem")


def test_cli_registry_inside_dataset_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    (e.ds / "registry.json").write_text(json.dumps(REGISTRY_JSON))
    code, out = run_cli(
        "verify_market_history.py",
        "--dataset",
        str(e.ds),
        "--store",
        str(tmp_path / "store"),
        "--registry",
        str(e.ds / "registry.json"),
    )
    assert code == 2 and out["status_reasons"] == ["registry_inside_dataset"]


def test_extract_cli_candidate_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    from tests.test_market_history_corrective import _cli_files

    spec = importlib.util.spec_from_file_location(
        "x", ROOT / "scripts" / "extract_market_history.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    class Boom:
        def __init__(self) -> None:
            raise AssertionError("terminal attached before validation")

    monkeypatch.setattr(cli, "Mt5ReadOnlyHistorySource", Boom)
    argv = _cli_files(tmp_path)
    key = key_file(tmp_path, "ann")
    monkeypatch.setattr(sys, "argv", [*argv, "--candidate-out", str(tmp_path / "c.json")])
    with pytest.raises(SystemExit):  # a candidate needs the operator identity AND key
        cli.main()
    monkeypatch.setattr(
        sys, "argv", [*argv, "--operator", "ann", "--candidate-out", str(tmp_path / "c.json")]
    )
    with pytest.raises(SystemExit):
        cli.main()
    with_key = ["--operator", "ann", "--operator-key", key]
    monkeypatch.setattr(
        sys, "argv", [*argv, *with_key, "--candidate-out", str(tmp_path / "out" / "c.json")]
    )
    with pytest.raises(HistoryError, match="candidate_inside_dataset"):
        cli.main()
    (tmp_path / "c.json").write_text("{}")
    monkeypatch.setattr(
        sys, "argv", [*argv, *with_key, "--candidate-out", str(tmp_path / "c.json")]
    )
    with pytest.raises(HistoryError, match="candidate_file_exists"):
        cli.main()


def test_lock_name_is_not_a_trust_input(tmp_path: Path) -> None:
    """Sanity: a leftover lock in an approved dataset is an integrity problem, not trust."""
    e = make_env(tmp_path)
    approve(e)
    (e.ds / LOCK_NAME).write_text("{}")
    s = status(e)
    assert not s.trusted and f"integrity:writer_lock_present:{LOCK_NAME}" in s.reasons
