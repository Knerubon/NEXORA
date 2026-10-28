"""Round 3 corrected NEGATIVE regressions for PR #96 (Reviewer A rounds 1 and 2).

Every reproduced exploit from the reviewer suites has a test here that runs the SAME attack and
asserts it is now REJECTED (or fails closed). A pass means the defence held; nothing in this file
asserts a vulnerable outcome. Test ids follow the round-3 work plan (A1-A12 identity, B1-B12
revocation and pin, C1-C12 pinned I/O, D1-D12 lock, recovery and duplicates); the finding each one
covers is in the docstring. Synthetic fixtures, temp dirs and per-run keys only: no MT5, no real
data, no network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import custody
from nexora.market_history.custody import (
    ChainHead,
    CustodianRegistry,
    approve_dataset,
    evaluate_custody,
    head_of,
    make_candidate,
    make_pin,
    revoke_dataset,
    seal_entry,
    verify_chain,
)
from nexora.market_history.extract import dataset_status
from nexora.market_history.models import HistoryError

from tests.test_market_history_corrective import go, reseal
from tests.test_market_history_v3 import NOW
from tests.test_market_history_v4_custody import (
    CHECK,
    KEYS,
    LATER,
    MAX_PIN_AGE,
    REGISTRY_JSON,
    Env,
    approve,
    body_for,
    fresh_candidate,
    fresh_pin,
    make_env,
    principal,
    registry_json,
    status,
)


def _revoke(e: Env, who: str = "carol", at: datetime = LATER + timedelta(hours=1)) -> Any:
    return revoke_dataset(
        e.ds, store=e.store, registry=e.registry, signer=KEYS[who], evidence_ref="t",
        revoked_at=at,
    )  # fmt: skip


def _rewrite_chain(store: Path, keep: list[dict[str, Any]]) -> None:
    """An actor with write access to the store directory deletes entries and re-seals the rest."""
    for p in store.iterdir():
        p.chmod(0o644)
        p.unlink()
    prev = custody.GENESIS
    for i, e in enumerate(keep, 1):
        sealed = seal_entry({k: v for k, v in e.items() if k not in custody._SEAL_KEYS}, i, prev)
        (store / f"entry-{i:08d}.json").write_text(json.dumps(sealed))
        prev = sealed["entry_sha256"]


def _reasons(e: Env, **kw: Any) -> tuple[str, ...]:
    s = status(e, **kw)
    assert not s.trusted, s
    return tuple(s.reasons)


# ============================ Workstream A: identity and custody ===============================
def test_A1_forged_approver_string_is_rejected(tmp_path: Path) -> None:
    """F1 (R1 test_FINDING_A6_rogue_appender_forges_custodian_approval): a raw append of an
    approval naming "carol" by someone who does not hold carol's key."""
    e = make_env(tmp_path)
    e.store.append(body_for(e, "mallory", approver="carol"))
    assert any("approver_key_not_registered" in r for r in _reasons(e))


def test_A2_raw_append_impersonating_an_approved_custodian_is_rejected(tmp_path: Path) -> None:
    """F1: the appender copies carol's id AND key id; only the signature can tell, and does."""
    e = make_env(tmp_path)
    forged = body_for(e, "mallory", approver="carol", approver_key_id=KEYS["carol"].key_id)
    e.store.append(forged)
    assert verify_chain(e.store.entries()) == ""  # storage integrity is fine...
    assert any("approval_signature_invalid" in r for r in _reasons(e))  # ...authenticity is not


def test_A3_valid_signature_from_an_unauthorized_principal_is_rejected(tmp_path: Path) -> None:
    """A perfectly valid Ed25519 signature is not authorization: unregistered key, or a
    registered principal without the custodian role."""
    e = make_env(tmp_path)
    e.store.append(body_for(e, "otto", approver="otto", approver_key_id=KEYS["otto"].key_id))
    e.store.append(body_for(e, "pia", approver="pia", approver_key_id=KEYS["pia"].key_id))
    r = _reasons(e)
    assert "custody:approval_1_rejected:approver_key_not_registered" in r
    assert "custody:approval_2_rejected:approver_principal_lacks_role:custodian" in r


