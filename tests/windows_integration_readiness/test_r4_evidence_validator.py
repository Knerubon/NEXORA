"""Round 4 defensive assertions for the evidence validator (findings N1, N2, R3-1, R3-4).

Adversarial cases A1-A14 of the PR #95 Round 4 prompt. Each assertion states the REMEDIATED
behaviour (a specific refusal code), so it fails on `3cc8869`, where the same records derive PASS,
and passes here. Negative records start from the PASS-shaped baseline of
`test_evidence_validator.py`, checked with the test-only dependency override so that the rule under
test is the only thing that can reject them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import evidence_validator as ev
from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.test_evidence_validator import (
    DOCS,
    OVERRIDE,
    Rec,
    check,
    real_shaped_pass,
    synthetic,
    template,
    with_gate,
)


def _errors(mutate: Callable[[Rec], None], *, regate: bool = False) -> list[str]:
    r = real_shaped_pass()
    mutate(r)
    if regate:
        with_gate(r)
    report = check(r)
    assert not report.ok
    return report.errors


# ---- N1: summary flags are derived from what they summarise ----------------------------------
def test_a1_case5_pass_with_differing_run_hashes() -> None:
    errors = _errors(lambda r: r["cases"]["5"]["evidence"].update(run_b_content_sha256="e" * 64))
    assert "pass_contradicted_by_evidence:cases.5.run_hashes_differ" in errors


def test_a1_case5_variant_hash_and_dataset_binding() -> None:
    def variant(r: Rec) -> None:
        r["cases"]["5"]["evidence"]["variant_content_sha256"]["86400000"] = "e" * 64

    assert "pass_contradicted_by_evidence:cases.5.variant_hash_differs" in _errors(variant)

    def rebind(r: Rec) -> None:
        r["dataset"]["content_sha256"] = "e" * 64

    assert "pass_contradicted_by_evidence:cases.5.run_hash_differs_from_dataset" in _errors(rebind)


def test_a2_case7_pass_with_order_send_observed() -> None:
    def mutate(r: Rec) -> None:
        r["cases"]["7"]["evidence"]["observed_mt5_names"] = ["initialize", "order_send"]

    errors = _errors(mutate)
    assert "derived_flag_contradicts_evidence:cases.7.forbidden_names_observed" in errors
    assert "pass_contradicted_by_evidence:cases.7.forbidden_name_observed" in errors


def test_a3_nonzero_exit_code_with_overall_pass() -> None:
    errors = _errors(lambda r: r["execution"].update(exit_codes=[1]))
    assert "pass_with_nonzero_exit_code" in errors


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda r: r["cases"]["2"]["evidence"].update(independent_counts={"dup": 2}),
            "derived_flag_contradicts_evidence:cases.2.evidence.counts_equal",
        ),
        (
            lambda r: r["cases"]["4"]["evidence"].update(independent_fold_ranges=[[1, 2]]),
            "derived_flag_contradicts_evidence:cases.4.evidence.ranges_equal",
        ),
        (
            lambda r: r["execution"].update(exit_codes=[0]),
            "execution_record_incomplete:commands_run_vs_exit_codes",
        ),
        (
            lambda r: r["timestamps"].update(finished_at_utc="2026-01-01T00:00:00Z"),
            "timestamps_out_of_order:finished_before_started",
        ),
        (
            lambda r: r["window"].update(preregistered_at_utc="2026-01-02T00:00:00Z"),
            "preregistered_after_run_started",
        ),
    ],
)
def test_n1_other_summary_contradictions(mutate: Callable[[Rec], None], code: str) -> None:
    assert code in _errors(mutate)


def test_n1_contradiction_rejected_even_without_pass() -> None:
    r = synthetic()
    r["cases"]["2"]["evidence"]["independent_counts"] = {"dup": 2}
    r["cases"]["2"]["result"] = "FAIL"
    r["overall_result"] = "FAIL"
    errors = ev.validate_record(r).errors
    assert "derived_flag_contradicts_evidence:cases.2.evidence.counts_equal" in errors


# ---- N2: real readiness needs machine-checked dependencies ------------------------------------
def test_a4_real_readiness_pass_while_dep01_blocked() -> None:
    report = ev.validate_record(real_shaped_pass(), _test_only_dependencies={"DEP-03": ev.RESOLVED})
    assert "real_readiness_pass_while_dependency_blocked:DEP-01" in report.errors
    assert "real_readiness_pass_while_dependency_blocked:DEP-03" not in report.errors
    assert report.derived_overall == "BLOCKED"


def test_a5_real_readiness_pass_while_dep03_blocked() -> None:
    report = ev.validate_record(real_shaped_pass(), _test_only_dependencies={"DEP-01": ev.RESOLVED})
    assert "real_readiness_pass_while_dependency_blocked:DEP-03" in report.errors
    assert "real_readiness_pass_while_dependency_blocked:DEP-01" not in report.errors


def test_n2_dependency_status_is_derived_not_declared() -> None:
    deps = ev.dependency_status()
    assert deps == {"DEP-01": "BLOCKED", "DEP-02": "UNVERIFIED", "DEP-03": "BLOCKED"}
    report = ev.validate_record(real_shaped_pass("BLOCKED"))
    assert report.ok and report.real_readiness == "BLOCKED"
    assert report.as_json()["offline_harness_consistent"] is True
    assert "real_readiness_blocked_by:DEP-01" in report.notes


def test_n2_flipping_only_the_baseline_flag_does_not_unblock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cg, "V4_BASELINE_APPROVED", True)
    assert ev.dependency_status()["DEP-01"] == "BLOCKED"  # authentication still unavailable


def test_n2_even_resolved_flags_leave_unauthenticated_principals_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the test override, gate dependency reasons keep a real record BLOCKED."""
    monkeypatch.setattr(cg, "V4_BASELINE_APPROVED", True)
    monkeypatch.setattr(cg, "V4_PRINCIPAL_AUTHENTICATION_AVAILABLE", True)
    monkeypatch.setitem(ec.REAL_HALF_STEPS, "case1.step1-3.dtype_and_layout", (ec.EXISTING, "x"))
    for step, (_, why) in list(ec.REAL_HALF_STEPS.items()):
        if why.startswith("DEP-03"):
            monkeypatch.setitem(ec.REAL_HALF_STEPS, step, (ec.EXISTING, "x"))
    r = real_shaped_pass("BLOCKED")
    report = ev.validate_record(r)
    assert report.dependencies["DEP-01"] == ev.RESOLVED
    assert report.derived_overall == "BLOCKED", report.errors
    assert "real_readiness_blocked_by_gate:operator_identity_not_authenticated" in report.notes


