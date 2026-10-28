"""Round 3 corrected NEGATIVE regressions for PR #96 (Reviewer A rounds 1 and 2).

Every reproduced exploit from the reviewer suites has a test here that runs the SAME attack and
asserts it is now REJECTED (or fails closed). A pass means the defence held; nothing in this file
asserts a vulnerable outcome. Test ids follow the round-3 work plan (A1-A12 identity, B1-B12
revocation and pin, C1-C12 pinned I/O, D1-D12 lock, recovery and duplicates); the finding each one
covers is in the docstring. Synthetic fixtures, temp dirs and per-run keys only: no MT5, no real
data, no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import nexora.market_history.extract as ex
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
from nexora.market_history.extract import (
    ExtractionRequest,
    dataset_status,
    open_trusted,
    recover_interrupted_publish,
    verify_dataset,
)
from nexora.market_history.fsutil import Dir
from nexora.market_history.lock import LOCK_NAME, acquire
from nexora.market_history.models import HistoryError, RawTick
from nexora.market_history.quality import (
    LEGACY_QUALITY_VERSION,
    QualityAccumulator,
    QualityConfig,
    quality_report,
)

from tests import _pr95_duplicate_oracle as oracle
from tests.test_market_history import FakeSource, conv, mk
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

ROOT = Path(__file__).resolve().parent.parent
ENVV = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'packages'}"}


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


def test_B4b_signed_reapproval_reusing_the_revoked_candidate_does_not_restore_trust(
    tmp_path: Path,
) -> None:
    """F2: a real custodian appending directly (no tool check) a fresh, correctly signed approval
    that acknowledges the revocation but reuses the OLD operator attestation is refused by the
    verifier itself: trust withdrawn by a revocation needs a fresh operator attestation."""
    e = make_env(tmp_path)
    approve(e)
    rv = _revoke(e)
    e.store.append(
        body_for(
            e, approval_id="34" * 16, acknowledged_revocations=[rv["revocation_id"]],
            approved_at=(LATER + timedelta(hours=2)).isoformat(),
        )
    )  # fmt: skip
    r = _reasons(e)
    assert "custody:approval_3_rejected:candidate_reused" in r


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


# ============================ Workstream C: pinned I/O and TOCTOU ==============================


def _tampered_twin(tmp: Path, name: str = "bad") -> Path:
    """Byte-identical extraction (same manifest) whose first chunk is then edited."""
    bad = tmp / name
    go(bad, operator="ann")
    chunk = sorted(bad.glob("chunk-*"))[0]
    chunk.chmod(0o644)
    chunk.write_bytes(chunk.read_bytes().replace(b"100.1", b"100.2"))
    assert verify_dataset(bad)
    return bad


def _open_trusted(e: Env) -> Any:
    pin = fresh_pin(e.store)
    assert pin is not None
    return open_trusted(e.ds, store=e.store, registry=e.registry, pin=pin,
                        max_pin_age=MAX_PIN_AGE, now=CHECK)  # fmt: skip


def test_C1_directory_swap_between_verify_and_status_is_never_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3 (R1 test_FINDING_A3_status_verifies_one_directory_and_trusts_another): the same swap,
    at the point that used to separate the integrity check from the custody decision. There is
    now one pinned object for both, and a path that no longer names it is refused."""
    e = make_env(tmp_path)
    approve(e)
    bad = _tampered_twin(tmp_path)
    real = ex._verify_in

    def swap_after(d: Dir, *a: Any) -> Any:
        out = real(d, *a)
        os.rename(e.ds, tmp_path / "genuine")
        os.rename(bad, e.ds)
        return out

    monkeypatch.setattr(ex, "_verify_in", swap_after)
    s = status(e)
    assert not s.trusted
    assert "integrity:dataset_path_replaced_during_check" in s.reasons
    monkeypatch.setattr(ex, "_verify_in", real)
    assert not status(e).trusted  # what now sits at the path fails integrity


