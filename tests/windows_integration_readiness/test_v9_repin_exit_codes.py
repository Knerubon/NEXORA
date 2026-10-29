"""Wave V9 phase 2: the exit-code contract of the pinned candidate (PR #99 head).

Gate and validator treat exit 1/2/3 as not-TRUSTED, never read exit 0 as approval by itself, and
reject anything outside 0-3. The live tests execute the candidate's own verifier CLI from a
`git archive` of the pinned head (explicit skip when the commit is absent)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness.test_evidence_validator import check, synthetic
from tests.windows_integration_readiness.test_v4_custody_compat import _approved_baseline, good

_HEX = "a" * 64
SEC_MAX = 10 * 366 * 86400
AGES = ["1", "3600", str(SEC_MAX), str(SEC_MAX + 1), "99999999999999", "0", "-1", "1\n", "٣", "５"]
HEADS = [
    f"7:{_HEX}",
    f"7:{_HEX}\n",
    f"0:{_HEX}",
    f"7:{_HEX.upper()}",
    f"7:{_HEX[:-1]}",
    f"٣:{_HEX}",
]


REPO = Path(__file__).resolve().parents[2]


def test_manifest_records_the_exit_code_contract_and_the_prior_reference() -> None:
    assert dp.CONTRACT["verify_exit_codes"] == {
        "0": "trusted",
        "1": "untrusted",
        "2": "invalid_input",
        "3": "operational_error",
    }
    assert cg.V4_VERIFY_EXIT_CODES == frozenset({0, 1, 2, 3})
    assert dp.CONTRACT["verify_deprecated_noop_flags"] == ["--require-trusted"]
    prior = "1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8"
    assert dp.classify_head(prior) == dp.STALE  # recorded, and refused as a head
    assert dp.classify_head(prior[:7]) == dp.ABBREVIATED
    assert dp.classify_head(dp.CANDIDATE_SHA) == dp.PINNED
    assert dp.CANDIDATE_SHA == "d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d"
    assert not dp.head_is_gate_approved(dp.CANDIDATE_SHA)
    assert dp.MANIFEST["pr96_candidate"]["gate_approved_baseline_sha"] is None


@pytest.mark.parametrize("code", [1, 2, 3])
def test_nonzero_exits_are_never_trusted(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(good(verifier_exit_code=code))
    assert d.outcome != cg.RECORDABLE
    assert "verifier_did_not_report_trusted" in d.reasons
    assert "verifier_exit_code_outside_contract" not in d.reasons


@pytest.mark.parametrize("code", [4, -1, 255, 1000])
def test_exit_codes_outside_the_contract_are_blocked(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    _approved_baseline(monkeypatch)
    assert (
        "verifier_exit_code_outside_contract"
        in cg.trust_gate(good(verifier_exit_code=code)).reasons
    )


def test_exit_zero_without_trusted_status_is_not_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old head exited 0 for a cleanly verified but UNTRUSTED dataset."""
    _approved_baseline(monkeypatch)
    for status in ("UNTRUSTED", "UNVERIFIED"):
        d = cg.trust_gate(good(verifier_status=status, verifier_exit_code=0))
        assert d.outcome != cg.RECORDABLE
        assert "verifier_exit_zero_without_trusted_status" in d.reasons


def test_exit_zero_with_trusted_is_the_only_clean_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(good())
    assert d.outcome == cg.RECORDABLE and d.reasons == ()


def test_validator_rejects_exit_zero_with_untrusted_status() -> None:
    r = synthetic()
    r["custody"]["verifier_status"] = "UNTRUSTED"
    r["custody"]["verifier_exit_code"] = 0
    assert "verifier_exit_zero_without_trusted_status" in check(r).errors


@pytest.mark.parametrize("bad", [4, -1, "0", True, None])
def test_validator_rejects_exit_codes_outside_0_to_3(bad: Any) -> None:
    r = synthetic()
    r["custody"]["verifier_exit_code"] = bad
    assert "invalid_value:custody.verifier_exit_code:verify_exit_code" in check(r).errors


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_validator_accepts_each_contract_exit_code_as_a_value(code: int) -> None:
    r = synthetic()
    r["custody"]["verifier_exit_code"] = code
    assert "invalid_value:custody.verifier_exit_code:verify_exit_code" not in check(r).errors


@pytest.mark.parametrize("age", [str(SEC_MAX + 1), "9" * 19, "0", "1\n"])
def test_gate_argv_refuses_pin_ages_the_candidate_refuses_without_the_export(age: str) -> None:
    assert (
        "verifier_argv_max_pin_age_malformed"
        in cg.parse_verify_argv(_argv("--max-pin-age-seconds", age)).problems
    )
    assert (
        "verifier_argv_max_pin_age_malformed"
        not in cg.parse_verify_argv(_argv("--max-pin-age-seconds", str(SEC_MAX))).problems
    )


@pytest.mark.parametrize("head", [f"7:{'a' * 64}\n", f"{'9' * 19}:{'a' * 64}", f"0:{'a' * 64}"])
def test_gate_argv_refuses_store_heads_the_candidate_refuses_without_the_export(head: str) -> None:
    assert (
        "verifier_argv_store_head_malformed"
        in cg.parse_verify_argv(_argv("--store-head", head)).problems
    )


def test_schema_bounds_the_exit_code() -> None:
    schema = json.loads(
        (dp.MANIFEST_PATH.parent / "evidence-record.schema.json").read_text(encoding="utf-8")
    )
    prop = json.dumps(schema)
    assert '"minimum": 0, "maximum": 3' in prop.replace("\n", " ") or '"maximum": 3' in prop


# ---- executed against the real export ---------------------------------------------------------