@pytest.mark.parametrize("declared", ["bob", "ann", "erin"])
def test_A4_custodian_who_extracted_cannot_approve_under_another_name(
    tmp_path: Path, declared: str
) -> None:
    """N1 (R2 test_FINDING_A7_custodian_who_extracted_approves_own_dataset_...): carol extracts,
    records some OTHER operator name and signs the attestation with her own key. Whatever name
    she declares, the attestation is not that principal's and the tool and verifier refuse it."""
    e = make_env(tmp_path, operator=declared, creator="carol")
    with pytest.raises(HistoryError, match="operator_key_"):
        approve(e, "carol")
    e.store.append(body_for(e, "carol"))
    assert any("operator_key_" in r for r in _reasons(e))


def test_A4b_dual_role_principal_cannot_be_operator_and_custodian(tmp_path: Path) -> None:
    """N1: erin is registered for both roles and genuinely signs both; still self-approval."""
    e = make_env(tmp_path, operator="erin")
    with pytest.raises(HistoryError, match="self_approval_by_operator"):
        approve(e, "erin")
    e.store.append(body_for(e, "erin"))
    assert any("self_approval_by_operator" in r for r in _reasons(e))


@pytest.mark.parametrize("confusable", ["cаrol", "carol.", "ｃarol", "Carol", "carol​"])
def test_A5_confusable_operator_identity_is_refused(tmp_path: Path, confusable: str) -> None:
    """N1/X5 (R2 Cyrillic 'а', trailing dot): refused at extraction when not canonical; a
    canonical-but-different id ("carol.") is just another, unregistered principal."""
    if confusable == "carol.":
        e = make_env(tmp_path, operator=confusable, creator="carol")
        e.store.append(body_for(e, "dave"))
        assert any("operator_key_" in r for r in _reasons(e))
        return
    with pytest.raises(HistoryError, match="invalid_operator"):
        go(tmp_path / "ds", operator=confusable)
    # a forger who reseals the manifest to carry the confusable gets nowhere either
    e = make_env(tmp_path / "f")
    reseal(e.ds, lambda m: m["provenance"].update(operator=confusable))
    with pytest.raises(HistoryError, match="invalid_identity"):
        make_candidate(json.loads((e.ds / "manifest.json").read_text()), signer=KEYS["ann"],
                       created_at=NOW)  # fmt: skip


