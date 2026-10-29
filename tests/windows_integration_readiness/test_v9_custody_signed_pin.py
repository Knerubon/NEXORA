"""Wave V9: the custody gate against the signed-pin / attestation / lineage contract of the pinned
PR #96 CANDIDATE (dependency-manifest.json). Not an approval of #96: every bundle stays BLOCKED
while DEP-01 is BLOCKED, and this gate cannot verify a signature (the verifier does).

`pr96_fixtures/pr96-custody-fixtures.json` was produced by the REAL #96 custody code at the pinned
head (generator next to it) with throwaway keys: real signed pins, real signed approval/revocation
entries in a real append-only store, and the real `evaluate_custody` verdict for three scenarios.
These tests map those real artifacts onto the evidence bundle and assert the gate agrees with the
verdict #96 reached, and that every field this package records exists in what #96 emits.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness.custody_gate import CustodyBundle, trust_gate
from tests.windows_integration_readiness.test_v4_custody_compat import (
    HEAD_SHA,
    T0,
    _approved_baseline,
    good,
)

FIXTURE = Path(__file__).resolve().parent / "pr96_fixtures" / "pr96-custody-fixtures.json"
DOC: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _reasons(monkeypatch: pytest.MonkeyPatch, **change: Any) -> tuple[str, ...]:
    _approved_baseline(monkeypatch)
    d = trust_gate(good(**change))
    assert d.outcome != cg.RECORDABLE, change
    return d.reasons


# ---- real #96 artifacts -> bundle -----
def bundle_from_pr96(scenario: str, **change: Any) -> CustodyBundle:
    """Map the real #96 entries/pin/candidate of a scenario onto the evidence bundle."""
    sc = DOC["scenarios"][scenario]
    entries, pin, cand = sc["entries"], sc["pins"][-1], sc["candidate"]
    approvals = [e for e in entries if e["decision"] == "approve"]
    revocations = [e for e in entries if e["decision"] == "revoke"]
    # the approval the operator relies on: the LAST one for this candidate
    chosen = [e for e in approvals if e["candidate_sha256"] == cand["candidate_sha256"]][-1]
    head = entries[-1]
    argv = (
        cg.V4_VERIFY_SCRIPT, "--dataset", "D", "--store", "S", "--registry", "R",
        "--store-head", f"{head['seq']}:{head['entry_sha256']}", "--pin", "pin.json",
        "--max-pin-age-seconds", str(sc["max_pin_age_seconds"]), "--require-trusted",
    )  # fmt: skip
    base = CustodyBundle(
        verify_argv=argv,
        verifier_status="TRUSTED" if sc["verdict"]["approved"] else "UNTRUSTED",
        verifier_exit_code=0 if sc["verdict"]["approved"] else 1,
        operator=cand["operator"],
        candidate_creator=cand["operator"],
        approver=chosen["approver"],
        approval_seq=chosen["seq"],
        registry_ids=frozenset(p["id"] for p in DOC["registry"]["principals"]),
        revocation_seqs=tuple(e["seq"] for e in revocations),
        store_chain="ok",
        store_head_seq_at_verification=head["seq"],
        pinned_head_seq=head["seq"],
        legacy_v3_anchor_only=False,
        manifest_schema=cg.V4_MANIFEST_SCHEMA,
        extractor_version=cg.V4_EXTRACTOR_VERSION,
        extraction_platform="linux",
        writer_lock="acquired",
        checklist_extractor_sha=dp.CANDIDATE_SHA,
        run_extractor_sha=dp.CANDIDATE_SHA,
        approver_identities=frozenset({chosen["approver"]}),
        operator_authenticated=True,  # explicit test-only conditions, as in good()
        approver_authenticated=True,
        pinned_head_sha256=head["entry_sha256"],
        store_head_sha256_at_verification=head["entry_sha256"],
        approval_entry_sha256=chosen["entry_sha256"],
        candidate_approval_seqs=tuple(
            e["seq"] for e in approvals if e["candidate_sha256"] == cand["candidate_sha256"]
        ),
        pin_channel=cg.PIN_INDEPENDENT,
        pin_path="pin.json",
        pin_id=pin["pin_id"],
        pin_pinned_by=pin["pinned_by"],
        pin_key_id=pin["pinned_by_key_id"],
        pin_signature=pin["signature"],
        pin_seq=pin["seq"],
        pin_entry_sha256=pin["entry_sha256"],
        pin_store_genesis_sha256=pin["store_genesis_sha256"],
        pin_captured_at=datetime.fromisoformat(pin["captured_at"]),
        max_pin_age_seconds=sc["max_pin_age_seconds"],
        verified_at=datetime.fromisoformat(sc["evaluated_at"]),
        approved_at=datetime.fromisoformat(chosen["approved_at"]),
        operator_key_id=cand["operator_key_id"],
        operator_signature=cand["operator_signature"],
        approver_key_id=chosen["approver_key_id"],
        approval_signature=chosen["signature"],
        approval_id=chosen["approval_id"],
        candidate_sha256=cand["candidate_sha256"],
        revocation_ids=tuple(e["revocation_id"] for e in revocations),
        acknowledged_revocations=tuple(chosen["acknowledged_revocations"]),
    )
    return dataclasses.replace(base, **change)


