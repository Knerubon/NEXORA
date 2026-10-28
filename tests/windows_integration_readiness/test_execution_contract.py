"""Finding B-M3 regression: the real-half execution path needs no operator-written code.

Adversarial cases C1-C10 from the PR #95 remediation prompt, against the command contract read
from PR #96 at d6a7cda, executed with a mock provider only. Every unsupported operation fails
closed; every step without a reviewed interface is BLOCKED and the checklist says so.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.execution_contract import ContractError, plan_invocation
from tests.windows_integration_readiness.vectors import T0, TICKS_SYNTH, H

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "research"
CHECKLIST = DOCS / "market-history-windows-readiness" / "WINDOWS-EXECUTION-CHECKLIST.md"
SHA = "0123456789abcdef0123456789abcdef01234567"
SYMBOL = "SYN-PREREGISTERED"


def argv(out: Path, **override: str | None) -> list[str]:
    base: dict[str, str | None] = {
        "--symbol": SYMBOL,
        "--from-raw-msc": str(T0),
        "--to-raw-msc": str(T0 + 3 * H),
        "--chunk-ms": str(H),
        "--max-gap-ms": "60000",
        "--time-conversion": "tc.json",
        "--clock-attestation": "ca.json",
        "--no-protected-ranges": "",
        "--out": str(out),
    }
    base.update(override)
    out_argv: list[str] = []
    for k, v in base.items():
        if v is None:
            continue
        out_argv += [k] if k in ec.EXTRACT_VALUELESS else [k, v]
    return out_argv


def plan(out: Path, args: list[str] | None = None, **kw: Any) -> ec.Plan:
    params: dict[str, Any] = {
        "provider": "mock",
        "offline": True,
        "preregistered_symbol": SYMBOL,
        "extractor_sha": SHA,
        "expected_extractor_sha": SHA,
        "extractor_version": ec.EXTRACTOR_VERSION,
        "manifest_schema": ec.MANIFEST_SCHEMA,
        "repo_root": REPO,
    }
    params.update(kw)
    return plan_invocation(args if args is not None else argv(out), **params)


def _code(fn: Any) -> str:
    with pytest.raises(ContractError) as e:
        fn()
    return e.value.code


def test_valid_mock_plan_runs_and_is_synthetic(tmp_path: Path) -> None:
    p = plan(tmp_path / "out")
    r = ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="synthetic-operator")
    assert r.status == "COMPLETE" and r.chunk_files == [f"chunk-0000{i}.jsonl" for i in range(3)]
    assert r.manifest is not None and r.manifest["evidence_class"] == "SYNTHETIC_MOCK"
    assert ec.check_provenance(r.manifest) == []
    again = ec.run_mock(
        plan(tmp_path / "out2"), TICKS_SYNTH, extractor_sha=SHA, operator="synthetic-operator"
    )
    assert again.manifest is not None
    assert again.manifest["content_sha256"] == r.manifest["content_sha256"]


@pytest.mark.parametrize("flag", ec.EXTRACT_REQUIRED)
def test_c1_missing_required_argument(tmp_path: Path, flag: str) -> None:
    code = _code(lambda: plan(tmp_path / "o", argv(tmp_path / "o", **{flag: None})))
    assert code == f"missing_argument:{flag}"


def test_c1_missing_protected_range_decision_and_unknown_flag(tmp_path: Path) -> None:
    o = tmp_path / "o"
    assert (
        _code(lambda: plan(o, argv(o, **{"--no-protected-ranges": None})))
        == "protected_range_decision_required"
    )
    assert _code(lambda: plan(o, [*argv(o), "--mock-source", "x"])) == (
        "unknown_argument:--mock-source"
    )
    no_symbol = argv(o, **{"--symbol": None})
    assert _code(lambda: plan(o, [*no_symbol, "--symbol"])) == "missing_value:--symbol"


@pytest.mark.parametrize("cap", ["inject_source", "crash_hook", "expose_dtype_descr"])
def test_c2_capability_the_real_extractor_lacks_is_refused(tmp_path: Path, cap: str) -> None:
    code = _code(
        lambda: plan(
            tmp_path / "o", provider="mt5", offline=False, required_capabilities=frozenset({cap})
        )
    )
    assert code == f"capability_unsupported:{cap}"


def test_c2_unknown_provider(tmp_path: Path) -> None:
    assert _code(lambda: plan(tmp_path / "o", provider="dukascopy")) == (
        "unsupported_provider:dukascopy"
    )


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"extractor_sha": "f" * 40}, "extractor_sha_mismatch"),
        (
            {"extractor_version": "nexora.market-history-extractor/4"},
            "extractor_version_mismatch:nexora.market-history-extractor/4",
        ),
        (
            {"manifest_schema": "nexora.market-history/3"},
            "manifest_schema_mismatch:nexora.market-history/3",
        ),
    ],
)
def test_c3_wrong_extractor_or_schema_version(
    tmp_path: Path, kw: dict[str, str], code: str
) -> None:
    assert _code(lambda: plan(tmp_path / "o", None, **kw)) == code


def test_c4_invalid_output_directory(tmp_path: Path) -> None:
    rel = Path("relative-out")
    assert _code(lambda: plan(rel, argv(rel))) == "output_dir_must_be_absolute"
    nested = tmp_path / "missing-parent" / "out"
    assert _code(lambda: plan(nested)) == "output_dir_invalid"
    f = tmp_path / "a-file"
    f.write_bytes(b"")
    assert _code(lambda: plan(f)) == "output_dir_invalid"
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "l"
    link.symlink_to(target)
    assert _code(lambda: plan(link)) == "output_dir_invalid"
    inside = tmp_path / "repo" / "data"
    inside.parent.mkdir()
    assert _code(lambda: plan(inside, repo_root=tmp_path / "repo")) == "output_dir_inside_checkout"


def test_c5_existing_output_collision(tmp_path: Path) -> None:
    out = tmp_path / "o"
    out.mkdir()
    (out / "chunk-00000.jsonl").write_bytes(b"x")
    assert _code(lambda: plan(out)) == "output_dir_not_empty"


def test_c5_mock_never_overwrites_a_chunk(tmp_path: Path) -> None:
    p = plan(tmp_path / "o")
    p.out_dir.mkdir()
    (p.out_dir / "chunk-00000.jsonl").write_bytes(b"x")  # appears after planning
    with pytest.raises(FileExistsError):
        ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op")
    assert (p.out_dir / "chunk-00000.jsonl").read_bytes() == b"x"


def test_c6_partial_failure_writes_no_manifest_and_claims_nothing(tmp_path: Path) -> None:
    p = plan(tmp_path / "o")
    r = ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op", fail_on_chunk=1)
    assert r.status == "PARTIAL_FAILED" and r.failure == "source_failed_at_chunk:1"
    assert r.manifest is None and not (p.out_dir / "manifest.json").exists()
    assert r.chunk_files == ["chunk-00000.jsonl"]
    assert ec.check_provenance(r.manifest) == ["manifest_absent"]
    with pytest.raises(ContractError):
        ec.evidence_class_for(r.manifest, "SYNTHETIC_MOCK")


@pytest.mark.parametrize("missing", ec.PROVENANCE_FIELDS)
def test_c7_missing_provenance_metadata(tmp_path: Path, missing: str) -> None:
    r = ec.run_mock(plan(tmp_path / "o"), TICKS_SYNTH, extractor_sha=SHA, operator="op")
    assert r.manifest is not None
    m = dict(r.manifest)
    m[missing] = "" if missing != "window_raw_msc" else []
    assert ec.check_provenance(m) == [f"provenance_missing:{missing}"]
    del m[missing]
    assert _code(lambda: ec.evidence_class_for(m, "SYNTHETIC_MOCK")) == (
        f"provenance_missing:{missing}"
    )


def test_c8_real_terminal_refused_offline(tmp_path: Path) -> None:
    assert _code(lambda: plan(tmp_path / "o", provider="mt5")) == "real_terminal_forbidden_offline"
    p = plan(tmp_path / "o", provider="mt5", offline=False)  # a plan only; nothing is run
    assert _code(lambda: ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op")) == (
        "run_mock_requires_mock_provider"
    )


def test_c8_metatrader5_import_and_network_are_blocked_in_this_harness() -> None:
    with pytest.raises(BaseException) as e:
        __import__("MetaTrader5")
    assert type(e.value).__name__ == "RealTerminalImportForbidden"
    with pytest.raises(BaseException) as n:
        socket.create_connection(("127.0.0.1", 9))
    assert type(n.value).__name__ == "NetworkAccessForbidden"


@pytest.mark.parametrize("symbol", ["SYN-PREREGISTERED.a", "syn-preregistered", "SYN"])
def test_c9_symbol_must_equal_preregistration_verbatim(tmp_path: Path, symbol: str) -> None:
    o = tmp_path / "o"
    assert _code(lambda: plan(o, argv(o, **{"--symbol": symbol}))) == (
        "symbol_differs_from_preregistration"
    )


def test_c9_contract_carries_no_broker_symbol_table() -> None:
    src = Path(ec.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(EURUSD|GBPUSD|XAUUSD|USDJPY)\b", src)
    assert "symbol_map" not in src


def test_c10_mock_output_cannot_be_classified_as_real_evidence(tmp_path: Path) -> None:
    r = ec.run_mock(plan(tmp_path / "o"), TICKS_SYNTH, extractor_sha=SHA, operator="op")
    for requested in ("REAL_WINDOWS_RUN", "VERIFIED", "TRUSTED"):
        assert _code(lambda req=requested: ec.evidence_class_for(r.manifest, req)) == (
            "mock_output_cannot_be_real_evidence"
        )
    assert ec.evidence_class_for(r.manifest, "SYNTHETIC_MOCK") == "SYNTHETIC_MOCK"
    assert r.manifest is not None
    relabelled = {**r.manifest, "evidence_class": "REAL_WINDOWS_RUN"}
    assert _code(lambda: ec.evidence_class_for(relabelled, "REAL_WINDOWS_RUN")) == (
        "mock_source_relabelled"
    )
    forged = {**relabelled, "source": "terminal"}
    assert _code(lambda: ec.evidence_class_for(forged, "REAL_WINDOWS_RUN")) == (
        "unknown_evidence_class"
    )


def test_real_extractor_offers_no_harness_capability() -> None:
    assert ec.EXTRACTOR_CAPABILITIES == frozenset()


def test_every_real_half_step_is_existing_or_blocked_with_a_dependency() -> None:
    for step, (disposition, detail) in ec.REAL_HALF_STEPS.items():
        assert disposition in (ec.EXISTING, ec.BLOCKED), step
        if disposition == ec.BLOCKED:
            assert detail.startswith(("DEP-01", "DEP-03")), step
        else:
            assert "scripts/" in detail or "python -m tests." in detail, step


def test_checklist_lists_every_step_with_the_same_disposition() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    for step, (disposition, _) in ec.REAL_HALF_STEPS.items():
        row = re.search(rf"^\| `{re.escape(step)}` \| (\w+) \|", text, flags=re.M)
        assert row, step
        assert row.group(1) == disposition, step