# ---- R3-1: contradictory evidence ------------------------------------------------------------
def _argv(r: Rec, argv: list[str]) -> None:
    r["custody"]["verifier_argv"] = argv


def test_a6_anchor_only_verifier_with_trusted_status() -> None:
    argv = [cg.V4_VERIFY_SCRIPT, "--dataset", "d", "--anchor", "a", "--require-trusted"]
    errors = _errors(lambda r: _argv(r, argv))
    assert "verifier_argv_invalid:verifier_argv_missing:--store" in errors
    assert "verifier_argv_invalid:verifier_argv_missing:--store-head" in errors
    assert "verifier_trusted_without_reviewed_v4_invocation" in errors
    assert "custody_gate_decision_mismatch" in errors


def test_a7_echo_trusted_verifier_command() -> None:
    errors = _errors(lambda r: _argv(r, ["echo", "TRUSTED"]), regate=True)
    assert "verifier_argv_invalid:verifier_argv_not_v4_verifier" in errors
    assert "verifier_trusted_without_reviewed_v4_invocation" in errors
    assert "pass_contradicted_by_custody_gate:verifier_argv_not_v4_verifier" in errors


def test_r3_1_free_text_verifier_command_is_not_a_field() -> None:
    def mutate(r: Rec) -> None:
        r["custody"]["verifier_command"] = "scripts/verify_market_history.py --require-trusted"

    assert "unknown_field:custody.verifier_command" in _errors(mutate)


def test_r3_1_argv_must_match_the_recorded_pin_and_store() -> None:
    def mutate(r: Rec) -> None:
        argv = list(r["custody"]["verifier_argv"])
        argv[argv.index("--store-head") + 1] = "2:" + "b" * 64
        argv[argv.index("--store") + 1] = "other-store"
        r["custody"]["verifier_argv"] = argv

    errors = _errors(mutate, regate=True)
    assert "verifier_argv_differs_from_record:--store-head" in errors
    assert "verifier_argv_differs_from_record:--store" in errors


def test_r3_1_recorded_gate_decision_must_match_the_gate() -> None:
    def mutate(r: Rec) -> None:
        r["custody"]["gate_outcome"] = cg.RECORDABLE
        r["custody"]["gate_reasons"] = []

    assert "custody_gate_decision_mismatch" in _errors(mutate)