def test_pr96_fixture_provenance_and_the_verdicts_pr96_itself_reached() -> None:
    sc = DOC["scenarios"]
    assert sc["A_single_approval"]["verdict"]["approved"] is True
    assert sc["B_revoke_then_reapprove"]["verdict"]["approved"] is True
    assert sc["C_replay_after_revocation"]["verdict"]["approved"] is False
    assert (
        "custody:approval_3_rejected:replayed_approval_id"
        in (sc["C_replay_after_revocation"]["verdict"]["reasons"])
    )
    assert (
        "throwaway" in DOC["note"].lower() or "Throwaway" in DOC["note"] or "public" in DOC["note"]
    )
    blob = json.dumps(DOC).lower()
    assert "private" not in blob and "begin" not in blob  # no key material


@pytest.mark.parametrize("scenario", ["A_single_approval", "B_revoke_then_reapprove"])
def test_gate_accepts_what_pr96_trusts_only_under_test_only_conditions(
    scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bundle_from_pr96(scenario)
    d = trust_gate(b)  # shipped flags: DEP-01 BLOCKED
    assert d.outcome == cg.BLOCKED
    assert d.reasons == (
        "dep01_principal_authentication_unavailable",
        "dep01_v4_custody_baseline_not_independently_approved",
    )
    _approved_baseline(monkeypatch)
    assert trust_gate(b).outcome == cg.RECORDABLE, trust_gate(b).reasons


def test_gate_agrees_with_pr96_that_a_replay_after_revocation_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)
    d = trust_gate(bundle_from_pr96("C_replay_after_revocation"))
    assert d.outcome == cg.BLOCKED
    assert "approval_omits_earlier_revocation" in d.reasons  # #96: lineage (F2)
    assert "candidate_reused_across_approvals" in d.reasons  # #96: candidate_reused
    assert "approval_replayed_after_revocation" in d.reasons


def test_every_signed_field_this_package_records_has_the_shape_pr96_emits() -> None:
    sc = DOC["scenarios"]["B_revoke_then_reapprove"]
    pin, cand = sc["pins"][0], sc["candidate"]
    approve = [e for e in sc["entries"] if e["decision"] == "approve"][-1]
    revoke = [e for e in sc["entries"] if e["decision"] == "revoke"][0]
    sig_len, key_len = dp.CONTRACT["signature_hex_length"], dp.CONTRACT["key_id_hex_length"]
    nonce_len = dp.CONTRACT["nonce_hex_length"]
    assert pin["schema"] == dp.CONTRACT["pin_schema"]
    assert approve["schema"] == dp.CONTRACT["approval_schema"]
    assert cand["schema"] == dp.CONTRACT["candidate_schema"]
    assert DOC["registry"]["schema"] == dp.CONTRACT["registry_schema"]
    for sig in (pin["signature"], approve["signature"], cand["operator_signature"]):
        assert len(sig) == sig_len
    for key in (pin["pinned_by_key_id"], approve["approver_key_id"], cand["operator_key_id"]):
        assert len(key) == key_len
    for nonce in (pin["pin_id"], approve["approval_id"], revoke["revocation_id"]):
        assert len(nonce) == nonce_len
    for identity in (pin["pinned_by"], approve["approver"], cand["operator"]):
        assert cg.identity_problem(identity) == ""
    assert approve["acknowledged_revocations"] == [revoke["revocation_id"]]
    # the evidence schema accepts exactly these shapes
    from tests.windows_integration_readiness import evidence_validator as ev

    kinds = ev.SECTIONS["custody"]
    for field_name, value in (
        ("pin_id", pin["pin_id"]),
        ("pin_pinned_by", pin["pinned_by"]),
        ("pin_key_id", pin["pinned_by_key_id"]),
        ("pin_signature", pin["signature"]),
        ("pin_seq", pin["seq"]),
        ("pin_entry_sha256", pin["entry_sha256"]),
        ("pin_store_genesis_sha256", pin["store_genesis_sha256"]),
        ("pin_captured_at_utc", pin["captured_at"]),
        ("operator_key_id", cand["operator_key_id"]),
        ("operator_signature", cand["operator_signature"]),
        ("approver_key_id", approve["approver_key_id"]),
        ("approval_signature", approve["signature"]),
        ("approval_id", approve["approval_id"]),
        ("candidate_sha256", cand["candidate_sha256"]),
        ("approved_at_utc", approve["approved_at"]),
        ("revocation_ids", [revoke["revocation_id"]]),
        ("acknowledged_revocations", approve["acknowledged_revocations"]),
    ):
        assert ev._kind_ok(kinds[field_name], value), (field_name, value)


