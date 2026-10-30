"""Finding B-M1 regression: an evidence record cannot claim PASS without complete, consistent proof.

Adversarial cases A1-A10 from the PR #95 remediation prompt, plus Reviewer B's B8 reproductions.
Every negative case must be rejected by `validate_record` (authoritative). Cases marked
`schema=True` must ALSO be rejected by the committed JSON Schema; that half runs only where
`jsonschema` is installed (it is not in the locked env), and is skipped otherwise with that reason.

Round 4: a real-half PASS is BLOCKED while DEP-01/DEP-03 are BLOCKED (N2), so the negative cases run
against a PASS-shaped record with `_test_only_dependencies` marking those dependencies RESOLVED.
Without that test-only override the same baseline is rejected (see the baseline tests).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness.evidence_validator import (
    MANDATORY_NOT_VERIFIED,
    NOT_VERIFIED,
    RESOLVED,
    SCHEMA_VERSION,
    Report,
    validate_record,
)
from tests.windows_integration_readiness.execution_contract import EXTRACT_SCRIPT

HERE = Path(__file__).resolve().parent
DOCS = HERE.parents[1] / "docs" / "research" / "market-history-windows-readiness"
Rec = dict[str, Any]


def _load(path: Path) -> Rec:
    data: Rec = json.loads(path.read_text(encoding="utf-8"))
    return data


def synthetic() -> Rec:
    return _load(HERE / "evidence_fixtures" / "valid-synthetic.json")


def template() -> Rec:
    return _load(DOCS / "evidence-record.template.json")


# Test-only: pretend the dependencies a real PASS needs are resolved (never used by the CLI).
OVERRIDE = {"DEP-01": RESOLVED, "DEP-03": RESOLVED}


def with_gate(r: Rec) -> Rec:
    """Record the custody gate decision the checklist would record for this custody section."""
    r["custody"]["gate_outcome"] = NOT_VERIFIED
    r["custody"]["gate_reasons"] = NOT_VERIFIED
    gate = validate_record(r).gate
    assert gate is not None
    r["custody"]["gate_outcome"] = gate.outcome
    r["custody"]["gate_reasons"] = list(gate.reasons)
    return r


def real_shaped_pass(overall: str = "PASS") -> Rec:
    """In-memory only, never committed: the synthetic fixture rewritten as a real-shaped run with a
    TRUSTED independent verification (on a proven platform), so the PASS derivation itself can be
    attacked. Provenance fields carry no synthetic markers."""
    r = synthetic()
    r["evidence_class"] = "REAL_WINDOWS_RUN"
    r["identity"]["record_id"] = "REAL-SHAPE-0001"
    r["node"]["hostname_class"] = "node-class-a"
    r["terminal"].update(company="Broker Co", name="Terminal", server_name="Server-1")
    r["source_provenance"]["source_descriptor"] = "terminal copy_ticks_range"
    r["source_provenance"]["broker_clock_evidence_kind"] = "broker_statement"
    r["timestamps"]["clock_source"] = "ntp"
    r["dataset"]["development_status"] = "TRUSTED"
    # people and custody principals carry no synthetic marker either: the operator id appears in
    # the recorded extractor command, which a real record marker-checks
    r["people"].update(
        operator="op-alice", second_person_verifier="ver-bob", custodian="cust-carol"
    )
    r["people"]["reviewer"] = "rev-dan"
    r["custody"].update(
        candidate_creator="op-alice",
        registry_identities=["cust-carol"],
        approver_identities=["cust-carol"],
        pin_pinned_by="pin-erin",
        dataset_ref="D:/nexora-runs/run-0001/dataset",
    )
    head = r["custody"]["store_head"]
    r["custody"]["registry_ref"]["path"] = "registry.json"
    r["custody"]["pin_ref"]["path"] = "pin.json"
    # a real run carries the probe outcomes the case 6 counters are derived from
    c6 = r["cases"]["6"]["evidence"]
    c6["reparse_results"] = {"junction": {"outcome": "observed", "detail": "refused"}}
    c6["crash_results"] = {"kill_between_chunks": {"outcome": "observed", "detail": "no manifest"}}
    c6["lock_results"] = {"second_writer": {"outcome": "observed", "detail": "lock held"}}
    r["custody"].update(
        store_ref="store-dir",
        verifier_argv=[
            cg.V4_VERIFY_SCRIPT,
            "--dataset",
            r["custody"]["dataset_ref"],
            "--store",
            "store-dir",
            "--registry",
            r["custody"]["registry_ref"]["path"],
            "--store-head",
            head,
            "--pin",
            r["custody"]["pin_ref"]["path"],
            "--max-pin-age-seconds",
            str(r["custody"]["max_pin_age_seconds"]),
            "--require-trusted",
        ],
        extraction_platform="linux",
        verifier_status="TRUSTED",
        verifier_exit_code=0,
        verifier_status_reasons=[],
    )
    r["execution"]["commands_run"] = [
        ["python", EXTRACT_SCRIPT, *real_extract_args(r)],
        list(r["custody"]["verifier_argv"]),
    ]
    r["execution"]["exit_codes"] = [0, 0]
    r["overall_result"] = overall
    return with_gate(r)


def real_extract_args(r: Rec) -> list[str]:
    """The extractor arguments a real run records, bound to the record's window and dataset."""
    window = r["window"]["window_raw_msc"]
    return [
        "--symbol",
        r["window"]["symbol"],
        "--from-raw-msc",
        str(window[0]),
        "--to-raw-msc",
        str(window[1]),
        "--chunk-ms",
        "3600000",
        "--max-gap-ms",
        "60000",
        "--time-conversion",
        "D:/nexora-runs/run-0001/time-conversion.json",
        "--clock-attestation",
        "D:/nexora-runs/run-0001/clock-attestation.json",
        "--no-protected-ranges",
        "--out",
        r["custody"]["dataset_ref"],
        "--operator",
        r["people"]["operator"],
        "--operator-key",
        "E:/keys/op-alice.key",
        "--candidate-out",
        "D:/nexora-runs/run-0001/candidate.json",
    ]