def test_C2_dataset_swap_between_verify_and_approval_appends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N2 (R2 test_FINDING_A5_approve_verifies_one_folder_and_approves_anothers_manifest)."""
    e = make_env(tmp_path)
    bad = _tampered_twin(tmp_path)
    real = ex.usability_blockers_in

    def swap_after(d: Dir, **kw: Any) -> Any:
        out = real(d, **kw)
        os.rename(e.ds, tmp_path / "genuine")
        os.rename(bad, e.ds)
        return out

    monkeypatch.setattr(ex, "usability_blockers_in", swap_after)
    with pytest.raises(HistoryError, match="dataset_path_replaced_during_approval"):
        approve(e)
    assert e.store.entries() == []


def test_C3_each_chunk_is_read_exactly_once_and_hashed_bytes_are_parsed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N3 (R2 test_FINDING_A3_chunk_swapped_between_hash_and_parse_is_not_noticed): the old
    exploit swapped the file between the hash open and the parse/recompute opens. Those opens
    no longer exist: one open per file, and the parser receives the very bytes that were hashed."""
    e = make_env(tmp_path)
    opens: dict[str, int] = {}
    real_open = Dir.open_regular

    def counting(self: Dir, name: str) -> Any:
        opens[name] = opens.get(name, 0) + 1
        return real_open(self, name)

    hashed: list[bytes] = []
    parsed: list[bytes] = []
    real_snap, real_scan = ex._snapshot, ex._scan_bytes

    def snap(d: Dir, name: str, limit: int) -> bytes:
        data = real_snap(d, name, limit)
        if name.startswith("chunk-"):
            hashed.append(data)
        return data

    def scan(data: bytes, *a: Any) -> Any:
        parsed.append(data)
        # the old attack: replace the file at this name right now
        name = e.manifest["chunks"][len(parsed) - 1]["file"]
        (e.ds / ".x").write_bytes(data.replace(b"100.1", b"100.2"))
        os.replace(e.ds / ".x", e.ds / name)
        return real_scan(data, *a)

    monkeypatch.setattr(Dir, "open_regular", counting)
    monkeypatch.setattr(ex, "_snapshot", snap)
    monkeypatch.setattr(ex, "_scan_bytes", scan)
    assert verify_dataset(e.ds) == []  # consistent: it verified ONE snapshot
    assert all(n == 1 for n in opens.values()), opens
    assert [id(x) for x in hashed] == [id(x) for x in parsed]
    monkeypatch.undo()
    assert verify_dataset(e.ds)  # the swapped files are not what was verified