# ---- signed pin: verifier invocation -----
def test_invocation_without_a_signed_pin_or_max_age_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = list(good().verify_argv)

    def without(*flags: str) -> tuple[str, ...]:
        out: list[str] = []
        skip = False
        for tok in base:
            if skip:
                skip = False
                continue
            if tok in flags:
                skip = tok != "--require-trusted"
                continue
            out.append(tok)
        return tuple(out)

    assert "verifier_argv_missing:--pin" in _reasons(monkeypatch, verify_argv=without("--pin"))
    assert "verifier_argv_missing:--max-pin-age-seconds" in _reasons(
        monkeypatch, verify_argv=without("--max-pin-age-seconds")
    )
    # an unsigned store head alone is not enough
    reasons = _reasons(monkeypatch, verify_argv=without("--pin", "--max-pin-age-seconds"))
    assert {"verifier_argv_missing:--pin", "verifier_argv_missing:--max-pin-age-seconds"} <= set(
        reasons
    )


@pytest.mark.parametrize("age", ["0", "-5", "3600.5", "one hour", "", "01"])
def test_max_pin_age_must_be_a_positive_integer(age: str, monkeypatch: pytest.MonkeyPatch) -> None:
    argv = list(good().verify_argv)
    argv[argv.index("--max-pin-age-seconds") + 1] = age
    inv = cg.parse_verify_argv(argv)
    assert inv.max_pin_age_seconds is None
    assert inv.problems  # malformed or missing value
    assert _reasons(monkeypatch, verify_argv=tuple(argv))


def test_recorded_pin_path_and_age_must_match_the_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "recorded_pin_path_differs_from_verifier_invocation" in _reasons(
        monkeypatch, pin_path="other-pin.json"
    )
    assert "recorded_max_pin_age_differs_from_verifier_invocation" in _reasons(
        monkeypatch, max_pin_age_seconds=60
    )
    assert "pin_file_not_recorded" in _reasons(monkeypatch, pin_path=None)
    assert "max_pin_age_not_recorded" in _reasons(monkeypatch, max_pin_age_seconds=None)


