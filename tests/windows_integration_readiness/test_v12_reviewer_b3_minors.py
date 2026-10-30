r"""V12 regressions for Reviewer B round 3 on PR #103 (RB3-1, RB3-2). Synthetic only.

RB3-1: Win32 spellings of a path inside `--out` (trailing dot or space on a component, `\\?\`,
`\\.\` and `\??\` prefixes, an alternate data stream) were accepted at 70c1b49. RB3-2: the record-id
serial must be ASCII digits (a `\\d` mutant survived).
"""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.test_evidence_validator import (
    real_extract_args,
    real_shaped_pass,
)
from tests.windows_integration_readiness.test_v11_reviewer_b2_minors import (
    OUTSIDE_DATASET,
    _accepted,
    _extract_flag,
    _refused,
)

WIN_DATASET = "D:\\nexora-runs\\run-0001\\dataset"


def _code(flag: str, path: str) -> str:
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
    return e.value.code


# ---- RB3-1: trailing dots and spaces are folded the Win32 way ----------------------------------
@pytest.mark.parametrize("flag", OUTSIDE_DATASET)
@pytest.mark.parametrize(
    "path",
    [
        WIN_DATASET + ".\\k.key",
        WIN_DATASET + " \\k.key",
        WIN_DATASET + ". .\\k.key",
        "D:\\nexora-runs.\\run-0001 \\dataset\\k.key",
        "D:/nexora-runs/run-0001/dataset./k.key",
        WIN_DATASET + ".",
    ],
)
def test_rb3_1_trailing_dot_or_space_still_names_the_dataset(flag: str, path: str) -> None:
    assert _code(flag, path) == f"{flag}_inside_dataset"


# ---- RB3-1: spellings that cannot be compared offline are refused ------------------------------
NOT_PLAIN = [
    "\\\\?\\" + WIN_DATASET + "\\k.key",
    "\\\\.\\" + WIN_DATASET + "\\k.key",
    "\\??\\" + WIN_DATASET + "\\k.key",
    "//?/D:/nexora-runs/run-0001/dataset/k.key",
    "\\\\?\\UNC\\srv\\share\\k.key",
    "\\??\\UNC\\srv\\share\\k.key",  # NT prefix without a drive colon
    "\\\\?\\E:\\keys\\op-alice.key",  # even outside the dataset: not comparable offline
    WIN_DATASET + ":k.key",  # alternate data stream on the dataset folder
    "E:\\keys\\op.key:hidden",
    "D:\\nexora-runs\\...\\k.key",  # a component of dots only
]


@pytest.mark.parametrize("flag", OUTSIDE_DATASET)
@pytest.mark.parametrize("path", NOT_PLAIN)
def test_rb3_1_device_prefix_or_stream_is_refused(flag: str, path: str) -> None:
    assert _code(flag, path) == f"{flag}_path_not_plain"


@pytest.mark.parametrize("out", ["\\\\?\\" + WIN_DATASET, WIN_DATASET + ":s", "D:\\x\\. ."])
def test_rb3_1_out_itself_must_be_plain(out: str) -> None:
    args = real_extract_args(real_shaped_pass())
    args[args.index("--out") + 1] = out
    with pytest.raises(ec.ContractError) as e:
        ec.recorded_extract_args(args)
    assert e.value.code == "--out_path_not_plain"


def test_rb3_1_record_with_an_extended_length_key_path_fails() -> None:
    _refused(
        lambda r: _extract_flag(r, "--operator-key", "\\\\?\\" + WIN_DATASET + "\\op.key"),
        "commands_run_extractor_invalid:0:--operator-key_path_not_plain",
    )


@pytest.mark.parametrize(
    "path",
    [
        "D:\\nexora-runs\\run-0001\\dataset2\\ca.json",  # sibling sharing the prefix
        "D:\\nexora-runs\\run-0001\\data.set\\ca.json",  # an inner dot is not trailing
        "D:\\nexora-runs\\run-0001\\clock-attestation.json",
        "\\\\srv\\share\\run-0001\\ca.json",  # plain UNC
    ],
)
def test_rb3_1_plain_paths_outside_the_dataset_are_accepted(path: str) -> None:
    _accepted(lambda r: _extract_flag(r, "--clock-attestation", path))


# ---- RB3-2: the record-id serial is ASCII digits only ------------------------------------------
@pytest.mark.parametrize(
    "serial",
    [
        "\u0660\u0660\u0660\u0661",  # Arabic-Indic
        "\u06f0\u06f0\u06f0\u06f1",  # Extended Arabic-Indic
        "\u0966\u0966\u0966\u0967",  # Devanagari
        "\uff10\uff10\uff10\uff11",  # fullwidth
        "000\u0661",
    ],
)
def test_rb3_2_non_ascii_digits_in_the_record_id_serial_are_malformed(serial: str) -> None:
    _refused(
        lambda r: r["identity"].update(record_id=f"RUN-20260101-{serial}"),
        "real_record_id_malformed",
    )