def test_C4_same_directory_rename_after_trust_is_caught_by_the_trusted_reader(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    with _open_trusted(e) as td:
        name = td.manifest["chunks"][0]["file"]
        original = (e.ds / name).read_bytes()
        (e.ds / ".x").write_bytes(original.replace(b"100.1", b"100.2"))
        os.replace(e.ds / ".x", e.ds / name)
        with pytest.raises(HistoryError, match="trusted_dataset_changed"):
            list(td.iter_ticks())


def test_C5_symlink_replacement_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    name = e.manifest["chunks"][0]["file"]
    os.rename(e.ds / name, tmp_path / "real-chunk")
    os.symlink(tmp_path / "real-chunk", e.ds / name)
    assert any("integrity:chunk_is_symlink" in r for r in _reasons(e))
    e2 = make_env(tmp_path / "b")
    approve(e2)
    os.rename(e2.ds, tmp_path / "b" / "elsewhere")
    os.symlink(tmp_path / "b" / "elsewhere", e2.ds)
    assert any(r.startswith("integrity:dataset_dir_unsafe") for r in _reasons(e2))


def test_C6_hardlink_alias_is_detected_on_the_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The alias is created AFTER the name check and BEFORE the open (the old stat-then-open
    gap); the link count is now read from the open descriptor."""
    e = make_env(tmp_path)
    approve(e)
    name = e.manifest["chunks"][0]["file"]
    real_kind = Dir.kind

    def kind_then_link(self: Dir, n: str) -> str:
        k = real_kind(self, n)
        if n == name and not (tmp_path / "alias").exists():
            os.link(e.ds / name, tmp_path / "alias")
        return k

    monkeypatch.setattr(Dir, "kind", kind_then_link)
    assert any(f"integrity:chunk_multiple_links:{name}" in r for r in _reasons(e))


def test_C7_manifest_replacement_or_alias_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    os.link(e.ds / "manifest.json", tmp_path / "m-alias")
    assert "integrity:manifest_multiple_links" in _reasons(e)
    os.unlink(tmp_path / "m-alias")
    reseal(e.ds, lambda m: m["provenance"].update(retrieval_note="replaced"))
    assert "custody:binding_mismatch:manifest_sha256" in _reasons(e)


def test_C8_file_mutation_during_verification_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = make_env(tmp_path)
    approve(e)
    name = e.manifest["chunks"][0]["file"]
    (e.ds / name).chmod(0o644)  # the attacker owns the file
    real_open = Dir.open_regular

    class MutatesOnFirstRead:
        """Delegates to the real handle; the first read() appends to the same inode first, i.e.
        after the reader took its 'before' fstat (in-place mutation mid-read)."""

        def __init__(self, fh: Any) -> None:
            self.fh, self.done = fh, False

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc: object) -> None:
            self.fh.close()

        def fileno(self) -> int:
            return int(self.fh.fileno())

        def read(self, n: int = -1) -> bytes:
            if not self.done:
                self.done = True
                with open(e.ds / name, "ab") as w:
                    w.write(b"\n")
            return bytes(self.fh.read(n))

    def mutate_on_open(self: Dir, n: str) -> Any:
        fh = real_open(self, n)
        return MutatesOnFirstRead(fh) if n == name else fh

    monkeypatch.setattr(Dir, "open_regular", mutate_on_open)
    r = _reasons(e)
    assert any(f"integrity:chunk_unreadable:{name}" in x for x in r), r


def test_C9_cross_process_swapping_never_feeds_tampered_bytes_to_a_trusted_reader(
    tmp_path: Path,
) -> None:
    """A separate process flips one chunk between its genuine and a tampered version as fast as
    it can, while this process repeatedly decides trust and reads through the trusted reader.
    Every completed read must yield exactly the genuine ticks; anything else must raise."""
    e = make_env(tmp_path)
    approve(e)
    genuine = list(ex._iter_ticks(open(e.ds / e.manifest["chunks"][0]["file"], "rb")))
    expected = [t for c in e.manifest["chunks"]
                for t in ex._iter_ticks(open(e.ds / c["file"], "rb"))]  # fmt: skip
    assert genuine
    name = e.manifest["chunks"][0]["file"]
    code = textwrap.dedent(
        """
        import os, sys, time
        d, name = sys.argv[1], sys.argv[2]
        good = open(os.path.join(d, name), "rb").read()
        bad = good.replace(b"100.1", b"100.2")
        end = time.time() + float(sys.argv[3])
        i = 0
        while time.time() < end:
            tmp = os.path.join(d, ".flip")
            with open(tmp, "wb") as fh:
                fh.write(bad if i % 2 == 0 else good)
            os.replace(tmp, os.path.join(d, name))
            i += 1
        with open(os.path.join(d, ".flip"), "wb") as fh:
            fh.write(good)
        os.replace(os.path.join(d, ".flip"), os.path.join(d, name))
        """
    )
    proc = subprocess.Popen([sys.executable, "-c", code, str(e.ds), name, "4"])
    trusted_reads = refused = 0
    try:
        for _ in range(40):
            try:
                with _open_trusted(e) as td:
                    got = list(td.iter_ticks())
            except HistoryError:
                refused += 1
                continue
            assert got == expected  # never the tampered bytes
            trusted_reads += 1
    finally:
        proc.wait(30)
    assert refused > 0  # the race was really exercised


def test_C10_unsupported_filesystem_capability_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = make_env(tmp_path)
    approve(e)
    from nexora.market_history import fsutil

    monkeypatch.setattr(fsutil, "DIR_FD_SUPPORTED", False)
    r = _reasons(e)
    assert "capability:verifier_lacks_dir_fd_nofollow" in r
    monkeypatch.undo()
    reseal(e.ds, lambda m: m["filesystem_capabilities"].update(fs_class="network"))
    assert "capability:filesystem_unproven:network" in _reasons(e)


def test_C11_legitimate_stable_dataset_is_trusted_and_digests_are_returned(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    s = status(e)
    assert s.trusted
    assert (s.dataset_id, s.manifest_sha256, s.content_sha256) == (
        e.manifest["dataset_id"],
        e.manifest["manifest_sha256"],
        e.manifest["content_sha256"],
    )
    with _open_trusted(e) as td:
        assert len(list(td.iter_ticks())) == e.manifest["tick_count"]
        assert td.status.manifest_sha256 == e.manifest["manifest_sha256"]
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        open_trusted(e.ds, store=e.store, registry=e.registry, pin=None,  # type: ignore[arg-type]
                     max_pin_age=MAX_PIN_AGE, now=CHECK)  # fmt: skip


def test_C12_interruption_during_pinned_io_fails_closed_and_leaks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = make_env(tmp_path)
    approve(e)
    real = ex._snapshot
    calls = [0]

    def flaky(d: Dir, name: str, limit: int) -> bytes:
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("EIO")
        return real(d, name, limit)

    before = len(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(ex, "_snapshot", flaky)
    s = status(e)
    assert not s.trusted and any(r.startswith("integrity:") for r in s.reasons)
    monkeypatch.undo()
    assert len(os.listdir("/proc/self/fd")) == before  # pinned descriptor closed
    killer = textwrap.dedent(
        """
        import os, signal, sys
        from pathlib import Path
        import nexora.market_history.extract as ex
        real = ex._snapshot
        def die(d, name, limit):
            if name.startswith("chunk-"): os.kill(os.getpid(), signal.SIGKILL)
            return real(d, name, limit)
        ex._snapshot = die
        ex.verify_dataset(Path(sys.argv[1]))
        """
    )
    r = subprocess.run([sys.executable, "-c", killer, str(e.ds)], cwd=ROOT, env=ENVV, timeout=60)
    assert r.returncode != 0
    assert status(e).trusted  # a killed reader leaves nothing behind


# ============================ Workstream D: lock, recovery, duplicates =========================

PR95_ORACLE_SHA256 = "81935643deea97ded77292505294a211a4862e8a0b6aad33371291f777beeb9d"


def test_D0_the_pr95_oracle_copy_is_verbatim() -> None:
    """X4: the comparator is PR #95's oracle at 3cc8869, byte for byte (Reviewer A round 2
    manifest); it was not written or edited by the remediation."""
    data = (ROOT / "tests" / "_pr95_duplicate_oracle.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == PR95_ORACLE_SHA256


HOLD = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from nexora.market_history.fsutil import Dir
    from nexora.market_history.lock import acquire
    lock = acquire(Dir.open(Path(sys.argv[1])))
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
    lock.release()
    """
)


