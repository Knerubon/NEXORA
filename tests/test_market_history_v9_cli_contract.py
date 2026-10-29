"""Wave V9 / N-03: the exit-code contract of `scripts/verify_market_history.py`.

At 1c536d5 the verifier exited 0 for a dataset that was cleanly verified but UNTRUSTED unless the
caller remembered `--require-trusted`; a CI step or shell `&&` chain would read that as approval.
The contract now (see the script's docstring and THREAT-MODEL-V4.md section 8):

    0 TRUSTED only; 1 UNTRUSTED (a verdict, never approval); 2 invalid input; 3 operational error.

These tests drive the real script as a subprocess (shell-level and CI-style) for every class, and
pin the safe default of the retained `--require-trusted` flag. Synthetic fixtures and per-run keys.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.extract import DatasetStatus

from tests.test_market_history_v4_custody import (
    CHECK,
    REGISTRY_JSON,
    ROOT,
    Env,
    approve,
    fresh_pin,
    key_file,
    make_env,
    run_cli,
)

SCRIPT = "verify_market_history.py"
TRUSTED, UNTRUSTED, INVALID, OPERATIONAL = 0, 1, 2, 3


class Fixture:
    """A dataset with a custodian approval, a registry file and a pin file written by the pin
    authority's CLI (real time), exactly as an operator would assemble them."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.env: Env = make_env(tmp)
        (tmp / "registry.json").write_text(json.dumps(REGISTRY_JSON))
        (tmp / "candidate.json").write_text(json.dumps(self.env.candidate))
        self.store = tmp / "store"
        self.registry = tmp / "registry.json"
        self.pin = tmp / "pin.json"

    def custody(self, *args: str) -> tuple[int, dict[str, Any]]:
        return run_cli("custody_market_history.py", *args)

    def approve(self) -> None:
        code, _ = self.custody(
            "approve", "--dataset", str(self.env.ds), "--store", str(self.store),
            "--registry", str(self.registry), "--candidate", str(self.tmp / "candidate.json"),
            "--signing-key", key_file(self.tmp, "carol"), "--evidence-ref", "ticket",
        )  # fmt: skip
        assert code == 0

    def make_pin(self) -> None:
        code, _ = self.custody(
            "pin", "--store", str(self.store), "--registry", str(self.registry), "--out",
            str(self.pin), "--signing-key", key_file(self.tmp, "pia"), "--initial",
        )  # fmt: skip
        assert code == 0

    def args(self, *, pin: bool = True, age: str | None = "3600") -> list[str]:
        out = ["--dataset", str(self.env.ds), "--store", str(self.store),
               "--registry", str(self.registry)]  # fmt: skip
        if pin:
            out += ["--pin", str(self.pin)]
        if age is not None:
            out += ["--max-pin-age-seconds", age]
        return out

    def verify(self, *extra: str, **kw: Any) -> tuple[int, dict[str, Any]]:
        return run_cli(SCRIPT, *self.args(**kw), *extra)


@pytest.fixture
def trusted(tmp_path: Path) -> Fixture:
    f = Fixture(tmp_path)
    f.approve()
    f.make_pin()
    return f


def _check_json(code: int, out: dict[str, Any]) -> None:
    """The JSON line repeats the exit code and can never claim trust for a non-zero exit."""
    assert out["exit_code"] == code
    assert out["trusted"] is (code == TRUSTED)
    assert out["exit_meaning"] == {0: "trusted", 1: "untrusted", 2: "invalid_input",
                                   3: "operational_error"}[code]  # fmt: skip
    assert (out["status"] == "TRUSTED") is (code == TRUSTED)


# --------------------------------------------------------------------------- exit 0: TRUSTED ---
def test_trusted_positive_control_is_the_only_exit_0(trusted: Fixture) -> None:
    code, out = trusted.verify()
    assert (code, out["status"], out["status_reasons"]) == (TRUSTED, "TRUSTED", [])
    _check_json(code, out)
    # the deprecated flag changes nothing for a trusted dataset
    assert trusted.verify("--require-trusted")[0] == TRUSTED


# ------------------------------------------------------------------------- exit 1: UNTRUSTED ---
def test_cleanly_verified_dataset_without_custody_is_exit_1_not_0(tmp_path: Path) -> None:
    """THE N-03 regression: previously exit 0 without --require-trusted."""
    env = make_env(tmp_path)
    code, out = run_cli(SCRIPT, "--dataset", str(env.ds))
    assert code == UNTRUSTED and out["status"] == "UNTRUSTED"
    assert "no_independent_anchor" in out["status_reasons"]
    _check_json(code, out)
    assert not any(r.startswith("integrity:") for r in out["status_reasons"])  # it DID verify


