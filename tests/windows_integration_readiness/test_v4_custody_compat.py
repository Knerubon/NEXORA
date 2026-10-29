"""Finding B-M2 regression: the readiness procedure follows PR #96 V4 custody semantics.

Adversarial cases B1-B10 from the PR #95 remediation prompt. Each mutation of an otherwise complete
bundle must add its specific refusal reason. No bundle can reach RECORDABLE while the V4 custody
baseline is unapproved (DEP-01), and the gate never returns TRUSTED.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness.custody_gate import CustodyBundle, trust_gate

SHA = dp.CANDIDATE_SHA  # the pinned PR #96 candidate head (not a gate-approved baseline)
T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
PIN_PATH = "pin.json"
HEAD_SHA = "a" * 64
ENTRY_SHA = "b" * 64


def argv(pin: str = "7:" + HEAD_SHA) -> tuple[str, ...]:
    return (
        cg.V4_VERIFY_SCRIPT,
        "--dataset",
        "D",
        "--store",
        "S",
        "--registry",
        "R",
        "--store-head",
        pin,
        "--pin",
        PIN_PATH,
        "--max-pin-age-seconds",
        "3600",
        "--require-trusted",
    )


GOOD_ARGV = argv()


def good(**changes: Any) -> CustodyBundle:
    base = CustodyBundle(
        verify_argv=GOOD_ARGV,
        verifier_status="TRUSTED",
        verifier_exit_code=0,
        operator="synthetic-operator",
        candidate_creator="synthetic-operator",
        approver="synthetic-custodian",
        approval_seq=5,
        registry_ids=frozenset({"synthetic-custodian"}),
        revocation_seqs=(),
        store_chain="ok",
        store_head_seq_at_verification=7,
        pinned_head_seq=7,
        legacy_v3_anchor_only=False,
        manifest_schema=cg.V4_MANIFEST_SCHEMA,
        extractor_version=cg.V4_EXTRACTOR_VERSION,
        extraction_platform="linux",
        writer_lock="acquired",
        checklist_extractor_sha=SHA,
        run_extractor_sha=SHA,
        # explicit test-only conditions: authenticated principals do not exist at V4 (DEP-01)
        approver_identities=frozenset({"synthetic-custodian"}),
        operator_authenticated=True,
        approver_authenticated=True,
        pinned_head_sha256=HEAD_SHA,
        store_head_sha256_at_verification=HEAD_SHA,
        approval_entry_sha256=ENTRY_SHA,
        candidate_approval_seqs=(5,),
        pin_channel=cg.PIN_INDEPENDENT,
        # V9 signed pin, attestation, approval, lineage (synthetic, internally consistent)
        pin_path=PIN_PATH,
        pin_id="11" * 16,
        pin_pinned_by="synthetic-pin-authority",
        pin_key_id="a1" * 32,
        pin_signature="5" * 128,
        pin_seq=7,
        pin_entry_sha256=HEAD_SHA,
        pin_store_genesis_sha256="9" * 64,
        pin_captured_at=T0,
        max_pin_age_seconds=3600,
        verified_at=T0 + timedelta(minutes=10),
        approved_at=T0,
        operator_key_id="c1" * 32,
        operator_signature="6" * 128,
        approver_key_id="d1" * 32,
        approval_signature="7" * 128,
        approval_id="22" * 16,
        candidate_sha256="e" * 64,
        revocation_ids=(),
        acknowledged_revocations=(),
    )
    return dataclasses.replace(base, **changes)


def _approved_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only: pretend DEP-01 is resolved so each other check can be seen on its own."""
    monkeypatch.setattr(cg, "V4_BASELINE_APPROVED", True)
    monkeypatch.setattr(cg, "V4_PRINCIPAL_AUTHENTICATION_AVAILABLE", True)


def test_complete_bundle_is_still_blocked_while_v4_baseline_unapproved() -> None:
    d = trust_gate(good())
    assert d.outcome == cg.BLOCKED
    assert d.reasons == (
        "dep01_principal_authentication_unavailable",
        "dep01_v4_custody_baseline_not_independently_approved",
    )


def test_complete_bundle_is_only_recordable_never_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = trust_gate(good())
    assert d.outcome == cg.RECORDABLE and d.outcome != "TRUSTED"