def _verify(export: Path, *args: str) -> tuple[int, dict[str, Any]]:
    env = {**os.environ, "PYTHONPATH": str(export / "packages"), "PYTHONNOUSERSITE": "1"}
    done = subprocess.run(
        [sys.executable, str(export / "scripts" / "verify_market_history.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(export),
    )
    line = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "{}"
    return done.returncode, json.loads(line)


def test_real_verifier_never_exits_zero_for_missing_or_hostile_input(
    export: Path,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    cases = [
        ("--dataset", str(tmp_path / "missing"), "--require-trusted"),
        ("--dataset", str(empty)),
        ("--dataset", str(empty), "--require-trusted"),
        ("--dataset", str(empty), "--max-pin-age-seconds", "0"),
        ("--dataset", str(empty), "--max-pin-age-seconds", "99999999999999"),
        ("--dataset", str(empty), "--store", str(tmp_path / "s")),
        (
            "--dataset",
            str(empty),
            "--pin",
            str(tmp_path / "nope.json"),
            "--max-pin-age-seconds",
            "5",
        ),
    ]
    for args in cases:
        code, verdict = _verify(export, *args)
        assert code in (1, 2, 3), (args, code, verdict)
        assert verdict["trusted"] is False and verdict["exit_code"] == code, args
        assert verdict["exit_meaning"] in dp.CONTRACT["verify_exit_codes"].values()
        assert code in cg.V4_VERIFY_EXIT_CODES


def test_real_verifier_invalid_input_is_exit_2_with_a_typed_reason(
    export: Path,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    code, verdict = _verify(export, "--dataset", str(empty), "--max-pin-age-seconds", "0")
    assert code == 2
    assert verdict["status_reasons"] and not any(
        r.endswith("_internal_error") for r in verdict["status_reasons"]
    )


def test_candidate_declares_the_contract_the_manifest_records(export: Path) -> None:
    text = (export / "scripts" / "verify_market_history.py").read_text(encoding="utf-8")
    for needle in (
        "EXIT_TRUSTED = 0",
        "EXIT_UNTRUSTED = 1",
        "EXIT_INVALID_INPUT = 2",
        "EXIT_OPERATIONAL = 3",
    ):
        assert needle in text
    assert "deprecated no-op" in text
    custody = (export / "scripts" / "custody_market_history.py").read_text(encoding="utf-8")
    assert "repin_required" in custody and "repin_covers_seq" in custody
    jsonio = (export / "packages/nexora/market_history/jsonio.py").read_text(encoding="utf-8")
    assert f"MAX_DEPTH = {dp.CONTRACT['json_max_depth']}" in jsonio.replace(
        "MAX_JSON_DEPTH", "MAX_DEPTH"
    )
    assert "MAX_AGE_SECONDS_SANITY = 10 * 366 * 86400" in jsonio
    assert dp.CONTRACT["max_pin_age_seconds_sanity_max"] == 10 * 366 * 86400


# ---- never accept what the candidate refuses (found at re-pin) ----------------------------------


def _argv(flag: str, value: str) -> tuple[str, ...]:
    base = list(good().verify_argv)
    base[base.index(flag) + 1] = value
    return tuple(base)


def test_gate_max_pin_age_never_accepts_what_the_candidate_refuses(
    export: Path, tmp_path: Path
) -> None:
    empty = tmp_path / "e"
    empty.mkdir()
    for age in AGES:
        code, _v = _verify(export, "--dataset", str(empty), "--max-pin-age-seconds", age)
        candidate_refuses = code == 2 and age in {
            "0",
            "-1",
            "1\n",
            "٣",
            "５",
            str(SEC_MAX + 1),
            "99999999999999",
        }
        problems = cg.parse_verify_argv(_argv("--max-pin-age-seconds", age)).problems
        gate_accepts = "verifier_argv_max_pin_age_malformed" not in problems
        if candidate_refuses:
            assert not gate_accepts, age
    assert cg.parse_verify_argv(_argv("--max-pin-age-seconds", "3600")).problems == ()  # control


def test_gate_store_head_never_accepts_what_the_candidate_refuses(export: Path) -> None:  # noqa: ARG001
    for head in HEADS:
        problems = cg.parse_verify_argv(_argv("--store-head", head)).problems
        ok = "verifier_argv_store_head_malformed" not in problems
        # the candidate fullmatches `[0-9]{1,18}:[0-9a-f]{64}` (ASCII) with seq >= 1
        import re

        candidate_ok = (
            bool(re.fullmatch(r"([0-9]{1,18}):([0-9a-f]{64})", head, re.ASCII))
            and int(head.split(":")[0]) >= 1
            if head[0].isascii()
            else False
        )
        if ok:
            assert candidate_ok, head
    assert (
        "verifier_argv_store_head_malformed"
        not in cg.parse_verify_argv(_argv("--store-head", f"7:{_HEX}")).problems
    )


def test_validator_max_pin_age_is_bounded_like_the_candidate() -> None:
    r = synthetic()
    r["custody"]["max_pin_age_seconds"] = SEC_MAX + 1
    assert check(r).errors


@pytest.mark.parametrize(
    ("section", "field", "good_value"),
    [
        ("custody", "pin_id", "11" * 16),
        ("custody", "approval_id", "22" * 16),
        ("custody", "pin_pinned_by", "synthetic-pin-authority"),
        ("custody", "store_head", f"7:{'a' * 64}"),
        ("custody", "pin_signature", "5" * 128),
    ],
)
def test_validator_does_not_accept_a_trailing_newline(
    section: str, field: str, good_value: str
) -> None:
    r = synthetic()
    r[section][field] = good_value
    assert not any(field in e and "invalid" in e for e in check(r).errors)  # control: well formed
    r[section][field] = good_value + "\n"
    assert any(field in e for e in check(r).errors), field