def check(r: Rec) -> Report:
    return validate_record(r, _test_only_dependencies=OVERRIDE)


def _every_leaf(r: Rec, value: Any) -> None:
    for key, sec in r.items():
        if isinstance(sec, dict) and key not in ("cases", "claim_evidence"):
            for k in sec:
                sec[k] = value
    for c in r["cases"].values():
        for k in c["evidence"]:
            c["evidence"][k] = value


def _a1(r: Rec) -> None:  # A1: PASS with every field NOT VERIFIED
    r.update(template())
    for c in r["cases"].values():
        c["result"] = "PASS"
    r["overall_result"] = "PASS"


def _a1_real(r: Rec) -> None:
    _every_leaf(r, NOT_VERIFIED)


def _a2(r: Rec) -> None:  # A2: PASS with an empty not_verified list
    r["not_verified"] = []


def _a3_flag(r: Rec) -> None:  # A3: dirty checkout recorded, flag says clean
    r["checkout"]["status_short"] = " M packages/x.py"


def _a3_false(r: Rec) -> None:  # A3: dirty checkout admitted, still PASS
    r["checkout"]["status_short"] = " M packages/x.py"
    r["checkout"]["checkout_clean"] = False


def _a3_head(r: Rec) -> None:
    r["checkout"]["head"] = "f" * 40


def _a4(r: Rec) -> None:  # A4: operator signs as the second-person verifier
    r["people"]["second_person_verifier"] = r["people"]["operator"]


def _a4_disguised(r: Rec) -> None:
    r["people"]["second_person_verifier"] = r["people"]["operator"].upper()


def _a4_custodian(r: Rec) -> None:
    r["people"]["custodian"] = r["people"]["operator"]


def _a5_missing(r: Rec) -> None:  # A5: verifier identity missing
    del r["people"]["second_person_verifier"]


def _a5_nv(r: Rec) -> None:
    r["people"]["second_person_verifier"] = NOT_VERIFIED


def _a5_blank(r: Rec) -> None:
    r["people"]["second_person_verifier"] = "   "


def _a6_case(r: Rec) -> None:  # A6: missing evidence references
    r["cases"]["5"]["evidence_refs"] = []


def _a6_case_nv(r: Rec) -> None:
    r["cases"]["5"]["evidence_refs"] = NOT_VERIFIED


def _a6_raw(r: Rec) -> None:
    r["execution"]["raw_output_refs"] = [{"path": "out.txt"}]


def _a6_attest(r: Rec) -> None:
    r["people"]["verifier_attestation_ref"] = {"path": "x", "sha256": "not-a-hash"}


def _a7_case_fail(r: Rec) -> None:  # A7: field-level vs overall contradiction
    r["cases"]["3"]["result"] = "FAIL"


def _a7_evidence(r: Rec) -> None:
    r["cases"]["3"]["evidence"]["terminal_contacted_before_validation"] = True


def _a7_trusted(r: Rec) -> None:
    r["custody"].update(verifier_status="UNTRUSTED", verifier_exit_code=1)


def _a7_untrusted_ds(r: Rec) -> None:
    r["dataset"]["development_status"] = "UNTRUSTED"


def _a7_revoked(r: Rec) -> None:
    r["custody"]["revocations_after_approval"] = 1


def _a7_probe_errors(r: Rec) -> None:
    r["cases"]["6"]["evidence"]["probe_error_count"] = 2


