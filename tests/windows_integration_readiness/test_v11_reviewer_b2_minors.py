"""V11 regressions for Reviewer B round 2 on PR #101 (RB2-1..RB2-4). Synthetic only.

Every refused variant below derived PASS (or was accepted) at #101 head 2feb915 and must now be
refused. Records are checked under the test-only dependency override, so the evidence rule itself
refuses them, not DEP-01/DEP-03.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import evidence_validator as ev
from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.test_evidence_validator import (
    check,
    real_extract_args,
    real_shaped_pass,
    with_gate,
)
from tests.windows_integration_readiness.test_execution_contract import _code, argv, plan

Rec = dict[str, Any]
REPO = Path(__file__).resolve().parents[2]


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


def _accepted(mutate: Callable[[Rec], None]) -> None:
    report = _report(mutate)
    assert report.ok, report.errors
    assert report.derived_overall == "PASS"


def _extract_flag(r: Rec, flag: str, value: str) -> None:
    run = r["execution"]["commands_run"][0]
    if flag in run:
        run[run.index(flag) + 1] = value
    else:
        run += [flag, value]


def test_baseline_still_derives_pass_under_the_override() -> None:
    report = check(real_shaped_pass())
    assert report.ok, report.errors
    assert report.derived_overall == "PASS"


# ---- RB2-1: the recorded extractor run's integer arguments follow #99's argparse and ranges -----
NOT_AN_INT = ["abc", "1e3", "1.0", "+5", "1_000", " 5", "5 ", "\u0665", "\uff15", "0x10"]
BAD_NUMBERS = [
    ("--chunk-ms", "0", "invalid_window_or_chunk"),
    ("--chunk-ms", "-5", "invalid_window_or_chunk"),
    ("--from-raw-msc", "-1", "invalid_window_or_chunk"),
    ("--max-gap-ms", "0", "invalid_max_gap_ms"),
    ("--max-gap-ms", "-1", "invalid_max_gap_ms"),
    ("--max-chunk-ticks", "0", "invalid_max_chunk_ticks"),
    ("--max-chunk-ticks", "-3", "invalid_max_chunk_ticks"),
    ("--protected-margin-ms", "-1", "invalid_protected_margin_ms"),
    ("--protected-margin-ms", str(2**62 + 1), "invalid_protected_margin_ms"),
]


@pytest.mark.parametrize("value", NOT_AN_INT)
@pytest.mark.parametrize(
    "flag", ["--chunk-ms", "--max-gap-ms", "--max-chunk-ticks", "--protected-margin-ms"]
)
def test_rb2_1_recorded_non_integer_argument_is_refused(flag: str, value: str) -> None:
    report = _report(lambda r: _extract_flag(r, flag, value))
    assert not report.ok
    assert any(e.startswith("commands_run_extractor_invalid:0:") for e in report.errors), (
        report.errors
    )


@pytest.mark.parametrize("value", NOT_AN_INT)
@pytest.mark.parametrize(
    "flag", ["--chunk-ms", "--max-gap-ms", "--max-chunk-ticks", "--protected-margin-ms"]
)
def test_rb2_1_recorded_args_refuse_non_integers_with_the_contract_code(
    flag: str, value: str
) -> None:
    args = real_extract_args(real_shaped_pass())
    args += [flag, value] if flag not in args else []
    args[args.index(flag) + 1] = value
    with pytest.raises(ec.ContractError) as e:
        ec.recorded_extract_args(args)
    assert e.value.code == "non_integer_argument"


@pytest.mark.parametrize(("flag", "value", "code"), BAD_NUMBERS)
def test_rb2_1_recorded_out_of_range_argument_is_refused(flag: str, value: str, code: str) -> None:
    _refused(lambda r: _extract_flag(r, flag, value), f"commands_run_extractor_invalid:0:{code}")


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--max-chunk-ticks", "1"),
        ("--max-chunk-ticks", "500000"),
        ("--protected-margin-ms", "0"),
        ("--protected-margin-ms", str(2**62)),
        ("--chunk-ms", "1"),
    ],
)
def test_rb2_1_recorded_boundary_values_are_accepted(flag: str, value: str) -> None:
    _accepted(lambda r: _extract_flag(r, flag, value))


@pytest.mark.parametrize(("flag", "value", "code"), BAD_NUMBERS)
def test_rb2_1_plan_invocation_applies_the_same_ranges(
    tmp_path: Path, flag: str, value: str, code: str
) -> None:
    out = tmp_path / "out"
    assert _code(lambda: plan(out, argv(out, **{flag: value}))) == code


@pytest.mark.parametrize("value", ["1e3", "+5", "1_000", "\u0665"])
def test_rb2_1_plan_invocation_is_lexically_strict(tmp_path: Path, value: str) -> None:
    out = tmp_path / "out"
    code = _code(lambda: plan(out, argv(out, **{"--chunk-ms": value})))
    assert code == "non_integer_argument"


DATASET = "D:/nexora-runs/run-0001/dataset"


@pytest.mark.parametrize("flag", ec.RECORDED_OUTSIDE_DATASET)
@pytest.mark.parametrize(
    "path",
    [
        DATASET + "/inside.json",
        "d:\\NEXORA-RUNS\\run-0001\\Dataset\\inside.json",  # NTFS: case and separator folded
        "D:/nexora-runs/run-0001/other/../dataset/inside.json",
        DATASET,
    ],
)
def test_rb2_1_recorded_input_or_custody_file_inside_the_dataset_is_refused(
    flag: str, path: str
) -> None:
    args = real_extract_args(real_shaped_pass())
    if flag == "--protected-utc-ranges":
        args.remove("--no-protected-ranges")
    if flag == "--anchor-out":
        args += ["--anchor-recorded-by", "cust-carol", "--anchor-storage-ref", "pr-comment"]
    if flag in args:
        args[args.index(flag) + 1] = path
    else:
        args += [flag, path]
    with pytest.raises(ec.ContractError) as e:
        ec.recorded_extract_args(args)
    assert e.value.code == f"{flag}_inside_dataset"


def test_rb2_1_a_key_inside_the_dataset_makes_the_record_fail() -> None:
    _refused(
        lambda r: _extract_flag(r, "--operator-key", DATASET + "/op.key"),
        "commands_run_extractor_invalid:0:--operator-key_inside_dataset",
    )


def test_rb2_1_a_sibling_folder_sharing_the_prefix_is_not_inside() -> None:
    _accepted(lambda r: _extract_flag(r, "--clock-attestation", DATASET + "2/ca.json"))


# ---- RB2-2: version fields bound to the lock; one positive REAL record id grammar --------------
@pytest.mark.parametrize(
    ("field", "value", "kind"),
    [
        ("metatrader5_version", "0.0.0", "mt5_locked"),
        ("metatrader5_version", "5.0.45", "mt5_locked"),
        ("metatrader5_version", "5.0.6181", "mt5_locked"),
        ("numpy_version", "0.0.0", "numpy_locked"),
        ("numpy_version", "2.1.3", "numpy_locked"),
        ("uv_version", "0.0.0", "release"),
        ("uv_version", "00.000.0", "release"),
        ("python_version", "3.0.0", "python_version"),
        ("python_version", "3.99.999", "python_version"),
        ("python_version", "3.12.7", "python_version"),
        ("python_version", "3.14.0", "python_version"),
        ("os_build", "10.0.00000", "windows_build"),
        ("os_build", "10.0.09999", "windows_build"),
    ],
)
def test_rb2_2_placeholder_or_unlocked_versions_are_refused(
    field: str, value: str, kind: str
) -> None:
    def mutate(r: Rec) -> None:
        r["node"][field] = value
        if field == "numpy_version":
            r["cases"]["1"]["evidence"]["numpy_version"] = value

    _refused(mutate, f"invalid_value:node.{field}:{kind}")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("uv_version", "0.9.5"),
        ("uv_version", "0.0.1"),
        ("python_version", "3.13.0"),
        ("os_build", "10.0.10240"),
        ("metatrader5_version", "5.0.6180"),
    ],
)
def test_rb2_2_real_versions_are_accepted(field: str, value: str) -> None:
    _accepted(lambda r: r["node"].update({field: value}))


def _lock_versions(lock: dict[str, Any]) -> dict[str, str]:
    return {p["name"]: p["version"] for p in lock["package"]}


def test_rb2_2_locked_versions_are_the_repository_lock() -> None:
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    versions = _lock_versions(lock)
    for name, version in ev.LOCKED_VERSIONS.items():
        assert versions[name] == version
    assert lock["requires-python"] == f"=={ev.LOCKED_PYTHON_SERIES}.*"
    major, minor = ev.LOCKED_PYTHON_SERIES.split(".")
    nxt = f"{major}.{int(minor) + 1}"
    assert project["project"]["requires-python"] == f">={ev.LOCKED_PYTHON_SERIES},<{nxt}"
    assert f"MetaTrader5=={ev.LOCKED_VERSIONS['metatrader5']}" in str(project["project"])


NOT_A_RUN_ID = [
    "smoke-run-0001",
    "pilot-run-0001",
    "staging-run-0001",
    "qa-run-0001",
    "dev-run-0001",
    "tst-run-0001",
    "sim-run-0001",
    "placeholder-run-0001",
    "s1mulated-run-0001",
    "copy-of-run-0001",
    "REAL-SHAPE-0001",
    "RUN-20260101-0000",  # sequence 0000
    "RUN-20261301-0001",  # month 13
    "RUN-20260230-0001",  # 30 February
    "RUN-2026011-0001",
    "RUN-20260101-001",
    "run-20260101-0001",
    "RUN-20260101-0001\n",
    "RUN-20260101-0001-smoke",
    "RUN-\uff12\uff10\uff12\uff16\uff10\uff11\uff10\uff11-0001",  # fullwidth digits
    "RUN-\u0662\u0660\u0662\u0666\u0660\u0661\u0660\u0661-0001",  # Arabic-Indic digits
]


@pytest.mark.parametrize("record_id", NOT_A_RUN_ID)
def test_rb2_2_record_id_outside_the_grammar_is_malformed(record_id: str) -> None:
    _refused(lambda r: r["identity"].update(record_id=record_id), "real_record_id_malformed")


def test_rb2_2_record_id_date_must_be_the_run_start_date() -> None:
    _refused(
        lambda r: r["identity"].update(record_id="RUN-20260102-0001"),
        "real_record_id_date_differs_from_started_at_utc",
    )


def test_rb2_2_record_id_date_is_not_checked_against_an_unverified_start() -> None:
    def mutate(r: Rec) -> None:
        r["timestamps"]["started_at_utc"] = ev.NOT_VERIFIED

    report = _report(mutate)
    assert "real_record_id_date_differs_from_started_at_utc" not in report.errors
    assert "real_record_id_malformed" not in report.errors


# ---- RB2-3: the candidate creator is the operator -----------------------------------------------
@pytest.mark.parametrize("creator", ["op-zed", "ver-bob", "rev-dan"])
def test_rb2_3_candidate_creator_other_than_the_operator_is_refused(creator: str) -> None:
    _refused(
        lambda r: r["custody"].update(candidate_creator=creator),
        "candidate_creator_must_be_the_operator",
    )


def test_rb2_3_creator_equal_to_the_custodian_is_still_self_approval() -> None:
    report = _report(lambda r: r["custody"].update(candidate_creator="cust-carol"))
    assert "candidate_creator_must_be_the_operator" in report.errors
    assert report.gate is not None
    assert "self_approval_by_candidate_creator" in report.gate.reasons


# ---- RB2-4: tests for Reviewer B's surviving mutants M04, M20, M24, M27, M33 --------------------
@pytest.mark.parametrize("detail", [5, None, ["refused"], {"text": "refused"}, True])
@pytest.mark.parametrize("field", ev.CASE6_RESULT_FIELDS)
def test_rb2_4_m04_probe_detail_must_be_a_string(field: str, detail: Any) -> None:
    def mutate(r: Rec) -> None:
        r["cases"]["6"]["evidence"][field] = {"junction": {"outcome": "observed", "detail": detail}}

    _refused(mutate, f"invalid_value:cases.6.evidence.{field}:probe_results")


@pytest.mark.parametrize("text", ["SYN-0001", "syn-run", "RUN-SYN-0001", "SynRun0001"])
def test_rb2_4_m20_syn_is_a_record_id_marker(text: str) -> None:
    assert ev.has_record_id_marker(text)
    assert not ev.has_mock_marker(text)  # only the record-id rule knows `syn`


def test_rb2_4_m20_syn_record_id_reports_the_marker() -> None:
    _refused(
        lambda r: r["identity"].update(record_id="SYN-20260101-0001"),
        "real_record_carries_mock_marker:identity.record_id",
    )


def _plan_command(r: Rec, *extra: str) -> list[str]:
    ident = r["identity"]
    return [
        "python",
        "-m",
        ev.PLAN_MODULE,
        "plan",
        "--repo-root",
        "C:/nexora",
        "--provider",
        "mt5",
        *extra,
        "--preregistered-symbol",
        r["window"]["symbol"],
        "--extractor-sha",
        ident["extractor_sha"],
        "--expected-extractor-sha",
        ident["extractor_sha"],
        "--extractor-version",
        ident["extractor_version"],
        "--manifest-schema",
        ident["manifest_schema"],
        "--",
        *real_extract_args(r),
    ]


def _append(command: Callable[[Rec], list[str]]) -> Callable[[Rec], None]:
    def mutate(r: Rec) -> None:
        r["execution"]["commands_run"].append(command(r))
        r["execution"]["exit_codes"].append(0)

    return mutate


def test_rb2_4_m24_plan_offline_is_refused_on_its_own() -> None:
    _accepted(_append(lambda r: _plan_command(r)))  # the same plan without --offline is fine
    report = _report(_append(lambda r: _plan_command(r, "--offline")))
    assert not report.ok
    assert report.errors == ["commands_run_plan_invalid:2"]


@pytest.mark.parametrize("field", ["registry_identities", "approver_identities"])
def test_rb2_4_m27_principal_list_refuses_duplicates(field: str) -> None:
    _refused(
        lambda r: r["custody"].update({field: ["cust-carol", "cust-carol"]}),
        f"invalid_value:custody.{field}:principal_list",
    )


@pytest.mark.parametrize("extra", [[], ["D:/probe-a", "D:/probe-b"]])
def test_rb2_4_m33_probes_take_exactly_one_directory(extra: list[str]) -> None:
    _accepted(_append(lambda r: ["python", "-m", ev.PROBES_MODULE, "D:/probe-a"]))
    _refused(
        _append(lambda r: ["python", "-m", ev.PROBES_MODULE, *extra]),
        "commands_run_probes_invalid:2",
    )