def test_D1_external_unlink_of_a_live_writer_lock_admits_no_second_owner(tmp_path: Path) -> None:
    """F6 (R1 test_FINDING_A1_external_unlink_of_live_lock_allows_second_owner)."""
    proc = subprocess.Popen([sys.executable, "-c", HOLD, str(tmp_path), "5"], cwd=ROOT, env=ENVV,
                            stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
        os.unlink(tmp_path / LOCK_NAME)  # the "attack": remove the name
        with Dir.open(tmp_path) as d, pytest.raises(HistoryError, match="writer_lock_held"):
            acquire(d)
    finally:
        proc.kill()
        proc.wait(30)
    with Dir.open(tmp_path) as d:  # kernel released everything on exit: free again
        acquire(d).release()


class _BlockingSource(FakeSource):
    def __init__(self, ticks: list[RawTick], block_at: int) -> None:
        super().__init__(ticks)
        self.block_at = block_at
        self.entered = threading.Event()
        self.go_on = threading.Event()

    def ticks(self, symbol: str, a: int, b: int, *, max_ticks: int) -> list[RawTick]:
        if a == self.block_at:
            self.entered.set()
            assert self.go_on.wait(20)
        return list(super().ticks(symbol, a, b, max_ticks=max_ticks))


def test_D1b_lock_name_removed_mid_extraction_stops_the_writer_and_admits_no_other(
    tmp_path: Path,
) -> None:
    """F6/I-A2 (R2 test_FINDING_A2_lock_name_removed_mid_extraction_admits_a_concurrent_writer):
    the second writer is refused at the directory lock; the first notices the lost record before
    its next publication and stops (fail closed); nothing half-written is ever trusted."""
    ds = tmp_path / "ds"
    src = _BlockingSource([mk(1000 + i * 500) for i in range(10)], block_at=3000)
    errors: list[BaseException] = []

    def writer1() -> None:
        try:
            go(ds, source=src, operator="ann")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=writer1)
    t.start()
    assert src.entered.wait(20)
    os.unlink(ds / LOCK_NAME)
    with pytest.raises(HistoryError, match="writer_lock_held"):
        go(ds, operator="ann")  # no second owner while writer 1 lives
    src.go_on.set()
    t.join(30)
    assert errors and isinstance(errors[0], HistoryError) and errors[0].code == "writer_lock_lost"
    assert not (ds / "manifest.json").exists()
    assert "manifest_missing" in verify_dataset(ds)
    go(ds, operator="ann")  # a clean re-run completes it
    assert verify_dataset(ds) == []


