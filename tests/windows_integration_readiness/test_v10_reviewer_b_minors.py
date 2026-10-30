"""V10 regressions for Reviewer B round 1 on PR #98 (RB-1..RB-6, I-1..I-3). Synthetic only.

Each variant below derived PASS (or was accepted) at #98 head cb9a845 and must now be refused. The
records are rejected under the test-only dependency override, so the evidence rule itself is what
refuses them, not DEP-01/DEP-03.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness import evidence_validator as ev
from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.mt5_reference import classify_reference
from tests.windows_integration_readiness.test_evidence_validator import (
    check,
    real_extract_args,
    real_shaped_pass,
    synthetic,
    with_gate,
)

Rec = dict[str, Any]


def _report(mutate: Callable[[Rec], None]) -> ev.Report:
    r = real_shaped_pass()
    mutate(r)
    if check(r).gate is not None:  # re-record the gate decision when the inputs still allow one
        with_gate(r)
    return check(r)


def _refused(mutate: Callable[[Rec], None], code: str) -> None:
    report = _report(mutate)
    assert not report.ok
    assert code in report.errors, report.errors


def test_baseline_still_derives_pass_under_the_override() -> None:
    report = check(real_shaped_pass())
    assert report.ok, report.errors
    assert report.derived_overall == "PASS"


# ---- RB-1: case 6 result maps are validated positively ------------------------------------------
OBSERVED = {"outcome": "observed", "detail": "refused"}


@pytest.mark.parametrize(
    "hidden",
    [
        {"status": "error"},  # R4-1a
        "error",  # R4-1b
        {"result": "manual_required"},  # R4-1c
        {"outcome": "observed"},  # no detail
        {"outcome": "observed", "detail": "x", "note": "extra key"},
        {"outcome": {"nested": "error"}},
        ["error"],
    ],
)
@pytest.mark.parametrize("field", ev.CASE6_RESULT_FIELDS)
def test_rb1_non_probe_node_beside_an_observed_node_is_refused(field: str, hidden: Any) -> None:
    def mutate(r: Rec) -> None:
        r["cases"]["6"]["evidence"][field] = {"junction": OBSERVED, "symlink": hidden}

    _refused(mutate, f"invalid_value:cases.6.evidence.{field}:probe_results")


def test_rb1_a_list_instead_of_a_probe_map_is_refused() -> None:
    def mutate(r: Rec) -> None:
        r["cases"]["6"]["evidence"]["lock_results"] = [OBSERVED]

    _refused(mutate, "invalid_value:cases.6.evidence.lock_results:probe_results")


def test_rb1_the_real_probe_output_is_accepted_as_a_result_map(tmp_path: Any) -> None:
    from tests.windows_integration_readiness import filesystem_probes as fp

    probes = fp.run_probes(tmp_path)["probes"]
    assert all(ev._probe_node_ok(node) for node in probes.values())


# ---- RB-2: all four people are principal ids; no alias set -------------------------------------
@pytest.mark.parametrize(
    ("role", "spelling"),
    [
        ("second_person_verifier", lambda op, cu: op[:3] + "\u200b" + op[3:]),  # R4-2a ZWSP
        ("second_person_verifier", lambda op, cu: op.replace("a", "\u0430")),  # R4-2b Cyrillic
        ("reviewer", lambda op, cu: cu + "\u200d"),  # R4-2c ZWJ
        ("second_person_verifier", lambda op, cu: "Synth Verifier!"),  # R4-2d
        ("reviewer", lambda op, cu: op.upper()),
        ("custodian", lambda op, cu: " " + cu),
    ],
)
def test_rb2_disguised_or_ungrammatical_people_are_refused(
    role: str, spelling: Callable[[str, str], str]
) -> None:
    def mutate(r: Rec) -> None:
        ppl = r["people"]
        ppl[role] = spelling(ppl["operator"], ppl["custodian"])

    _refused(mutate, f"invalid_value:people.{role}:principal_id")


@pytest.mark.parametrize("role", ["second_person_verifier", "custodian", "reviewer"])
def test_rb2_exact_duplicates_of_the_operator_are_refused(role: str) -> None:
    def mutate(r: Rec) -> None:
        r["people"][role] = r["people"]["operator"]

    _refused(mutate, f"same_identity:operator={role}")


def test_rb2_reviewer_may_not_be_the_verifier() -> None:
    def mutate(r: Rec) -> None:
        r["people"]["reviewer"] = r["people"]["second_person_verifier"]

    _refused(mutate, "same_identity:second_person_verifier=reviewer")


@pytest.mark.parametrize(
    "identities", [["cust-carol", "carol-alias"], ["carol-alias"], ["op-alice"]]
)
def test_rb2_approver_identities_must_be_exactly_the_custodian(identities: list[str]) -> None:
    def mutate(r: Rec) -> None:
        r["custody"]["approver_identities"] = identities
        r["custody"]["registry_identities"] = sorted({*identities, "cust-carol"})

    _refused(mutate, "approver_identities_must_be_exactly_the_custodian")


@pytest.mark.parametrize("field", ["registry_identities", "approver_identities"])
def test_rb2_custody_identity_lists_use_the_principal_grammar(field: str) -> None:
    def mutate(r: Rec) -> None:
        r["custody"][field] = ["Cust Carol"]

    _refused(mutate, f"invalid_value:custody.{field}:principal_list")


# ---- RB-3: --dataset and commands_run are bound to the record ----------------------------------
def _set_flag(argv: list[str], flag: str, value: str) -> None:
    argv[argv.index(flag) + 1] = value


def test_rb3_verifier_dataset_must_be_the_recorded_dataset() -> None:
    def mutate(r: Rec) -> None:
        _set_flag(r["custody"]["verifier_argv"], "--dataset", "some-other-dataset-dir")
        r["execution"]["commands_run"][1] = list(r["custody"]["verifier_argv"])

    _refused(mutate, "verifier_argv_differs_from_record:--dataset")


@pytest.mark.parametrize(
    "command",
    [
        ["echo", "ok"],  # R4-3b
        ["python", "-c", "print('TRUSTED')"],
        ["scripts/verify_market_history.py"],
        ["-m", ev.PROBES_MODULE, "D:/probe"],  # -m without an interpreter
    ],
)
def test_rb3_commands_run_refuses_anything_but_the_reviewed_commands(command: list[str]) -> None:
    def mutate(r: Rec) -> None:
        r["execution"]["commands_run"].append(command)
        r["execution"]["exit_codes"].append(0)

    report = _report(mutate)
    assert not report.ok
    assert any(
        e.startswith(("commands_run_not_a_reviewed_command:", "commands_run_verifier_differs"))
        for e in report.errors
    ), report.errors


def test_rb3_free_text_commands_are_not_an_argv_list() -> None:
    _refused(
        lambda r: r["execution"].update(commands_run=["echo ok", "echo ok"]),
        "invalid_value:execution.commands_run:argv_list",
    )


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--out", "D:/nexora-runs/other/dataset"),
        ("--symbol", "EURUSD"),
        ("--from-raw-msc", "1700000000001"),
        ("--to-raw-msc", "1700000009999"),
        ("--operator", "op-mallory"),
    ],
)
def test_rb3_extractor_run_must_match_the_record(flag: str, value: str) -> None:
    def mutate(r: Rec) -> None:
        _set_flag(r["execution"]["commands_run"][0], flag, value)

    _refused(mutate, f"commands_run_extractor_differs_from_record:0:{flag}")


@pytest.mark.parametrize(
    ("drop", "code"),
    [
        (
            "--operator",
            "commands_run_extractor_invalid:0:candidate_out_needs_operator_and_operator_key",
        ),
        ("--candidate-out", "commands_run_extractor_invalid:0:missing_argument:--candidate-out"),
        ("--max-gap-ms", "commands_run_extractor_invalid:0:missing_argument:--max-gap-ms"),
    ],
)
def test_rb3_extractor_run_must_satisfy_the_execution_contract(drop: str, code: str) -> None:
    def mutate(r: Rec) -> None:
        argv = r["execution"]["commands_run"][0]
        i = argv.index(drop)
        del argv[i : i + 2]

    _refused(mutate, code)


def test_rb3_both_extractor_and_verifier_runs_are_required() -> None:
    def no_verifier(r: Rec) -> None:
        r["execution"].update(commands_run=r["execution"]["commands_run"][:1], exit_codes=[0])

    def no_extractor(r: Rec) -> None:
        r["execution"].update(commands_run=r["execution"]["commands_run"][1:], exit_codes=[0])

    _refused(no_verifier, "commands_run_missing_verifier_invocation")
    _refused(no_extractor, "commands_run_missing_extractor_invocation")


def _plan(r: Rec, **over: str) -> list[str]:
    args = {
        "--repo-root": "C:/nexora",
        "--provider": "mt5",
        "--preregistered-symbol": r["window"]["symbol"],
        "--extractor-sha": r["identity"]["extractor_sha"],
        "--expected-extractor-sha": r["identity"]["extractor_sha"],
        "--extractor-version": r["identity"]["extractor_version"],
        "--manifest-schema": r["identity"]["manifest_schema"],
        **over,
    }
    flat = [x for kv in args.items() for x in kv]
    return ["uv", "run", "python", "-m", ev.PLAN_MODULE, "plan", *flat, "--", *real_extract_args(r)]


def test_rb3_probes_and_plan_for_the_same_run_are_accepted() -> None:
    def mutate(r: Rec) -> None:
        r["execution"]["commands_run"] += [
            ["python", "-m", ev.PROBES_MODULE, "D:/probe-scratch"],
            _plan(r),
        ]
        r["execution"]["exit_codes"] += [0, 0]

    report = _report(mutate)
    assert report.ok, report.errors


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"--provider": "mock"}, "commands_run_plan_invalid:2"),
        ({"--preregistered-symbol": "EURUSD"}, "commands_run_plan_differs_from_record:2:symbol"),
        ({"--extractor-sha": "f" * 40}, "commands_run_plan_differs_from_record:2:extractor_sha"),
    ],
)
def test_rb3_a_plan_for_another_run_is_refused(over: dict[str, str], code: str) -> None:
    def mutate(r: Rec) -> None:
        r["execution"]["commands_run"].append(_plan(r, **over))
        r["execution"]["exit_codes"].append(0)

    _refused(mutate, code)


def test_rb3_a_plan_must_check_the_extractor_arguments_that_ran() -> None:
    def mutate(r: Rec) -> None:
        plan = _plan(r)
        _set_flag(plan, "--chunk-ms", "60000")
        r["execution"]["commands_run"].append(plan)
        r["execution"]["exit_codes"].append(0)

    _refused(mutate, "commands_run_plan_not_the_recorded_extractor_run")


def test_rb3_recorded_extract_args_matches_the_plan_contract() -> None:
    r = real_shaped_pass()
    args = ec.recorded_extract_args(real_extract_args(r))
    assert args["--out"] == r["custody"]["dataset_ref"]
    argv = real_extract_args(r)
    i = argv.index("--operator")
    del argv[i:]  # no operator attestation at all: a real run cannot become TRUSTED
    with pytest.raises(ec.ContractError, match="missing_argument:--operator"):
        ec.recorded_extract_args(argv)


# ---- RB-4: positive formats; record-id rehearsal markers ---------------------------------------
NOT_A_VERSION = ["0", "same as above", "see above", "unverified-by-operator", "not-checked", "x"]
NOT_A_VERSION += ["later", "5.0", "v5.0.45", "5.0.45 ", "5.0.45\n", "\uff15.0.45"]


@pytest.mark.parametrize("value", NOT_A_VERSION)
@pytest.mark.parametrize(
    ("section", "field", "kind"),
    [
        ("node", "metatrader5_version", "mt5_locked"),  # V11 (RB2-2): bound to uv.lock
        ("node", "numpy_version", "numpy_locked"),
        ("node", "uv_version", "release"),
        ("node", "python_version", "python_version"),
        ("node", "os_build", "windows_build"),
        ("terminal", "build", "terminal_build"),
    ],
)
def test_rb4_version_fields_need_their_positive_format(
    section: str, field: str, kind: str, value: str
) -> None:
    def mutate(r: Rec) -> None:
        r[section][field] = value

    report = _report(mutate)
    assert not report.ok
    assert f"invalid_value:{section}.{field}:{kind}" in report.errors or (
        f"placeholder_value:{section}.{field}" in report.errors
    ), report.errors


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("node", "python_version", "3.13.1"),
        ("node", "os_build", "10.0.26100.2605"),
        ("node", "os_build", "10.0.20348"),
        ("terminal", "build", "5120"),
    ],
)
def test_rb4_real_formats_are_accepted(section: str, field: str, value: str) -> None:
    def mutate(r: Rec) -> None:
        r[section][field] = value
        if (section, field) == ("terminal", "build"):
            r["cases"]["1"]["evidence"]["terminal_build"] = value

    report = _report(mutate)
    assert report.ok, report.errors


def test_rb4_case1_terminal_build_must_equal_the_terminal() -> None:
    def mutate(r: Rec) -> None:
        r["cases"]["1"]["evidence"]["terminal_build"] = "9999"

    _refused(mutate, "case1_terminal_build_differs_from_terminal.build")


def test_rb4_case1_numpy_version_is_the_locked_one() -> None:
    # V11 (RB2-2): node.numpy_version and case 1 are both bound to the uv.lock version, so a
    # different case 1 value is refused by its kind before the cross-check can see it
    def mutate(r: Rec) -> None:
        r["cases"]["1"]["evidence"]["numpy_version"] = "9.9.9"

    _refused(mutate, "invalid_value:cases.1.evidence.numpy_version:numpy_locked")


REHEARSAL_IDS = [
    "simulated-run-0001",
    "sample-run-0001",
    "test-run-0001",
    "emulated-run-0001",
    "demo-run-0001",
    "stub-run-0001",
    "SYN-0001-REHEARSAL",
    "dry-run-0001",
    "RUN-0001-SIMULATION",
    "sandboxRun0001",
]


@pytest.mark.parametrize("record_id", REHEARSAL_IDS)
def test_rb4_record_id_rehearsal_markers_are_refused(record_id: str) -> None:
    _refused(
        lambda r: r["identity"].update(record_id=record_id),
        "real_record_carries_mock_marker:identity.record_id",
    )


# V11 (RB2-2): the one REAL record id grammar, dated with the run's start (2026-01-01 here)
@pytest.mark.parametrize("record_id", ["RUN-20260101-0001", "RUN-20260101-9999"])
def test_rb4_plain_record_ids_are_accepted(record_id: str) -> None:
    report = _report(lambda r: r["identity"].update(record_id=record_id))
    assert report.ok, report.errors


def test_rb4_a_demo_server_name_is_still_allowed() -> None:
    report = _report(lambda r: r["terminal"].update(server_name="Broker-Demo"))
    assert report.ok, report.errors


# ---- RB-5: real_readiness never PASS beside errors ---------------------------------------------
def test_rb5_real_readiness_is_fail_whenever_the_record_has_errors() -> None:
    r = real_shaped_pass()
    r["cases"]["1"]["notes"] = 1  # case_notes_not_text; derivation alone would still say PASS
    report = check(r)
    assert report.derived_overall == "PASS"
    assert not report.ok
    assert report.as_json()["real_readiness"] == "FAIL"
    assert report.as_json()["structurally_valid"] is False


def test_rb5_honest_blocked_record_keeps_blocked() -> None:
    report = ev.validate_record(real_shaped_pass("BLOCKED"))
    assert report.ok and report.real_readiness == "BLOCKED"


# ---- RB-6: test gaps from the mutation sweep ---------------------------------------------------
def test_rb6_classify_head_compares_every_character() -> None:
    sha = dp.CANDIDATE_SHA
    last = "0" if sha[-1] != "0" else "1"
    assert dp.classify_head(sha[:-1] + last) == dp.MISMATCH  # RB19: shared 39-char prefix
    assert dp.classify_head(sha[:12] + "f" * 28) in (dp.MISMATCH, dp.PINNED)
    assert dp.classify_head(sha[:12] + "f" * 28) == (
        dp.PINNED if sha[12:] == "f" * 28 else dp.MISMATCH
    )
    assert dp.head_matches_candidate(sha)
    assert not dp.head_matches_candidate(sha[:-1] + last)


def test_rb6_two_mt5_receivers_are_not_an_allowed_read() -> None:
    assert classify_reference("mt5.mt5.copy_ticks_range").status != "ALLOWED"  # RB01


@pytest.mark.parametrize("value", ["syn-thetic source", "Fix_Ture source", "syn thetic"])
def test_rb6_a_marker_split_across_tokens_is_caught(value: str) -> None:
    assert ev.has_mock_marker(value)  # RB08: joined-token rule


# ---- INFO I-1..I-3 ------------------------------------------------------------------------------
def _bundle_reasons(**over: Any) -> tuple[str, ...]:
    r = real_shaped_pass()
    v = {s: r[s] for s in ev.SECTIONS}
    bundle = ev._custody_bundle(v, frozenset())
    assert bundle is not None
    return cg.trust_gate(_replace(bundle, **over)).reasons


def _replace(bundle: cg.CustodyBundle, **over: Any) -> cg.CustodyBundle:
    import dataclasses

    return dataclasses.replace(bundle, **over)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("pin_signature", "pin_signature_malformed"),
        ("pin_key_id", "pin_key_id_malformed"),
        ("approval_id", "approval_id_malformed"),
        ("approval_entry_sha256", "approval_entry_sha256_not_recorded"),
    ],
)
def test_i1_gate_alone_refuses_a_trailing_newline(field: str, reason: str) -> None:
    r = real_shaped_pass()
    value = r["custody"][field] + "\n"
    reasons = _bundle_reasons(**{field: value})
    assert any(x.startswith(reason.rsplit("_", 1)[0]) for x in reasons), reasons


def test_i1_gate_alone_refuses_a_trailing_newline_on_revocation_ids() -> None:
    reasons = _bundle_reasons(
        revocation_seqs=(1,), revocation_ids=("a" * 32 + "\n",), acknowledged_revocations=()
    )
    assert "revocation_ids_malformed" in reasons


def test_i2_gate_refuses_a_revocation_at_the_approval_seq() -> None:
    reasons = _bundle_reasons(revocation_seqs=(1,), revocation_ids=("a" * 32,))
    assert "revocation_recorded_at_approval_seq" in reasons


def test_i3_an_earlier_acknowledged_revocation_has_its_own_reason() -> None:
    def mutate(r: Rec) -> None:
        cu = r["custody"]
        rid = "a" * 32
        cu.update(
            approval_seq=2,
            candidate_approval_seqs=[2],
            revocation_seqs=[1],
            revocation_ids=[rid],
            acknowledged_revocations=[rid],
            revocations_after_approval=0,
        )

    errors = _report(mutate).errors
    assert "pass_requires_no_revocation_in_lineage" in errors
    assert "pass_requires_no_revocation_after_approval" not in errors


# ---- the synthetic fixture and the template still validate -------------------------------------
def test_fixture_and_template_still_validate() -> None:
    from tests.windows_integration_readiness.test_evidence_validator import template

    for rec in (synthetic(), template()):
        report = ev.validate_record(copy.deepcopy(rec))
        assert report.ok, report.errors