def test_A6_operator_identity_changed_after_extraction_voids_the_approval(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    reseal(e.ds, lambda m: m["provenance"].update(operator="erin"))
    assert "custody:binding_mismatch:manifest_sha256" in _reasons(e)


def test_A7_altered_approval_signature_or_body_is_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    good = body_for(e)
    flipped = dict(good, signature=("0" if good["signature"][0] != "0" else "1")
                   + good["signature"][1:])  # fmt: skip
    edited = dict(good, evidence_ref="something else")  # body changed, signature kept
    e.store.append(flipped)
    e.store.append(edited)
    r = _reasons(e)
    assert "custody:approval_1_rejected:approval_signature_invalid" in r
    assert "custody:approval_2_rejected:approval_signature_invalid" in r


def test_A8_dataset_digest_changed_after_approval_loses_trust(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    name = e.manifest["chunks"][0]["file"]
    (e.ds / name).chmod(0o644)
    (e.ds / name).write_bytes((e.ds / name).read_bytes().replace(b"100.1", b"100.2"))
    assert any(r.startswith("integrity:") for r in _reasons(e))
    e2 = make_env(tmp_path / "b")
    approve(e2)
    reseal(e2.ds, lambda m: m.update(tick_count=m["tick_count"]))  # re-sealed, new manifest
    assert status(e2).trusted  # byte-identical reseal: same manifest hash, same approval
    reseal(e2.ds, lambda m: m["quality"].update(note="x"))
    assert any("binding_mismatch" in r or r.startswith("integrity:") for r in _reasons(e2))


def test_A9_expired_or_revoked_custodian_authorization(tmp_path: Path) -> None:
    """F4 (R1 test_FINDING_A6_expired_custodian_backdates_approval): dave's authorization ended
    2026-12-31. A backdated approval inside the window does not count at a 2027 verification;
    a revoked key invalidates everything it signed."""
    e = make_env(tmp_path)
    late = datetime(2027, 6, 1, tzinfo=UTC)
    e.store.append(body_for(e, "dave", approver="dave", approver_key_id=KEYS["dave"].key_id))
    r = _reasons(e, now=late)
    assert any("approver_key_not_valid_at_time" in x for x in r)
    reg = registry_json()
    for p in reg["principals"]:
        if p["id"] == "dave":
            p["keys"][0]["revoked"] = True
    revoked = _reasons(e, registry=CustodianRegistry.from_json(reg))
    assert any("approver_key_revoked" in x for x in revoked)


def test_A10_missing_operator_attestation_is_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    unsigned = dict(e.candidate)
    unsigned.pop("operator_signature")
    e.store.append(body_for(e, candidate=unsigned))
    e.store.append(body_for(e, candidate=None, candidate_sha256=None))
    r = _reasons(e)
    assert "custody:approval_1_rejected:candidate_malformed:operator_signature" in r
    assert "custody:approval_2_rejected:candidate_malformed:schema" in r


def test_A11_legitimate_distinct_operator_and_custodian_is_trusted(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    s = status(e)
    assert s.trusted and s.reasons == ()


def test_A12_key_rotation_makes_old_authorizations_stale(tmp_path: Path) -> None:
    """carol rotates keys: the old key's window ends before verification. Approvals signed with
    it stop counting (fail closed); a fresh approval with the new key restores trust."""
    from nexora.market_history.signing import generate_signer

    new = generate_signer()
    reg = registry_json()
    for p in reg["principals"]:
        if p["id"] == "carol":
            cut = (LATER + timedelta(days=1)).isoformat()
            p["keys"] = [
                {"ed25519": KEYS["carol"].public_hex, "valid_until": cut},
                {"ed25519": new.public_hex, "valid_from": cut},
            ]
    e = make_env(tmp_path)
    e.registry = CustodianRegistry.from_json(reg)
    e.store.append(body_for(e))  # signed with the old key while it was valid
    assert any("approver_key_not_valid_at_time" in r for r in _reasons(e))
    fresh_candidate(e)
    approve_dataset(
        e.ds, e.candidate, store=e.store, registry=e.registry, signer=new,
        evidence_ref="rotated", approved_at=LATER + timedelta(days=2),
    )  # fmt: skip
    assert status(e).trusted


# ============================ Workstream B: revocation and pin =================================
def test_B1_revoked_approval_replayed_byte_identically_stays_revoked(tmp_path: Path) -> None:
    """F2 (R1 test_FINDING_A7_replay_of_revoked_approval_restores_trust, R2 A8)."""
    e = make_env(tmp_path)
    first = approve(e)
    _revoke(e)
    assert not status(e).trusted
    e.store.append({k: v for k, v in first.items() if k not in custody._SEAL_KEYS})
    r = _reasons(e)
    assert "custody:approval_3_rejected:replayed_approval_id" in r


def test_B2_replay_with_a_new_id_or_sequence_cannot_be_forged(tmp_path: Path) -> None:
    """F2: changing approval_id (to dodge replay detection) or the acknowledged list breaks the
    custodian signature; the attacker cannot re-sign."""
    e = make_env(tmp_path)
    first = approve(e)
    _revoke(e)
    body = {k: v for k, v in first.items() if k not in custody._SEAL_KEYS}
    e.store.append(dict(body, approval_id="ab" * 16))
    e.store.append(dict(body, approval_id="cd" * 16, acknowledged_revocations=["ef" * 16]))
    r = _reasons(e)
    assert "custody:approval_3_rejected:approval_signature_invalid" in r
    assert "custody:approval_4_rejected:approval_signature_invalid" in r


def test_B2b_custodian_resigning_an_old_decision_must_acknowledge_the_revocation(
    tmp_path: Path,
) -> None:
    """Even a real custodian replaying the OLD approval content (new id, fresh signature) after a
    revocation is refused: it does not name the revocation it answers, and reuses the candidate."""
    e = make_env(tmp_path)
    approve(e)
    _revoke(e)
    e.store.append(body_for(e, approval_id="12" * 16))  # acknowledged_revocations == []
    r = _reasons(e)
    assert any(x.startswith("custody:approval_3_rejected:") for x in r)
    assert any(("revocation_not_acknowledged" in x) or ("candidate_reused" in x) for x in r)


def test_B3_same_approval_under_a_new_alias_is_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    first = approve(e)
    _revoke(e)
    body = {k: v for k, v in first.items() if k not in custody._SEAL_KEYS}
    e.store.append(dict(body, approver="dave", approver_key_id=KEYS["dave"].key_id))
    e.store.append(dict(body, approver="carol@corp.example"))
    r = _reasons(e)
    assert "custody:approval_3_rejected:approval_signature_invalid" in r
    assert "custody:approval_4_rejected:approver_key_belongs_to_another_principal" in r


def test_B4_valid_new_reapproval_after_revocation_restores_trust(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    rv = _revoke(e)
    with pytest.raises(HistoryError, match="candidate_reused"):  # needs a fresh attestation
        approve(e, at=LATER + timedelta(hours=2))
    fresh_candidate(e)
    again = approve(e, at=LATER + timedelta(hours=2))
    assert again["acknowledged_revocations"] == [rv["revocation_id"]]
    assert status(e).trusted


def test_B5_missing_revocation_entry_breaks_lineage(tmp_path: Path) -> None:
    """Approve, revoke, re-approve; then the revocation is deleted and the chain re-sealed. The
    re-approval names a revocation that is gone: history was removed, never TRUSTED."""
    e = make_env(tmp_path)
    approve(e)
    _revoke(e)
    fresh_candidate(e)
    approve(e, at=LATER + timedelta(hours=2))
    entries = e.store.entries()
    _rewrite_chain(e.store.location, [entries[0], entries[2]])
    r = _reasons(e)
    assert "custody:revocation_lineage_broken" in r
    assert "custody:approval_2_rejected:acknowledged_revocation_missing" in r


def test_B6_rechained_store_after_revocation_removal_is_caught_by_the_independent_pin(
    tmp_path: Path,
) -> None:
    """N4 (R2 test_FINDING_A6_rechained_store_without_the_revocation_...): the pin authority
    pinned the store after the revocation; the rewrite cannot match it, and the pin authority's
    own tool refuses to pin the rewritten store as a continuation."""
    e = make_env(tmp_path)
    approve(e)
    other = make_env(tmp_path / "o")
    approve(other, store=e.store)
    _revoke(e)
    pin_after_revocation = fresh_pin(e.store)
    assert pin_after_revocation is not None
    entries = e.store.entries()
    _rewrite_chain(e.store.location, entries[:2])
    assert verify_chain(e.store.entries()) == ""
    r = _reasons(e, pin=pin_after_revocation)
    assert "custody:pin_ahead_of_store_truncated" in r
    with pytest.raises(HistoryError, match="store_does_not_extend_previous_pin"):
        make_pin(e.store.entries(), signer=KEYS["pia"], pinned_by="pia",
                 captured_at=CHECK, previous=pin_after_revocation)  # fmt: skip


def test_B7_head_taken_from_the_same_store_is_never_an_independent_pin(tmp_path: Path) -> None:
    """F5/N4/X3: an unsigned head read from the store being verified does not qualify; neither
    does a "pin" signed by a custodian or carrying a forged signature."""
    e = make_env(tmp_path)
    approve(e)
    same_store = head_of(e.store.entries())
    assert "custody:independent_pin_missing" in _reasons(e, pin=None, expected_head=same_store)
    by_custodian = make_pin(e.store.entries(), signer=KEYS["carol"], pinned_by="carol",
                            captured_at=CHECK - timedelta(minutes=1), initial=True)  # fmt: skip
    assert "custody:pin_principal_lacks_role:pin_authority" in _reasons(e, pin=by_custodian)
    good = fresh_pin(e.store)
    assert good is not None
    forged = dict(good, seq=good["seq"])
    forged["captured_at"] = CHECK.isoformat()
    assert "custody:pin_signature_invalid" in _reasons(e, pin=forged)


def test_B8_stale_independently_captured_pin_is_refused(tmp_path: Path) -> None:
    """F5 (R1 test_FINDING_A6_stale_pinned_head_does_not_protect_a_later_revocation): a pin
    captured at approval time, revocation later, revocation deleted. The old pin matches the
    truncated store exactly, but it is older than the verifier's maximum age."""
    e = make_env(tmp_path)
    approve(e)
    old_pin = make_pin(e.store.entries(), signer=KEYS["pia"], pinned_by="pia",
                       captured_at=LATER + timedelta(minutes=5), initial=True)  # fmt: skip
    _revoke(e)
    (e.store.location / "entry-00000002.json").unlink()
    assert "custody:pin_stale" in _reasons(e, pin=old_pin)


def test_B9_correct_independent_pin_with_untampered_store_is_trusted(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    pin = fresh_pin(e.store)
    assert status(e, pin=pin).trusted


def test_B10_same_sequence_with_a_different_head_digest_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    fresh_candidate(e)
    approve(e, "dave")
    pin = fresh_pin(e.store)
    assert pin is not None and pin["seq"] == 2
    entries = e.store.entries()
    replacement = dict(entries[1], evidence_ref="different")
    _rewrite_chain(e.store.location, [entries[0], replacement])
    assert "custody:pin_head_mismatch" in _reasons(e, pin=pin)
    other = make_env(tmp_path / "x")
    approve(other)
    assert "custody:pin_store_mismatch" in _reasons(other, pin=pin)


def test_B11_missing_pin_or_missing_age_policy_is_untrusted(tmp_path: Path) -> None:
    """F5 (R2 test_FINDING_A9_cli_require_trusted_succeeds_without_any_head_pin) is covered by
    the CLI round-trip test; this is the API side."""
    e = make_env(tmp_path)
    approve(e)
    assert "custody:independent_pin_missing" in _reasons(e, pin=None)
    assert "custody:pin_max_age_not_set" in _reasons(e, max_pin_age=None)
    pinless = dataset_status(e.ds, None, store=e.store, registry=e.registry, now=CHECK)
    assert not pinless.trusted


def test_B12_unauthorized_re_anchoring_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    with pytest.raises(HistoryError, match="no_previous_pin"):
        make_pin(e.store.entries(), signer=KEYS["pia"], pinned_by="pia", captured_at=CHECK)
    # the approving custodian may not also be the pin authority for that approval
    reg = registry_json()
    reg["principals"] = [p for p in reg["principals"] if p["id"] != "pia"]
    for p in reg["principals"]:
        if p["id"] == "carol":
            p["roles"] = ["custodian", "pin_authority"]
    e.registry = CustodianRegistry.from_json(reg)
    self_pin = make_pin(e.store.entries(), signer=KEYS["carol"], pinned_by="carol",
                        captured_at=CHECK - timedelta(minutes=1), initial=True)  # fmt: skip
    assert "custody:pin_not_independent" in _reasons(e, pin=self_pin)
    # an explicit re-anchor is recorded inside the signed pin, never implicit
    re = make_pin(e.store.entries(), signer=KEYS["pia"], pinned_by="pia", captured_at=CHECK,
                  reanchor_ref="ticket-77")  # fmt: skip
    assert re["reanchor_ref"] == "ticket-77" and re["previous_pin_id"] is None


def test_B_approval_appended_after_the_pin_is_not_covered(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    e.store.append(body_for(e, "mallory", approver="mallory"))
    pin = fresh_pin(e.store)  # pins seq 1 only
    approve(e)
    v = evaluate_custody(e.manifest, e.store.entries(), e.registry, pin=pin,
                         max_pin_age=MAX_PIN_AGE, now=CHECK)  # fmt: skip
    assert not v.approved
    assert "custody:approval_2_rejected:approval_not_covered_by_pin" in v.reasons
    assert status(e).trusted  # a fresh pin that covers it


def test_B_revocation_without_an_identifier_cannot_be_acknowledged(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    e.store.append({"schema": custody.APPROVAL_SCHEMA, "decision": "revoke",
                    **custody._bindings(e.manifest)})  # fmt: skip
    fresh_candidate(e)
    with pytest.raises(HistoryError, match="revocation_2_unidentifiable"):
        approve(e, at=LATER + timedelta(hours=2))
    assert "custody:revocation_2_unidentifiable" in _reasons(e)


def test_B_registry_fixture_is_unchanged() -> None:
    assert REGISTRY_JSON == registry_json()
    assert principal("ann", ["operator"])["keys"][0]["ed25519"] == KEYS["ann"].public_hex
    assert ChainHead(1, "0" * 64).seq == 1