def test_D2_competing_extraction_processes_exactly_one_completes(tmp_path: Path) -> None:
    code = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        from tests.test_market_history_corrective import go
        from nexora.market_history.models import HistoryError
        t0 = float(sys.argv[2])
        while time.time() < t0: pass
        try:
            go(Path(sys.argv[1]), operator="ann"); print("OK")
        except HistoryError as e:
            print(e.code)
        """
    )
    import time

    for rnd in range(3):
        ds = tmp_path / f"ds{rnd}"
        t0 = time.time() + 1.5
        argv = [sys.executable, "-c", code, str(ds), str(t0)]
        procs = [
            subprocess.Popen(argv, cwd=ROOT, env=ENVV, stdout=subprocess.PIPE, text=True)
            for _ in range(6)
        ]
        outs = [p.communicate(timeout=60)[0].strip() for p in procs]
        assert outs.count("OK") == 1, outs
        assert set(outs) - {"OK"} <= {"writer_lock_held", "manifest_exists_dataset_is_immutable"}
        assert verify_dataset(ds) == []


CRASH_APPEND = textwrap.dedent(
    """
    import os, sys, signal, json
    from pathlib import Path
    from nexora.market_history.fsutil import Dir
    stage, store = sys.argv[1], Path(sys.argv[2])
    real_link = Dir.link
    def link(self, src, dst):
        if stage == "before_link": os.kill(os.getpid(), signal.SIGKILL)
        real_link(self, src, dst)
        if stage == "after_link": os.kill(os.getpid(), signal.SIGKILL)
    Dir.link = link
    from nexora.market_history.custody import LocalAppendOnlyStore
    LocalAppendOnlyStore(store).append(json.loads(sys.argv[3]))
    """
)


def _crash_append(stage: str, e: Env, body: dict[str, Any]) -> int:
    r = subprocess.run([sys.executable, "-c", CRASH_APPEND, stage, str(e.store.location),
                        json.dumps(body)], cwd=ROOT, env=ENVV, timeout=60)  # fmt: skip
    return r.returncode


def test_D3_crash_between_link_and_unlink_does_not_wedge_the_store(tmp_path: Path) -> None:
    """N5 (R2 test_FINDING_A12_crash_between_link_and_temp_unlink_wedges_the_whole_store)."""
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    assert _crash_append("after_link", e, body_for(e, "dave")) == -signal.SIGKILL
    names = sorted(p.name for p in e.store.location.iterdir())
    assert any(".tmp-" in n for n in names) and "entry-00000002.json" in names
    assert len(e.store.entries()) == 2 and verify_chain(e.store.entries()) == ""  # readable
    assert status(e).trusted  # every dataset in the store stays verifiable
    other = make_env(tmp_path / "o")
    approve(other, store=e.store)  # appendable; the append also finished the crashed one
    assert not any(".tmp-" in p.name for p in e.store.location.iterdir())


def test_D4_crash_during_manifest_publication_is_recoverable(tmp_path: Path) -> None:
    """N5 (R2 test_FINDING_A12_crash_between_manifest_link_and_temp_unlink_bricks_the_dataset)."""
    code = textwrap.dedent(
        """
        import os, signal, sys
        from pathlib import Path
        from nexora.market_history.fsutil import Dir
        real = Dir.link
        def link(self, src, dst):
            real(self, src, dst)
            if dst == "manifest.json": os.kill(os.getpid(), signal.SIGKILL)
        Dir.link = link
        from tests.test_market_history_corrective import go
        go(Path(sys.argv[1]), operator="ann")
        """
    )
    ds = tmp_path / "ds"
    r = subprocess.run([sys.executable, "-c", code, str(ds)], cwd=ROOT, env=ENVV, timeout=60)
    assert r.returncode == -signal.SIGKILL
    assert "manifest_multiple_links" in verify_dataset(ds)  # blocked until recovered (fail closed)
    out = subprocess.run([sys.executable, "scripts/recover_market_history.py", "--dataset",
                          str(ds)], cwd=ROOT, env=ENVV, capture_output=True, text=True,
                         timeout=60)  # fmt: skip
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 0 and len(report["removed_temp_names"]) == 1
    assert report["removed_temp_names"][0].startswith("manifest.json.tmp-")
    assert report["verify_problems"] == []
    assert recover_interrupted_publish(ds) == []  # deterministic: nothing left to do


def test_D4b_extract_rerun_finishes_the_interrupted_publication_itself(tmp_path: Path) -> None:
    code = textwrap.dedent(
        """
        import os, signal, sys
        from pathlib import Path
        from nexora.market_history.fsutil import Dir
        real = Dir.link
        def link(self, src, dst):
            real(self, src, dst)
            if dst == "manifest.json": os.kill(os.getpid(), signal.SIGKILL)
        Dir.link = link
        from tests.test_market_history_corrective import go
        go(Path(sys.argv[1]), operator="ann")
        """
    )
    ds = tmp_path / "ds"
    subprocess.run([sys.executable, "-c", code, str(ds)], cwd=ROOT, env=ENVV, timeout=60)
    with pytest.raises(HistoryError, match="manifest_exists_dataset_is_immutable"):
        go(ds, operator="ann")  # still immutable...
    assert verify_dataset(ds) == []  # ...but the interrupted publication is now complete


def test_D5_recovery_of_a_completed_append_deletes_no_evidence(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert _crash_append("before_link", e, body_for(e, "dave")) == -signal.SIGKILL  # orphan
    assert _crash_append("after_link", e, body_for(e, "dave")) == -signal.SIGKILL  # twin
    before = {p.name: p.read_bytes() for p in e.store.location.iterdir() if ".tmp-" not in p.name}
    removed = e.store.recover()
    assert len(removed) == 1 and removed[0].startswith("entry-00000002.json.tmp-")
    after = {p.name: p.read_bytes() for p in e.store.location.iterdir() if ".tmp-" not in p.name}
    assert after == before  # every entry intact
    assert sum(".tmp-" in p.name for p in e.store.location.iterdir()) == 1  # orphan left alone
    assert e.store.recover() == []
    assert verify_chain(e.store.entries()) == "" and len(e.store.entries()) == 2


def _oracle(ticks: list[RawTick]) -> dict[str, int]:
    return oracle.classify(
        [
            {
                "time_msc": t.time_msc,
                "bid": str(t.bid),
                "ask": str(t.ask),
                **({"flags": t.flags} if t.flags is not None else {}),
            }
            for t in ticks
        ]  # fmt: skip
    )


def _quality(tmp: Path, ticks: list[RawTick]) -> dict[str, Any]:
    return dict(go(tmp, source=FakeSource(ticks), operator="ann")["quality"])


def _counts(q: dict[str, Any]) -> tuple[int, int, int]:
    return (q["exact_duplicate_count"], q["same_timestamp_different_quote_count"],
            q["same_quote_different_flags_count"])  # fmt: skip


def _ocounts(o: dict[str, int]) -> tuple[int, int, int]:
    return (o["exact_duplicate"], o["same_ts_diff_quote"], o["same_quote_diff_flags"])


A, B, C = ("100.00", "100.10"), ("100.05", "100.15"), ("100.02", "100.12")


def tk(t: int, q: tuple[str, str] = A, flags: int | None = None) -> RawTick:
    return RawTick(t, Decimal(q[0]), Decimal(q[1]), flags)


# Golden vectors derived BY HAND from ORACLE-SPEC section 3 (not by running either program):
# (ticks, (exact_duplicate, same_timestamp_different_quote, same_quote_different_flags))
GOLDEN: list[tuple[str, list[RawTick], tuple[int, int, int]]] = [
    ("G1 (t,A)(t,B)(t,A)", [tk(1000), tk(1500), tk(1500, B), tk(1500), tk(2000)], (1, 1, 0)),
    ("G2 repeat across timestamps", [tk(1000), tk(1500), tk(2000), tk(1500), tk(2500)], (1, 0, 0)),
    ("G3 flags 2 vs 4", [tk(1000), tk(1500, A, 2), tk(1500, A, 4), tk(2000)], (0, 0, 1)),
    ("G4 adjacent repeat", [tk(1000), tk(1000), tk(1500)], (1, 0, 0)),
    ("G5 absent vs present flags", [tk(1500), tk(1500, A, 2), tk(1500)], (1, 0, 1)),
    (
        "G6 equal decimals, other spelling",
        [tk(1500, ("100.1", "100.2")), tk(1500, ("100.10", "100.20"))],
        (1, 0, 0),
    ),  # fmt: skip
    ("G7 three quotes, one repeated", [tk(1500), tk(1500, B), tk(1500, C), tk(1500, B)], (1, 2, 0)),
]


@pytest.mark.parametrize(("label", "ticks", "expected"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_D6_D7_D8_duplicates_match_hand_golden_vectors_and_the_independent_oracle(
    tmp_path: Path, label: str, ticks: list[RawTick], expected: tuple[int, int, int]
) -> None:
    """N6/X4 (R2 test_FINDING_A11_* x3): through the REAL extract() pipeline."""
    q = _quality(tmp_path / "ds", ticks)
    assert _counts(q) == expected, label
    assert _ocounts(_oracle(ticks)) == expected, label  # the oracle agrees with the hand vector
    assert verify_dataset(tmp_path / "ds") == []  # and the recompute reproduces it


def test_D9_out_of_order_sequence_counts_the_repeat_and_stays_a_hard_defect(tmp_path: Path) -> None:
    ticks = [tk(1000), tk(1500), tk(2000), tk(1500), tk(2500)]
    q = _quality(tmp_path / "ds", ticks)
    assert q["exact_duplicate_count"] == 1 and q["non_monotonic_count"] == 1
    assert _oracle(ticks)["out_of_order"] == 1
    assert "non_monotonic_count" in go(tmp_path / "x", source=FakeSource(ticks),
                                       operator="ann")["quality_hard_defects"]  # fmt: skip


def test_D10_large_dataset_with_separated_duplicates_matches_the_oracle(tmp_path: Path) -> None:
    import random

    rnd = random.Random(77)
    ticks: list[RawTick] = []
    for i in range(20_000):
        t = 1_000 + i * 3
        ticks.append(tk(t, rnd.choice([A, B, C]), rnd.choice([None, 2, 4])))
        if i % 97 == 0 and ticks:  # re-emit an earlier tick from the same chunk, non-adjacent
            ticks.append(ticks[max(0, len(ticks) - 1 - rnd.randrange(1, 30))])
    req = ExtractionRequest("SYN", 1_000, 1_000 + 20_000 * 3, 5_000)
    m = go(tmp_path / "ds", source=FakeSource(ticks), req=req, operator="ann")
    in_window = [x for x in ticks if 1_000 <= x.time_msc < 61_000]
    assert _counts(m["quality"]) == _ocounts(_oracle(in_window))
    assert m["quality"]["exact_duplicate_count"] > 100
    assert verify_dataset(tmp_path / "ds") == []


def test_D11_legacy_quality_v1_manifests_still_verify(tmp_path: Path) -> None:
    """Existing V3/V4 datasets carry tick-quality/1; the verifier reproduces that report exactly
    (adjacent semantics), so the version bump does not invalidate them."""
    ticks = [tk(1000), tk(1500), tk(1500, B), tk(1500), tk(2000)]
    go(tmp_path / "ds", source=FakeSource(ticks), operator="ann")
    acc = QualityAccumulator(QualityConfig(5000), version=LEGACY_QUALITY_VERSION)
    from nexora.market_history.convert import normalize

    for t in normalize([x for x in ticks if 1000 <= x.time_msc < 6000], conv()):
        acc.add(t)
    legacy = acc.report()
    assert legacy["version"] == LEGACY_QUALITY_VERSION and legacy["exact_duplicate_count"] == 0
    reseal(tmp_path / "ds", lambda m: m.update(quality=legacy))
    assert verify_dataset(tmp_path / "ds") == []
    reseal(tmp_path / "ds", lambda m: m["quality"].update(version="nexora.tick-quality/9"))
    assert any("quality_recompute_failed" in p for p in verify_dataset(tmp_path / "ds"))
    assert quality_report([], QualityConfig(1))["same_quote_different_flags_count"] == 0


def test_D12_unsupported_filesystem_lock_semantics_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexora.market_history import fsutil, lock

    e = make_env(tmp_path)
    approve(e)
    reseal(e.ds, lambda m: m["filesystem_capabilities"]["probes"].update(
        directory_lock_exclusion=False))  # fmt: skip
    assert "capability:probe_not_proven:directory_lock_exclusion" in _reasons(e)
    caps = fsutil.capability_blockers({"schema": fsutil.FS_SCHEMA, "platform": "linux",
                                       "fs_class": "local", "probes": {}})  # fmt: skip
    assert "probe_not_proven:directory_lock_exclusion" in caps

    def no_flock(fd: int) -> bool:
        raise HistoryError("writer_lock_unsupported", "errno 95")

    monkeypatch.setattr(lock, "_try_lock", no_flock)
    with pytest.raises(HistoryError, match="writer_lock_unsupported"):
        go(tmp_path / "nolock", operator="ann")


def test_without_the_crypto_library_nothing_verifies_and_signing_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core package imports with the standard library only (`cryptography` is loaded
    lazily); without the library every signature check fails closed and the dataset is UNTRUSTED."""
    from nexora.market_history import signing

    e = make_env(tmp_path)
    approve(e)
    pin = fresh_pin(e.store, CHECK - timedelta(minutes=1))
    assert status(e, pin=pin).trusted
    for name in ("cryptography", "cryptography.exceptions", "cryptography.hazmat.primitives",
                 "cryptography.hazmat.primitives.asymmetric",
                 "cryptography.hazmat.primitives.asymmetric.ed25519"):  # fmt: skip
        monkeypatch.setitem(sys.modules, name, None)
    assert not status(e, pin=pin).trusted
    with pytest.raises(HistoryError, match="signing_unavailable"):
        signing.generate_signer()