def test_r3_1_approval_sequence_and_pinned_chain_head() -> None:
    def mutate(r: Rec) -> None:
        r["custody"].update(approval_seq=9, candidate_approval_seqs=[9])

    errors = _errors(mutate, regate=True)
    assert "pass_contradicted_by_custody_gate:approval_outside_pinned_chain" in errors

    def revocations(r: Rec) -> None:
        r["custody"]["revocation_seqs"] = [2]

    errors = _errors(revocations, regate=True)
    assert "revocations_after_approval_contradicts_revocation_seqs" in errors


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("identity", "record_id", "SYNTHETIC-0001"),
        ("identity", "record_id", "TEST-ONLY-PASS-SHAPE"),
        ("node", "hostname_class", "mock-node"),
        ("custody", "store_ref", "fixture-store"),
    ],
)
def test_a8_real_record_with_synthetic_markers(section: str, key: str, value: str) -> None:
    def mutate(r: Rec) -> None:
        r[section][key] = value
        if key == "store_ref":
            argv = r["custody"]["verifier_argv"]
            argv[argv.index("--store") + 1] = value

    errors = _errors(mutate, regate=key == "store_ref")
    assert f"real_record_carries_mock_marker:{section}.{key}" in errors


@pytest.mark.parametrize(
    "value", [ec.MOCK_SOURCE, "SYNTHETIC-source", "ＭＯＣＫ terminal", "synthetic_mock"]
)
def test_a9_real_record_with_mock_source_descriptor(value: str) -> None:
    errors = _errors(lambda r: r["source_provenance"].update(source_descriptor=value))
    assert "real_record_carries_mock_marker:source_provenance.source_descriptor" in errors


def test_a9_real_record_with_synthetic_commands() -> None:
    errors = _errors(lambda r: r["execution"].update(commands_run=[["SYNTHETIC: no command"]]))
    assert "real_record_carries_mock_marker:execution.commands_run" in errors


@pytest.mark.parametrize(
    "claim",
    [
        "windows_msvcrt_lock_exclusivity",
        "windows_ntfs_atomicity_and_hardlink_behaviour",
        "reparse_point_and_junction_handling",
    ],
)
def test_a10_unsupported_windows_guarantee_moved_to_claim_evidence(claim: str) -> None:
    def mutate(r: Rec) -> None:
        r["not_verified"].remove(claim)
        r["claim_evidence"][claim] = {"path": "evidence/x.json", "sha256": "a" * 64}

    errors = _errors(mutate, regate=True)
    assert f"claim_requires_unresolved_dependency:DEP-02:{claim}" in errors
    assert f"pass_contradicted_by_custody_gate:guarantee_not_on_allow_list:{claim}" in errors
    # even with DEP-02 pretended resolved, the gate's empty allow-list still refuses it
    r = real_shaped_pass()
    mutate(r)
    with_gate(r)
    report = ev.validate_record(r, _test_only_dependencies={**OVERRIDE, "DEP-02": ev.RESOLVED})
    assert f"pass_contradicted_by_custody_gate:guarantee_not_on_allow_list:{claim}" in (
        report.errors
    )


@pytest.mark.parametrize(
    ("claim", "code"),
    [
        ("tick_completeness", "claim_has_no_reviewed_evidence_method:tick_completeness"),
        (
            "custodian_identity_authenticity",
            "claim_requires_unresolved_dependency:DEP-01:custodian_identity_authenticity",
        ),
        (
            "documented_tick_dtype_matches_terminal_build",
            "claim_requires_unresolved_dependency:DEP-03:documented_tick_dtype_matches_terminal_build",
        ),
    ],
)
def test_a10_other_claims_owned_by_unresolved_dependencies(claim: str, code: str) -> None:
    r = real_shaped_pass("BLOCKED")
    r["not_verified"].remove(claim)
    r["claim_evidence"][claim] = {"path": "evidence/x.json", "sha256": "a" * 64}
    assert code in ev.validate_record(r).errors


def test_a10_claim_with_a_reviewable_reference_is_still_accepted() -> None:
    r = real_shaped_pass()
    r["not_verified"].remove("broker_clock_rule_truth")
    r["claim_evidence"]["broker_clock_rule_truth"] = {"path": "broker.pdf", "sha256": "a" * 64}
    assert check(r).ok