# ---- signed pin: evidence -----
@pytest.mark.parametrize(
    ("field", "good_value"),
    [
        ("pin_id", "11" * 16),
        ("pin_key_id", "a1" * 32),
        ("pin_signature", "5" * 128),
        ("pin_entry_sha256", HEAD_SHA),
        ("pin_store_genesis_sha256", "9" * 64),
        ("operator_key_id", "c1" * 32),
        ("operator_signature", "6" * 128),
        ("approver_key_id", "d1" * 32),
        ("approval_signature", "7" * 128),
        ("approval_id", "22" * 16),
        ("candidate_sha256", "e" * 64),
    ],
)
def test_every_signature_key_and_id_field_is_required_and_well_formed(
    field: str, good_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert good_value == getattr(good(), field)
    assert f"{field}_not_recorded" in _reasons(monkeypatch, **{field: None})
    variants = (good_value[:-1], good_value + "0", good_value.upper(), "z" * len(good_value), "")
    for bad in (v for v in variants if v != good_value):
        assert f"{field}_malformed" in _reasons(monkeypatch, **{field: bad}), (field, bad)


def test_pin_time_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    age = good().max_pin_age_seconds
    assert age == 3600
    _approved_baseline(monkeypatch)
    at_limit = dataclasses.replace(good(), verified_at=T0 + timedelta(seconds=age))
    assert trust_gate(at_limit).outcome == cg.RECORDABLE  # exactly max age: not stale
    stale = dataclasses.replace(good(), verified_at=T0 + timedelta(seconds=age + 1))
    assert "pin_stale" in trust_gate(stale).reasons
    # captured in the future: allowed up to the 5 minute skew, refused beyond
    ok = dataclasses.replace(
        good(), verified_at=T0 - timedelta(seconds=300), approved_at=T0 - timedelta(seconds=300)
    )
    assert trust_gate(ok).outcome == cg.RECORDABLE
    future = dataclasses.replace(good(), verified_at=T0 - timedelta(seconds=301))
    assert "pin_in_the_future" in trust_gate(future).reasons
    assert "verification_time_not_recorded" in _reasons(monkeypatch, verified_at=None)
    assert "pin_captured_at_not_recorded" in _reasons(monkeypatch, pin_captured_at=None)
    assert "approved_at_not_recorded" in _reasons(monkeypatch, approved_at=None)


def test_approval_after_pin_capture_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    assert (
        trust_gate(dataclasses.replace(good(), approved_at=T0 + timedelta(seconds=300))).outcome
        == cg.RECORDABLE
    )
    late = dataclasses.replace(good(), approved_at=T0 + timedelta(seconds=301))
    assert "approved_after_pin_capture" in trust_gate(late).reasons


def test_pin_authority_must_be_independent_and_keys_must_not_be_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "pin_not_independent" in _reasons(monkeypatch, pin_pinned_by="synthetic-custodian")
    assert "pin_not_independent" in _reasons(monkeypatch, pin_pinned_by="synthetic-operator")
    assert "key_shared_between:pin:approver" in _reasons(monkeypatch, pin_key_id="d1" * 32)
    assert "key_shared_between:operator:approver" in _reasons(
        monkeypatch, operator_key_id="d1" * 32
    )
    assert "key_shared_between:pin:operator" in _reasons(monkeypatch, pin_key_id="c1" * 32)
    assert "signature_reused_across_purposes" in _reasons(monkeypatch, approval_signature="6" * 128)


def test_pin_must_cover_the_approval_and_match_the_store_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # good(): approval_seq 5, store head seq 7. A pin at seq 4 does not cover approval 5.
    assert "approval_not_covered_by_pin" in _reasons(monkeypatch, pin_seq=4)
    _approved_baseline(monkeypatch)
    assert "approval_not_covered_by_pin" not in trust_gate(good(pin_seq=5)).reasons  # == seq
    assert "approval_not_covered_by_pin" in trust_gate(good(pin_seq=4)).reasons
    assert "pin_ahead_of_store_truncated" in _reasons(monkeypatch, pin_seq=8)
    assert "signed_pin_head_mismatch" in _reasons(monkeypatch, pin_entry_sha256="f" * 64)
    # approval AT the pin: its entry digest must be the pinned digest
    reasons = _reasons(monkeypatch, pin_seq=5, approval_entry_sha256="f" * 64)
    assert "approval_entry_differs_from_signed_pin" in reasons
    assert "pin_seq_not_recorded" in _reasons(monkeypatch, pin_seq=None)
    assert "pin_seq_not_recorded" in _reasons(monkeypatch, pin_seq=0)


def test_pin_authority_identity_uses_the_strict_pr96_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "identity_ambiguous:pin_authority" in _reasons(monkeypatch, pin_pinned_by="Pin-Auth")
    assert "pin_pinned_by_not_recorded" in _reasons(monkeypatch, pin_pinned_by=None)


# ---- revocation lineage and fail-closed omission -----
def test_candidate_approval_seqs_must_be_recorded_not_defaulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert CustodyBundle.__dataclass_fields__["candidate_approval_seqs"].default is None
    for missing in (None, ()):
        reasons = _reasons(monkeypatch, candidate_approval_seqs=missing)
        assert "candidate_approval_seqs_not_recorded" in reasons
    # the omission used to hide a replay: with the list missing the gate cannot know the first
    # approval of this candidate, so it must not report "no replay" as if it had looked
    reasons = _reasons(
        monkeypatch, revocation_seqs=(4,), revocation_ids=("33" * 16,), candidate_approval_seqs=()
    )
    assert "candidate_approval_seqs_not_recorded" in reasons
    assert "approval_not_among_candidate_approvals" in _reasons(
        monkeypatch, candidate_approval_seqs=(2,)
    )


def test_candidate_may_back_only_one_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "candidate_reused_across_approvals" in _reasons(
        monkeypatch, candidate_approval_seqs=(3, 5)
    )


def test_revocation_lineage_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    rid, other = "33" * 16, "44" * 16
    assert "revocation_ids_not_recorded" in _reasons(monkeypatch, revocation_ids=None)
    assert "acknowledged_revocations_not_recorded" in _reasons(
        monkeypatch, acknowledged_revocations=None
    )
    assert "revocation_ids_do_not_match_revocation_seqs" in _reasons(
        monkeypatch, revocation_seqs=(3,), revocation_ids=()
    )
    assert "revocation_ids_malformed" in _reasons(
        monkeypatch, revocation_seqs=(3,), revocation_ids=("short",)
    )
    assert "revocation_ids_malformed" in _reasons(
        monkeypatch, revocation_seqs=(3, 4), revocation_ids=(rid, rid)
    )
    assert "acknowledged_revocations_malformed" in _reasons(
        monkeypatch,
        acknowledged_revocations=(other, rid),  # unsorted
    )
    # the approval (seq 5) omits an EARLIER revocation (seq 3): a replay of an older decision
    assert "approval_omits_earlier_revocation" in _reasons(
        monkeypatch, revocation_seqs=(3,), revocation_ids=(rid,), acknowledged_revocations=()
    )
    # it acknowledges a revocation that is not in the chain: history was removed
    assert "revocation_lineage_broken" in _reasons(
        monkeypatch, revocation_seqs=(3,), revocation_ids=(rid,), acknowledged_revocations=(other,)
    )
    # an acknowledged earlier revocation is fine
    _approved_baseline(monkeypatch)
    ok = good(revocation_seqs=(3,), revocation_ids=(rid,), acknowledged_revocations=(rid,))
    assert trust_gate(ok).outcome == cg.RECORDABLE


def test_a_revocation_after_the_approval_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    rid = "33" * 16
    reasons = _reasons(
        monkeypatch, revocation_seqs=(6,), revocation_ids=(rid,), acknowledged_revocations=()
    )
    assert "approval_revoked" in reasons
    assert "approval_omits_earlier_revocation" not in reasons  # seq 6 is later, not earlier


# ---- identity grammar: never a superset of #96's -----
GRAMMAR_OK = ["a", "a0", "synthetic-operator", "carol.smith@example", "x" * 128, "0abc", "a_b"]
GRAMMAR_REFUSED = [
    "",
    " ",
    "A",  # uppercase: #96 does not case-fold, so a case-folding gate would accept what #96 refuses
    "Carol",
    "carol ",
    " carol",
    "ca rol",
    "carol+tag",  # the old gate accepted '+'
    "-carol",
    ".carol",
    "x" * 129,  # #96 allows 128; the old gate allowed 200
    "ｃａｒｏｌ",  # fullwidth: #96 does not NFKC-normalise
    "carоl",  # Cyrillic o
    "car​ol",  # zero-width
    "carol\n",
    "carol\u0000",
    "ca\trol",
    "é",
]


@pytest.mark.parametrize("identity", GRAMMAR_OK)
def test_identity_grammar_accepts_exactly_the_pr96_principal_id(identity: str) -> None:
    import re

    assert re.fullmatch(dp.CONTRACT["principal_id_grammar"], identity)
    assert cg.identity_problem(identity) == ""


@pytest.mark.parametrize("identity", GRAMMAR_REFUSED)
def test_identity_grammar_refuses_everything_pr96_refuses(identity: str) -> None:
    import re

    assert not re.fullmatch(dp.CONTRACT["principal_id_grammar"], identity)
    assert cg.identity_problem(identity) != ""


def test_identity_non_strings_are_refused() -> None:
    for bad in (None, 5, b"carol", ["carol"]):
        assert cg.identity_problem(bad) == "identity_not_a_string"


def test_spelling_collisions_are_still_detected_even_though_the_spellings_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over-blocking is kept: ' Synthetic-OPERATOR' is refused AND recognised as the operator."""
    reasons = _reasons(
        monkeypatch, approver=" Synthetic-OPERATOR", registry_ids=frozenset({"synthetic-operator"})
    )
    assert "self_approval_by_operator" in reasons
    assert any(r.startswith("identity_ambiguous") for r in reasons)


# ---- candidate head matching -----
def test_gate_requires_the_exact_pinned_candidate_head(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = dp.CANDIDATE_SHA
    cases = {
        sha[:7]: "ABBREVIATED",
        sha[:39]: "ABBREVIATED",
        sha.upper(): "MALFORMED",
        sha + " ": "MALFORMED",
        "f" * 40: "NOT_THE_PINNED_CANDIDATE",
        "d6a7cda72a88600e2ccf6554605f1c50478893ea": "STALE",
        "": "MALFORMED",
    }
    _approved_baseline(monkeypatch)
    for head, kind in cases.items():
        d = trust_gate(good(checklist_extractor_sha=head, run_extractor_sha=head))
        assert f"extractor_sha_not_pinned_pr96_candidate:{kind}" in d.reasons, (head, d.reasons)
        assert d.outcome != cg.RECORDABLE
    assert trust_gate(good()).outcome == cg.RECORDABLE