def _a8_section(r: Rec) -> None:  # A8: altered / malformed template
    r["node"] = {"x": "y"}


def _a8_extra(r: Rec) -> None:
    r["approved_by_reviewer"] = True


def _a8_blank(r: Rec) -> None:
    r["terminal"]["company"] = " "


def _a8_empty_list(r: Rec) -> None:
    r["execution"]["commands_run"] = []


def _a8_empty_obj(r: Rec) -> None:
    r["cases"]["4"]["evidence"]["transitions"] = {}


def _a8_case_missing(r: Rec) -> None:
    del r["cases"]["7"]


def _a8_not_object(r: Rec) -> None:
    r["custody"] = "TRUSTED"


def _a9_enum(r: Rec) -> None:  # A9: unknown enum values / schema version
    r["terminal"]["account_kind"] = "live"


def _a9_result(r: Rec) -> None:
    r["cases"]["1"]["result"] = "VERIFIED"


def _a9_overall(r: Rec) -> None:
    r["overall_result"] = "VERIFIED"


def _a9_version(r: Rec) -> None:
    r["schema_version"] = "nexora.windows-readiness-evidence/1"


def _a9_class(r: Rec) -> None:
    r["evidence_class"] = "WINDOWS_VERIFIED"


def _a9_identity_basis(r: Rec) -> None:
    r["identity_basis"] = "cryptographically_verified"


def _a9_claim(r: Rec) -> None:
    r["not_verified"] = [*r["not_verified"], "everything_else"]


def _drop_claim(r: Rec) -> None:
    r["not_verified"] = [c for c in r["not_verified"] if c != "tick_completeness"]


def _claim_both(r: Rec) -> None:
    r["claim_evidence"] = {"tick_completeness": {"path": "p", "sha256": "a" * 64}}


NEGATIVE: list[tuple[str, Callable[[Rec], None], bool]] = [
    ("A1_template_claims_pass", _a1, True),
    ("A1_every_field_not_verified", _a1_real, True),
    ("A2_empty_not_verified", _a2, False),
    ("A2_claim_dropped_without_evidence", _drop_claim, False),
    ("A2_claim_both_listed_and_evidenced", _claim_both, False),
    ("A3_dirty_status_flag_clean", _a3_flag, True),
    ("A3_dirty_checkout_admitted", _a3_false, True),
    ("A3_head_differs_from_sha", _a3_head, False),
    ("A4_operator_is_verifier", _a4, False),
    ("A4_operator_is_verifier_disguised", _a4_disguised, False),
    ("A4_operator_is_custodian", _a4_custodian, False),
    ("A5_verifier_missing", _a5_missing, True),
    ("A5_verifier_not_verified", _a5_nv, True),
    ("A5_verifier_blank", _a5_blank, True),
    ("A6_case_refs_empty", _a6_case, True),
    ("A6_case_refs_not_verified", _a6_case_nv, True),
    ("A6_raw_ref_without_hash", _a6_raw, True),
    ("A6_attestation_ref_bad_hash", _a6_attest, True),
    ("A7_case_fail_overall_pass", _a7_case_fail, True),
    ("A7_case3_terminal_contacted", _a7_evidence, True),
    ("A7_verifier_untrusted_overall_pass", _a7_trusted, True),
    ("A7_dataset_untrusted_overall_pass", _a7_untrusted_ds, True),
    ("A7_revoked_after_approval", _a7_revoked, True),
    ("A7_probe_errors_case_pass", _a7_probe_errors, True),
    ("A8_section_replaced", _a8_section, True),
    ("A8_extra_top_level_key", _a8_extra, True),
    ("A8_whitespace_value", _a8_blank, True),
    ("A8_empty_list_value", _a8_empty_list, True),
    ("A8_empty_object_value", _a8_empty_obj, True),
    ("A8_case_missing", _a8_case_missing, True),
    ("A8_section_not_object", _a8_not_object, True),
    ("A9_unknown_account_kind", _a9_enum, True),
    ("A9_unknown_case_result", _a9_result, True),
    ("A9_unknown_overall_result", _a9_overall, True),
    ("A9_schema_version_mismatch", _a9_version, True),
    ("A9_unknown_evidence_class", _a9_class, True),
    ("A9_identity_basis_overclaimed", _a9_identity_basis, True),
    ("A9_unknown_claim_id", _a9_claim, True),
]


def _mutated(fn: Callable[[Rec], None]) -> Rec:
    r = real_shaped_pass()
    fn(r)
    return r


def test_baseline_real_shaped_pass_is_accepted_so_negatives_are_meaningful() -> None:
    report = check(real_shaped_pass())
    assert report.ok, report.errors
    assert report.derived_overall == "PASS"
    assert report.as_json()["dependency_source"] == "test_override"


