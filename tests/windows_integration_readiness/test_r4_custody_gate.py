"""Round 4 defensive assertions for the custody gate (findings N3 and R3-2, cross-track X2-X6).

Adversarial cases B1-B12 of the PR #95 Round 4 prompt. Each assertion states the REMEDIATED
behaviour, so it fails on `3cc8869` (where the bundle has no alias set, no pin digest and no
allow-list) and passes here. Reproducing the old weakness is not counted as a fix.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness.custody_gate import parse_verify_argv, trust_gate
from tests.windows_integration_readiness.test_v4_custody_compat import (
    ENTRY_SHA,
    HEAD_SHA,
    _approved_baseline,
    argv,
    good,
)


def _reasons(monkeypatch: pytest.MonkeyPatch, **change: Any) -> tuple[str, ...]:
    _approved_baseline(monkeypatch)
    d = trust_gate(good(**change))
    assert d.outcome != cg.RECORDABLE, change
    return d.reasons


def test_b1_operator_equals_approver(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "self_approval_by_operator" in _reasons(monkeypatch, operator="synthetic-custodian")


def test_b2_operator_equals_approver_through_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons = _reasons(
        monkeypatch,
        operator="carol",
        candidate_creator="carol",
        approver_identities=frozenset({"synthetic-custodian", "carol"}),
        registry_ids=frozenset({"synthetic-custodian", "carol"}),
    )
    assert "self_approval_by_operator" in reasons
    assert "self_approval_by_candidate_creator" in reasons


def test_b2_alias_set_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "approver_alias_set_not_recorded" in _reasons(
        monkeypatch, approver_identities=frozenset()
    )


def test_b2_alias_outside_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons = _reasons(monkeypatch, approver_identities=frozenset({"synthetic-custodian", "eve"}))
    assert "approver_alias_not_in_verifier_registry" in reasons


@pytest.mark.parametrize(
    ("operator", "reason"),
    [
        ("synthetic-custоdian", "identity_ambiguous:operator"),  # Cyrillic o
        ("synthetic-custo​dian", "identity_control_or_format_character:operator"),
        ("synthetic-custodian\u0007", "identity_control_or_format_character:operator"),
        ("   ", "identity_empty:operator"),
    ],
)
def test_b3_confusable_or_hidden_identity_is_refused_not_compared(
    operator: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert reason in _reasons(monkeypatch, operator=operator, candidate_creator=operator)


def test_b3_fullwidth_disguise_still_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    fullwidth = "ｓｙｎｔｈｅｔｉｃ－ｃｕｓｔｏｄｉａｎ"
    assert "self_approval_by_operator" in _reasons(monkeypatch, operator=fullwidth)


def test_b4_missing_or_unauthenticated_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "operator_identity_not_authenticated" in _reasons(
        monkeypatch, operator_authenticated=False
    )
    assert "operator_not_recorded" in _reasons(monkeypatch, operator=None)


def test_b4_principal_authentication_is_unavailable_at_v4() -> None:
    """No monkeypatch: the shipped gate blocks every bundle on missing authentication."""
    assert "dep01_principal_authentication_unavailable" in trust_gate(good()).reasons


def test_b5_verifier_argv_without_store_head(monkeypatch: pytest.MonkeyPatch) -> None:
    no_pin = tuple(a for a in argv() if a not in ("--store-head", "7:" + HEAD_SHA))
    reasons = _reasons(monkeypatch, verify_argv=no_pin)
    assert "verifier_argv_missing:--store-head" in reasons
    assert "recorded_pin_absent_from_verifier_invocation" in reasons


@pytest.mark.parametrize(
    ("bad_argv", "reason"),
    [
        (("echo", "TRUSTED"), "verifier_argv_not_v4_verifier"),
        (("sh", "-c", "python scripts/verify_market_history.py"), "verifier_argv_not_v4_verifier"),
        ((cg.V4_VERIFY_SCRIPT, "--dataset", "D", "--anchor", "A"), "verifier_argv_missing:--store"),
        (
            (*argv()[:7], "--store-head=7:" + HEAD_SHA, "--require-trusted"),
            "verifier_argv_malformed",
        ),
        ((*argv(), "--store", "S2"), "verifier_argv_duplicate:--store"),
        (argv("7:not-a-digest"), "verifier_argv_store_head_malformed"),
    ],
)
def test_b5_verifier_argv_is_structured(
    bad_argv: tuple[str, ...], reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reasons = _reasons(monkeypatch, verify_argv=bad_argv)
    assert any(r.startswith(reason) for r in reasons), reasons


def test_b5_launcher_prefixes_are_accepted() -> None:
    for prefix in cg.V4_VERIFY_LAUNCHERS:
        inv = parse_verify_argv((*prefix, *argv()))
        assert inv.problems == () and inv.store_head == (7, HEAD_SHA), prefix


def test_b6_approval_sequence_after_the_pinned_head(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons = _reasons(monkeypatch, approval_seq=9, candidate_approval_seqs=(9,))
    assert "approval_outside_pinned_chain" in reasons


def test_b6_approval_at_the_pinned_head_must_be_the_head_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasons = _reasons(monkeypatch, approval_seq=7, candidate_approval_seqs=(7,))
    assert "approval_entry_differs_from_pinned_head" in reasons
    _approved_baseline(monkeypatch)
    ok = trust_gate(
        good(approval_seq=7, candidate_approval_seqs=(7,), approval_entry_sha256=HEAD_SHA)
    )
    assert ok.outcome == cg.RECORDABLE, ok.reasons


def test_b6_revocation_beyond_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "revocation_outside_pinned_chain" in _reasons(monkeypatch, revocation_seqs=(8,))


def test_b7_same_sequence_different_head_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons = _reasons(monkeypatch, store_head_sha256_at_verification="c" * 64)
    assert "pinned_head_digest_mismatch" in reasons
    reasons = _reasons(monkeypatch, pinned_head_sha256="c" * 64)
    assert "recorded_pin_differs_from_verifier_invocation" in reasons


def test_b8_unknown_windows_guarantee_is_blocked_by_the_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasons = _reasons(
        monkeypatch, claimed_verified_guarantees=frozenset({"windows_oplock_semantics"})
    )
    assert "guarantee_not_on_allow_list:windows_oplock_semantics" in reasons
    assert "dep02_windows_guarantees_unresolved" in reasons


def test_b9_revoked_approval_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    """X2: approval 3 revoked at 4, then the same approval re-appended at 5."""
    reasons = _reasons(monkeypatch, revocation_seqs=(4,), candidate_approval_seqs=(3, 5))
    assert "approval_replayed_after_revocation" in reasons


def test_b9_revocation_before_the_first_approval_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X2 must not over-block: an older revocation followed by the only approval is fine."""
    _approved_baseline(monkeypatch)
    d = trust_gate(good(revocation_seqs=(3,), candidate_approval_seqs=(5,)))
    assert d.outcome == cg.RECORDABLE, d.reasons


def test_b10_same_store_pin_is_not_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons = _reasons(monkeypatch, pin_channel=cg.PIN_SAME_STORE)
    assert "store_head_pin_not_from_independent_channel" in reasons
    assert "same_store_pin_presented_as_independent" in _reasons(
        monkeypatch, same_store_pin_claimed_independent=True
    )
    assert "store_head_pin_channel_unknown" in _reasons(monkeypatch, pin_channel="")


def test_b11_valid_synthetic_bundle_only_under_explicit_test_only_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as m:
        _approved_baseline(m)
        assert trust_gate(good()).outcome == cg.RECORDABLE
    assert trust_gate(good()).outcome == cg.BLOCKED  # the override does not outlive the test
    assert list(inspect.signature(trust_gate).parameters) == ["b"]  # no override parameter


def test_b12_pr96_dependency_still_blocked() -> None:
    d = trust_gate(good())
    assert d.outcome == cg.BLOCKED
    assert d.reasons == (
        "dep01_principal_authentication_unavailable",
        "dep01_v4_custody_baseline_not_independently_approved",
    )
    assert cg.V4_REF_SHA == "d6a7cda72a88600e2ccf6554605f1c50478893ea"
    assert ENTRY_SHA != HEAD_SHA
