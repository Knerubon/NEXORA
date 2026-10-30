r"""V13 regressions for Reviewer B round 4 on PR #103 (RB4-1, RB4-2, I-b). Synthetic only.

RB4-1: root-relative (`\runs\...`), drive-relative (`D:dataset`) and plain relative paths were
compared against an absolute `--out` and never matched, so a file inside the dataset (or a relative
dataset itself) was accepted at ac828f1. RB4-2: leading dots and spaces of a component are kept,
only trailing ones are folded (mutant N16).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.test_evidence_validator import (
    real_extract_args,
    real_shaped_pass,
)
from tests.windows_integration_readiness.test_v11_reviewer_b2_minors import (
    OUTSIDE_DATASET,
    _refused,
)

Rec = dict[str, Any]


def _args(flag: str | None = None, path: str | None = None, out: str | None = None) -> list[str]:
    args = real_extract_args(real_shaped_pass())
    if out is not None:
        args[args.index("--out") + 1] = out
    if flag is None or path is None:
        return args
    if flag == "--protected-utc-ranges":
        args.remove("--no-protected-ranges")
    if flag == "--anchor-out":
        args += ["--anchor-recorded-by", "cust-carol", "--anchor-storage-ref", "pr-comment"]
    if flag in args:
        args[args.index(flag) + 1] = path
    else:
        args += [flag, path]
    return args


def _code(args: list[str]) -> str:
    with pytest.raises(ec.ContractError) as e:
        ec.recorded_extract_args(args)
    return e.value.code


# ---- RB4-1: every compared path is fully qualified ----------------------------------------------
RELATIVE = [
    "\\nexora-runs\\run-0001\\dataset\\k.key",  # root-relative
    "/nexora-runs/run-0001/dataset/k.key",
    "D:dataset\\k.key",  # drive-relative
    "D:..\\run-0001\\dataset\\k.key",
    "dataset\\k.key",  # plain relative
    ".\\dataset\\k.key",
    "\\\\srv",  # UNC without a share
    "\\\\srv\\\\k.key",  # empty share segment
]


@pytest.mark.parametrize("flag", OUTSIDE_DATASET)
@pytest.mark.parametrize("path", RELATIVE)
def test_rb4_1_relative_input_or_custody_path_is_refused(flag: str, path: str) -> None:
    assert _code(_args(flag, path)) == f"{flag}_must_be_absolute"


@pytest.mark.parametrize(
    "out", ["dataset", ".\\dataset", "\\nexora-runs\\run-0001\\dataset", "D:dataset", "\\\\srv"]
)
def test_rb4_1_relative_out_is_refused_with_the_plan_code(out: str) -> None:
    assert _code(_args(out=out)) == "output_dir_must_be_absolute"


@pytest.mark.parametrize("out", ["dataset", "\\nexora-runs\\run-0001\\dataset", "D:dataset"])
def test_rb4_1_a_consistently_relative_dataset_ref_fails_the_record(out: str) -> None:
    def mutate(r: Rec) -> None:
        r["custody"]["dataset_ref"] = out
        argv = r["custody"]["verifier_argv"]
        argv[argv.index("--dataset") + 1] = out
        run = r["execution"]["commands_run"][0]
        run[run.index("--out") + 1] = out
        r["execution"]["commands_run"][1] = list(argv)

    _refused(mutate, "commands_run_extractor_invalid:0:output_dir_must_be_absolute")


@pytest.mark.parametrize(
    "path", ["D:\\keys\\op.key", "d:/keys/op.key", "\\\\srv\\share\\keys\\op.key"]
)
def test_rb4_1_fully_qualified_paths_are_accepted(path: str) -> None:
    assert ec.recorded_extract_args(_args("--operator-key", path))["--operator-key"] == path


# ---- RB4-2: only trailing dots and spaces are folded --------------------------------------------
@pytest.mark.parametrize(
    ("out", "key"),
    [
        ("D:\\runs\\.ds", "D:\\runs\\ds\\k.key"),
        ("D:\\runs\\ds", "D:\\runs\\.ds\\k.key"),
        ("D:\\runs\\ ds", "D:\\runs\\ds\\k.key"),
        ("D:\\runs\\ds", "D:\\runs\\ ds\\k.key"),
    ],
)
def test_rb4_2_a_leading_dot_or_space_names_another_folder(out: str, key: str) -> None:
    assert ec.recorded_extract_args(_args("--operator-key", key, out=out))["--out"] == out


@pytest.mark.parametrize(
    ("out", "key"),
    [
        ("D:\\runs\\.ds", "D:\\runs\\.ds\\k.key"),
        ("D:\\runs\\.ds", "D:\\runs\\.ds.\\k.key"),
        ("D:\\runs\\ ds", "D:\\runs\\ ds \\k.key"),
    ],
)
def test_rb4_2_a_leading_dot_dataset_still_contains_its_files(out: str, key: str) -> None:
    assert _code(_args("--operator-key", key, out=out)) == "--operator-key_inside_dataset"


# ---- I-b: dotless i upper-cases to I on NTFS; fold it so the check over-refuses ---------------
def test_rb4_ib_dotless_i_is_folded_like_capital_i() -> None:
    args = _args("--operator-key", "D:\\runs\\d\u0131t\\k.key", out="D:\\runs\\dIt")
    assert _code(args) == "--operator-key_inside_dataset"