def test_require_trusted_flag_is_a_noop_and_cannot_change_an_untrusted_exit(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path)
    plain = run_cli(SCRIPT, "--dataset", str(env.ds))
    flagged = run_cli(SCRIPT, "--dataset", str(env.ds), "--require-trusted")
    assert plain[0] == flagged[0] == UNTRUSTED
    assert plain[1] == flagged[1]


@pytest.mark.parametrize(
    "case", ["no_pin", "no_max_age", "stale_pin", "revoked", "tampered_chunk", "store_head_wrong"]
)
def test_every_untrusted_cause_exits_1_never_0(trusted: Fixture, case: str) -> None:
    kw: dict[str, Any] = {}
    extra: list[str] = []
    if case == "no_pin":
        kw["pin"] = False
    elif case == "no_max_age":
        kw["age"] = None
    elif case == "stale_pin":
        kw["age"] = "1"  # the pin was captured at least a moment ago; 1 s after, it is stale
        import time

        time.sleep(1.2)
    elif case == "revoked":
        code, rev = trusted.custody(
            "revoke", "--dataset", str(trusted.env.ds), "--store", str(trusted.store),
            "--registry", str(trusted.registry), "--signing-key", key_file(trusted.tmp, "carol"),
            "--evidence-ref", "incident",
        )  # fmt: skip
        assert code == 0
        assert rev["repin_required"] is True and rev["repin_covers_seq"] == rev["seq"]  # R-1
    elif case == "tampered_chunk":
        chunk = sorted(trusted.env.ds.glob("chunk-*.jsonl"))[0]
        chunk.chmod(0o644)
        chunk.write_bytes(chunk.read_bytes() + b" ")
    else:
        extra = ["--store-head", f"1:{'0' * 64}"]
    code, out = trusted.verify(*extra, **kw)
    assert code == UNTRUSTED, out
    _check_json(code, out)
    assert out["status_reasons"], "an UNTRUSTED verdict always says why"


def test_missing_dataset_directory_is_a_verdict_exit_1(tmp_path: Path) -> None:
    code, out = run_cli(SCRIPT, "--dataset", str(tmp_path / "does-not-exist"))
    assert code == UNTRUSTED and "integrity:manifest_missing" in out["status_reasons"]
    _check_json(code, out)


# ------------------------------------------------------------------- exit 2: INVALID INPUT ---
def test_invalid_inputs_exit_2(trusted: Fixture) -> None:
    cases: dict[str, list[str]] = {
        "unknown_flag": ["--no-such-flag"],
        "bad_store_head": ["--store-head", "garbage"],
        "bad_max_age": ["--max-pin-age-seconds", "0"],
    }
    for name, extra in cases.items():
        code, out = trusted.verify(*extra)
        assert code == INVALID, (name, out)
        assert out["trusted"] is False and out["exit_code"] == INVALID, name

    missing_pin = trusted.args()
    missing_pin[missing_pin.index("--pin") + 1] = str(trusted.tmp / "absent-pin.json")
    code, out = run_cli(SCRIPT, *missing_pin)
    assert code == INVALID and out["status_reasons"] == ["file_unreadable"]

    trusted.pin.write_text("{not json")
    code, out = trusted.verify()
    assert code == INVALID and out["status_reasons"] == ["pin_unreadable"]


def test_malformed_missing_and_misplaced_registry_exit_2(trusted: Fixture) -> None:
    trusted.registry.write_text("{not json")
    assert trusted.verify()[0] == INVALID
    trusted.registry.write_text(json.dumps({"schema": "wrong", "principals": []}))
    code, out = trusted.verify()
    assert code == INVALID and out["status_reasons"] == ["registry_malformed"]
    trusted.registry.unlink()
    assert trusted.verify()[0] == INVALID
    inside = trusted.env.ds / "registry.json"
    inside.write_text(json.dumps(REGISTRY_JSON))
    args = trusted.args()
    args[args.index("--registry") + 1] = str(inside)
    code, out = run_cli(SCRIPT, *args)
    assert code == INVALID and out["status_reasons"] == ["registry_inside_dataset"]