CASES: list[tuple[str, dict[str, Any], str, str]] = [
    (
        "B1_anchor_only",
        {
            "verify_argv": (
                cg.V4_VERIFY_SCRIPT,
                "--dataset",
                "D",
                "--anchor",
                "A",
                "--require-trusted",
            )
        },
        cg.BLOCKED,
        "anchor_only_or_no_custody_inputs_never_trusted",
    ),
    ("B2_no_approval", {"approver": None, "approval_seq": None}, cg.BLOCKED, "no_custody_approval"),
    (
        "B3_unauthorized_custodian",
        {"approver": "mallory"},
        cg.BLOCKED,
        "approver_not_in_verifier_registry",
    ),
    (
        "B3_operator_self_approves_in_disguise",
        {"approver": " Synthetic-OPERATOR", "registry_ids": frozenset({"synthetic-operator"})},
        cg.BLOCKED,
        "self_approval_by_operator",
    ),
    ("B4_revoked", {"revocation_seqs": (6,)}, cg.BLOCKED, "approval_revoked"),
    (
        "B5_chain_tampered",
        {"store_chain": "entry_hash_mismatch:3"},
        cg.BLOCKED,
        "evidence_store_chain_invalid:entry_hash_mismatch:3",
    ),
    (
        "B6_stale_pin",
        {"pinned_head_seq": 6, "verify_argv": argv("6:" + HEAD_SHA)},
        cg.BLOCKED,
        "stale_pinned_head",
    ),
    ("B6_no_pin", {"pinned_head_seq": None}, cg.BLOCKED, "store_head_not_pinned"),
    (
        "B7_v3_anchor_as_custody",
        {"legacy_v3_anchor_only": True, "manifest_schema": "nexora.market-history/3"},
        cg.BLOCKED,
        "legacy_v3_anchor_is_not_custody_evidence",
    ),
    (
        "B8_writer_lock_held",
        {"writer_lock": "writer_lock_held"},
        cg.STOP,
        "writer_lock_not_owned:writer_lock_held",
    ),
    (
        "B9_windows_guarantee_claimed",
        {"claimed_verified_guarantees": frozenset({"windows_msvcrt_lock_exclusivity"})},
        cg.BLOCKED,
        "windows_guarantee_unverified:windows_msvcrt_lock_exclusivity",
    ),
    (
        "B9_windows_extraction_platform",
        {"extraction_platform": "win32"},
        cg.BLOCKED,
        "dep01_platform_unproven_at_v4:win32",
    ),
    (
        "B10_extractor_sha_changed",
        {"run_extractor_sha": "f" * 40},
        cg.STOP,
        "extractor_sha_changed_since_checklist",
    ),
    (
        "B10_extractor_version_changed",
        {"extractor_version": "nexora.market-history-extractor/7"},
        cg.STOP,
        "extractor_version_changed:nexora.market-history-extractor/7",
    ),
]


@pytest.mark.parametrize(("name", "change", "outcome", "reason"), CASES)
def test_b_case_is_refused_with_its_reason(
    name: str,
    change: dict[str, Any],
    outcome: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)  # isolate the case from the global DEP-01 block
    d = trust_gate(good(**change))
    assert d.outcome == outcome, (name, d)
    assert reason in d.reasons, (name, d.reasons)


@pytest.mark.parametrize(("name", "change", "outcome", "reason"), CASES)
def test_b_case_with_verifier_claiming_trusted_is_flagged_as_contradiction(
    name: str,
    change: dict[str, Any],
    outcome: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)
    d = trust_gate(good(**change))
    if outcome == cg.BLOCKED:
        assert "verifier_trusted_contradicts_recorded_evidence" in d.reasons


def test_unknown_verifier_flag_is_a_stop_not_an_invention(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = trust_gate(good(verify_argv=(*GOOD_ARGV, "--trust-anchor")))
    assert d.outcome == cg.STOP and "unknown_verifier_flag:--trust-anchor" in d.reasons


def test_untrusted_verifier_output_is_never_recordable(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = trust_gate(good(verifier_status="UNTRUSTED", verifier_exit_code=1))
    assert d.outcome == cg.BLOCKED and "verifier_did_not_report_trusted" in d.reasons


def test_quoted_v4_interface_constants() -> None:
    """Guard against silent edits: these are quotes from PR #96 at V4_REF_SHA, re-verified by
    the operator at the running SHA (checklist A3), not values this package may choose."""
    assert cg.V4_REF_SHA == dp.CANDIDATE_SHA == "1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8"
    assert cg.V4_PROVEN_PLATFORMS == frozenset({"linux"})
    assert {"--anchor", "--store-head", "--pin", "--max-pin-age-seconds"} <= cg.V4_VERIFY_FLAGS
    assert cg.V4_CUSTODY_SUBCOMMANDS == (
        "approve",
        "revoke",
        "head",
        "recover-store",
        "pin",
        "keygen",
    )
    assert cg.V4_EXTRACTOR_VERSION == "nexora.market-history-extractor/6"
    assert not dp.head_is_gate_approved(dp.CANDIDATE_SHA)  # a candidate is not an approved baseline
    assert cg.V4_BASELINE_APPROVED is False
    assert cg.V4_PRINCIPAL_AUTHENTICATION_AVAILABLE is False
    assert cg.DEP02_WINDOWS_GUARANTEES_RESOLVED is False
    assert cg.VERIFIABLE_GUARANTEES == frozenset()