def test_r3_1_every_mandatory_claim_has_an_owner_entry() -> None:
    assert set(ev.CLAIM_OWNER) == set(ev.MANDATORY_NOT_VERIFIED)


# ---- R3-4: placeholder spellings -------------------------------------------------------------
PLACEHOLDERS = [
    "not verified",
    "NOT  VERIFIED",
    "Not Verified.",
    "not_verified",
    "NOT-VERIFIED",
    "ＮＯＴ ＶＥＲＩＦＩＥＤ",  # fullwidth (NFKC)
    "NOT​VERIFIED",  # zero-width space
    "n/a",
    "N.A.",
    "TBD",
    "unknown",
    "-",
    "?",
]


@pytest.mark.parametrize("value", PLACEHOLDERS)
def test_a11_placeholder_text_values_are_rejected(value: str) -> None:
    errors = _errors(lambda r: r["node"].update(antivirus_indexer_state=value))
    assert "placeholder_value:node.antivirus_indexer_state" in errors


@pytest.mark.parametrize("value", ["not verified", "NOT VERIFIED "])
def test_a11_placeholders_in_lists_reasons_and_refs(value: str) -> None:
    errors = _errors(lambda r: r["cases"]["3"]["evidence"].update(refusals_observed=[value]))
    assert "placeholder_value:cases.3.evidence.refusals_observed" in errors
    errors = _errors(lambda r: r["people"]["verifier_attestation_ref"].update(path=value))
    assert "placeholder_value:people.verifier_attestation_ref" in errors


@pytest.mark.parametrize(
    "value",
    [
        "Windows 11 Pro 23H2 (build 22631.4317)",
        "partially not verified by vendor; see attached volume info",
        "none installed",
        "NA-1 server",
    ],
)
def test_a11_valid_free_text_is_not_mistaken_for_a_placeholder(value: str) -> None:
    r = real_shaped_pass()
    r["node"]["antivirus_indexer_state"] = value
    assert check(r).ok, check(r).errors


def test_a11_the_exact_sentinel_still_marks_an_unfilled_field() -> None:
    r = synthetic()
    r["node"]["antivirus_indexer_state"] = ev.NOT_VERIFIED
    assert ev.validate_record(r).ok
    assert not ev.is_placeholder(ev.NOT_VERIFIED)


# ---- valid records and schema ----------------------------------------------------------------
def test_a12_valid_offline_synthetic_record() -> None:
    report = ev.validate_record(synthetic())
    assert report.ok, report.errors
    assert report.derived_overall == "NOT_RUN"
    assert report.real_readiness == "NOT_CLAIMED"
    assert report.gate is not None and report.gate.outcome == cg.BLOCKED
    assert report.as_json()["windows_verified"] is False


@pytest.mark.parametrize("launcher", cg.V4_VERIFY_LAUNCHERS)
def test_a13_valid_structured_verifier_command(launcher: tuple[str, ...]) -> None:
    r = real_shaped_pass()
    r["custody"]["verifier_argv"] = [*launcher, *r["custody"]["verifier_argv"]]
    r["execution"]["commands_run"][1] = list(r["custody"]["verifier_argv"])
    with_gate(r)
    report = check(r)
    assert report.ok, report.errors
    assert report.derived_overall == "PASS"  # only under the test-only dependency override


def test_a14_schema_has_no_drift() -> None:
    committed = json.loads((DOCS / "evidence-record.schema.json").read_text(encoding="utf-8"))
    assert committed == ev.build_schema()
    assert committed["$id"] == ev.SCHEMA_VERSION == "nexora.windows-readiness-evidence/5"
    assert committed["properties"]["overall_result"]["enum"] == list(ev.OVERALL_RESULTS)
    assert template()["schema_version"] == ev.SCHEMA_VERSION


def _schema() -> Any:
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema is not in the locked env; validator half still runs"
    )
    path = Path(DOCS / "evidence-record.schema.json")
    return jsonschema.Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("value", [p for p in PLACEHOLDERS if p.isascii()])
def test_a14_schema_rejects_ascii_placeholders_too(value: str) -> None:
    r = real_shaped_pass("BLOCKED")
    r["node"]["antivirus_indexer_state"] = value
    assert list(_schema().iter_errors(r))


def test_a14_schema_accepts_the_valid_records() -> None:
    v = _schema()
    for rec in (template(), synthetic(), real_shaped_pass(), real_shaped_pass("BLOCKED")):
        assert not list(v.iter_errors(rec))