def test_store_without_registry_is_invalid_input(trusted: Fixture) -> None:
    code, out = run_cli(SCRIPT, "--dataset", str(trusted.env.ds), "--store", str(trusted.store))
    assert code == INVALID and out["status_reasons"] == ["store_and_registry_go_together"]


# ------------------------------------------------------------------ exit 3: OPERATIONAL ERROR ---
def _load_cli() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_cli_under_test", ROOT / "scripts" / SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_internal_errors_are_exit_3_with_json_never_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    env = make_env(tmp_path)
    monkeypatch.setattr(sys, "argv", ["v", "--dataset", str(env.ds)])

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "dataset_status", boom)
    assert cli.main() == OPERATIONAL
    out = json.loads(capsys.readouterr().out)
    assert out["status_reasons"] == ["cli_internal_error:RuntimeError"] and out["trusted"] is False

    # a verdict that itself reports an internal error is OPERATIONAL, not a plain UNTRUSTED
    monkeypatch.setattr(
        cli,
        "dataset_status",
        lambda *a, **k: DatasetStatus("UNTRUSTED", ("status_internal_error:X",)),
    )
    assert cli.main() == OPERATIONAL
    _check_json(OPERATIONAL, json.loads(capsys.readouterr().out))


def test_exit_0_requires_every_input_even_if_a_verdict_claims_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defence in depth: a (buggy) TRUSTED verdict without store/registry/pin/age is not exit 0."""
    cli = _load_cli()
    env = make_env(tmp_path)
    monkeypatch.setattr(sys, "argv", ["v", "--dataset", str(env.ds)])
    monkeypatch.setattr(cli, "dataset_status", lambda *a, **k: DatasetStatus("TRUSTED", ()))
    assert cli.main() == UNTRUSTED
    capsys.readouterr()


# ------------------------------------------------------------- shell-level / CI-style gates ---
def _sh(script: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "packages"), str(ROOT)])}
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, cwd=ROOT, check=False
    )


def _verify_cmd(args: list[str]) -> str:
    quoted = " ".join(f"'{a}'" for a in args)
    return f"'{sys.executable}' '{ROOT / 'scripts' / SCRIPT}' {quoted}"


def test_shell_and_ci_gates_approve_only_the_trusted_dataset(
    trusted: Fixture, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`cmd && echo APPROVED || echo REJECTED`, `set -e` and `if` gates, as a CI step would."""
    good = _verify_cmd(trusted.args())
    assert _sh(f"{good} >/dev/null && echo APPROVED || echo REJECTED").stdout.strip() == "APPROVED"
    assert _sh(f"set -e; {good} >/dev/null; echo REACHED").stdout.strip() == "REACHED"

    plain = make_env(tmp_path_factory.mktemp("plain"))  # cleanly verified, no custody
    bad = _verify_cmd(["--dataset", str(plain.ds)])
    out = _sh(f"{bad} >/dev/null && echo APPROVED || echo REJECTED").stdout.strip()
    assert out == "REJECTED"
    assert _sh(f"set -e; {bad} >/dev/null; echo REACHED").stdout.strip() == ""  # set -e stopped it
    assert _sh(f"{bad} >/dev/null; echo $?").stdout.strip() == "1"
    garbage = _verify_cmd(["--dataset", str(plain.ds), "--pin", "/nonexistent/pin.json"])
    assert _sh(f"{garbage} >/dev/null; echo $?").stdout.strip() == "2"
    # the same gate with the deprecated flag gives the same answers
    assert _sh(f"{bad} --require-trusted >/dev/null; echo $?").stdout.strip() == "1"
    assert _sh(f"{good} --require-trusted >/dev/null; echo $?").stdout.strip() == "0"


def test_the_only_exit_0_in_the_whole_matrix_is_the_trusted_dataset(
    trusted: Fixture, tmp_path_factory: pytest.TempPathFactory
) -> None:
    plain = make_env(tmp_path_factory.mktemp("matrix"))
    runs = [
        trusted.verify()[0],
        run_cli(SCRIPT, "--dataset", str(plain.ds))[0],
        run_cli(SCRIPT, "--dataset", str(plain.ds), "--require-trusted")[0],
        run_cli(SCRIPT, "--dataset", str(plain.ds), "--pin", "/nonexistent")[0],
        trusted.verify("--store-head", "x")[0],
        trusted.verify(age=None)[0],
    ]
    assert runs == [TRUSTED, UNTRUSTED, UNTRUSTED, INVALID, INVALID, UNTRUSTED]
    assert fresh_pin is not None and approve is not None and CHECK is not None  # imports used