def test_baseline_real_shaped_record_is_blocked_without_the_test_override() -> None:
    report = validate_record(real_shaped_pass())
    assert set(report.errors) == {
        "overall_result_not_derivable:claimed=PASS,derived=BLOCKED",
        "real_readiness_pass_while_dependency_blocked:DEP-01",
        "real_readiness_pass_while_dependency_blocked:DEP-03",
    }
    honest = validate_record(real_shaped_pass("BLOCKED"))
    assert honest.ok, honest.errors
    assert honest.real_readiness == "BLOCKED"
    assert honest.as_json()["dependency_source"] == "derived"


@pytest.mark.parametrize(("name", "mutate"), [(n, f) for n, f, _ in NEGATIVE])
def test_negative_record_is_rejected(name: str, mutate: Callable[[Rec], None]) -> None:
    report = check(_mutated(mutate))
    assert not report.ok, name
    assert report.as_json()["windows_verified"] is False


def test_a1_pass_derivation_ignores_the_claimed_value() -> None:
    r = template()
    r["overall_result"] = "PASS"
    report = validate_record(r)
    assert report.derived_overall == "NOT_RUN"
    assert "overall_result_not_derivable:claimed=PASS,derived=NOT_RUN" in report.errors


def test_a3_contradiction_rejected_even_when_not_claiming_pass() -> None:
    r = synthetic()
    r["checkout"]["status_short"] = "?? stray"
    assert "checkout_clean_contradicts_status_short" in validate_record(r).errors


def test_a4_reports_which_identities_collide() -> None:
    assert "same_identity:operator=second_person_verifier" in check(_mutated(_a4)).errors
    # RB-2: a disguised spelling is no longer a "different" name: it is not a principal id at all
    errors = check(_mutated(_a4_disguised)).errors
    assert "invalid_value:people.second_person_verifier:principal_id" in errors


def test_a10_synthetic_fixture_passes_structural_checks_without_claiming_windows() -> None:
    r = synthetic()
    report = validate_record(r)
    assert report.ok, report.errors
    assert report.derived_overall == "NOT_RUN"
    assert report.as_json()["windows_verified"] is False
    assert report.as_json()["identity_authenticated"] is False
    assert r["evidence_class"] == "SYNTHETIC_FIXTURE"
    assert r["identity"]["record_id"].startswith("SYNTHETIC-")
    assert set(r["not_verified"]) == set(MANDATORY_NOT_VERIFIED)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(overall_result="PASS"),
        lambda r: r["dataset"].update(development_status="TRUSTED"),
        lambda r: r["identity"].update(record_id="REAL-0001"),
    ],
)
def test_a10_synthetic_fixture_cannot_claim_pass_trust_or_a_real_identity(
    mutate: Callable[[Rec], None],
) -> None:
    r = synthetic()
    mutate(r)
    assert not validate_record(r).ok


def test_template_is_valid_and_unfilled() -> None:
    r = template()
    assert validate_record(r).ok
    r["node"]["antivirus_indexer_state"] = "Windows Defender on"
    assert "template_must_be_unfilled" in validate_record(r).errors


def test_non_object_record_rejected() -> None:
    assert not validate_record([]).ok
    assert not validate_record(None).ok


def test_cli_exit_codes(tmp_path: Path) -> None:
    from tests.windows_integration_readiness.evidence_validator import main

    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(synthetic()), encoding="utf-8")
    bad.write_text(json.dumps(_mutated(_a2)), encoding="utf-8")
    assert main(["x", str(good)]) == 0
    assert main(["x", str(bad)]) == 1
    assert main(["x", str(tmp_path / "missing.json")]) == 2


# ---- the JSON Schema must agree where it can express the rule ---------------------------------
def _schema_validator() -> Any:
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema is not in the locked env; validator half still runs"
    )
    schema = _load(DOCS / "evidence-record.schema.json")
    assert schema["$id"] == SCHEMA_VERSION
    return jsonschema.Draft202012Validator(schema)


def test_schema_accepts_template_synthetic_and_real_shape() -> None:
    v = _schema_validator()
    for rec in (template(), synthetic(), real_shaped_pass()):
        assert not list(v.iter_errors(rec))


@pytest.mark.parametrize(("name", "mutate"), [(n, f) for n, f, s in NEGATIVE if s])
def test_schema_rejects_expressible_negative(name: str, mutate: Callable[[Rec], None]) -> None:
    v = _schema_validator()
    assert list(v.iter_errors(_mutated(mutate))), name


def test_negative_fixture_names_cover_a1_to_a9() -> None:
    prefixes = {n.split("_")[0] for n, _, _ in NEGATIVE}
    assert prefixes == {f"A{i}" for i in range(1, 10)}
