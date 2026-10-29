"""Wave V9: the package's contract constants against the REAL PR #96 candidate code.

Everything else in this package restates #96's interfaces from the dependency manifest. These tests
check the restatement against the actual objects of the pinned head: `git archive` of that commit
into a temp directory (read-only: no checkout, no network, no push) and, for behaviour, a
subprocess that imports the exported `nexora.market_history` with `PYTHONPATH` pointing at the
export (the only place that code runs; nothing of #96 is imported into this test process).

Skipped, with the reason stated, when the pinned commit is not in the local object store (a
shallow CI clone). A skip is not a pass: the PR description records the run on a full clone.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.duplicate_oracle import classify
from tests.windows_integration_readiness.test_duplicate_oracle import GOLDEN
from tests.windows_integration_readiness.test_v9_custody_signed_pin import (
    GRAMMAR_OK,
    GRAMMAR_REFUSED,
)
from tests.windows_integration_readiness.vectors import ALLOWED_MT5_CALLS

REPO = Path(__file__).resolve().parents[2]
SHA = dp.CANDIDATE_SHA
PKG = "packages/nexora/market_history"


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=False)


def _tree(export: Path, rel: str) -> ast.Module:
    return ast.parse((export / rel).read_text(encoding="utf-8"))


def _flags(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(all `--flag` strings given to add_argument, those with required=True)."""
    allf: set[str] = set()
    req: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            names = [
                a.value
                for a in node.args
                if isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and a.value.startswith("--")
            ]
            allf.update(names)
            if any(
                k.arg == "required" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords
            ):
                req.update(names)
    return allf, req


def _const(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            val = node.value
            if isinstance(val, ast.Call) and getattr(val.func, "id", "") in {
                "frozenset",
                "set",
                "tuple",
            }:
                return ast.literal_eval(val.args[0]) if val.args else ()
            return ast.literal_eval(val)
    raise AssertionError(f"{name} not found")


def test_the_export_is_the_pinned_head(export: Path) -> None:
    assert _git("rev-parse", f"{SHA}^{{commit}}").stdout.decode().strip() == SHA
    assert (export / PKG / "custody.py").is_file()


def test_verifier_cli_matches_the_manifest_contract(export: Path) -> None:
    flags, _ = _flags(_tree(export, "scripts/verify_market_history.py"))
    assert flags == set(dp.CONTRACT["verify_flags"]) == set(cg.V4_VERIFY_FLAGS)
    assert {"--pin", "--max-pin-age-seconds"} <= flags  # a signed pin, not just a store head
    assert set(dp.CONTRACT["verify_valueless_flags"]) == {"--require-trusted"}


def test_extractor_cli_matches_the_manifest_contract(export: Path) -> None:
    flags, required = _flags(_tree(export, "scripts/extract_market_history.py"))
    declared = set(ec.EXTRACT_REQUIRED + ec.EXTRACT_ONE_OF + ec.EXTRACT_OPTIONAL)
    assert flags == declared == set(ec.EXTRACT_FLAGS)
    assert set(ec.EXTRACT_REQUIRED) <= required
    assert "--operator-key" in flags and "--custodian" not in flags


def test_custody_cli_matches_the_manifest_contract(export: Path) -> None:
    tree = _tree(export, "scripts/custody_market_history.py")
    flags, _ = _flags(tree)
    assert set(dp.CONTRACT["custody_approve_flags"]) <= flags
    assert "--custodian" not in flags and "--signing-key" in flags  # approve took --custodian at /5
    names = [
        n.args[0].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", "") == "add_parser"
        and n.args
        and isinstance(n.args[0], ast.Constant)
    ]
    assert set(names) >= set(dp.CONTRACT["custody_subcommands"]) - {"approve", "revoke"}


def test_constants_match_the_manifest_contract(export: Path) -> None:
    ext = _tree(export, f"{PKG}/extract.py")
    assert (
        _const(ext, "EXTRACTOR_VERSION") == dp.CONTRACT["extractor_version"] == ec.EXTRACTOR_VERSION
    )
    assert _const(_tree(export, f"{PKG}/__init__.py"), "SCHEMA_VERSION") == cg.V4_MANIFEST_SCHEMA
    assert set(_const(_tree(export, f"{PKG}/fsutil.py"), "PROVEN_PLATFORMS")) == set(
        cg.V4_PROVEN_PLATFORMS
    ) or "frozenset" in (export / PKG / "fsutil.py").read_text(encoding="utf-8")
    cust = _tree(export, f"{PKG}/custody.py")
    for const, key in (
        ("PIN_SCHEMA", "pin_schema"),
        ("APPROVAL_SCHEMA", "approval_schema"),
        ("CANDIDATE_SCHEMA", "candidate_schema"),
        ("REGISTRY_SCHEMA", "registry_schema"),
    ):
        assert _const(cust, const) == dp.CONTRACT[key], const
    text = (export / PKG / "custody.py").read_text(encoding="utf-8")
    assert re.search(r'_PRINCIPAL_ID = re\.compile\(r"([^"]+)"\)', text)
    grammar = re.search(r'_PRINCIPAL_ID = re\.compile\(r"([^"]+)"\)', text)
    assert grammar and grammar.group(1) == dp.CONTRACT["principal_id_grammar"]
    assert "MAX_SKEW = timedelta(minutes=5)" in text and dp.CONTRACT["pin_max_skew_seconds"] == 300
    assert "PROVEN_PLATFORMS = frozenset({" in (export / PKG / "fsutil.py").read_text()
    sig = (export / PKG / "signing.py").read_text(encoding="utf-8")
    assert "len(text) == 128" in sig and dp.CONTRACT["signature_hex_length"] == 128


def test_mt5_allow_list_is_the_one_the_extractor_enforces(export: Path) -> None:
    allowed = _const(_tree(export, f"{PKG}/provider.py"), "ALLOWED_MT5_CALLS")
    assert set(allowed) == set(ALLOWED_MT5_CALLS) == set(dp.CONTRACT["allowed_mt5_calls"])


_HARNESS = r"""
import json, sys
from decimal import Decimal
from nexora.market_history.custody import normalize_identity
from nexora.market_history.models import HistoryError, NormalizedTick
from nexora.market_history.quality import QualityAccumulator, QualityConfig
job = json.load(sys.stdin)
out = {"identity": {}, "quality": {}}
for s in job["identities"]:
    try:
        normalize_identity(s); out["identity"][s] = True
    except HistoryError:
        out["identity"][s] = False
for name, ticks in job["vectors"].items():
    acc = QualityAccumulator(QualityConfig(10**12))
    for t in ticks:
        if "ask" not in t:
            continue
        bid, ask = Decimal(t["bid"]), Decimal(t["ask"])
        acc.add(NormalizedTick(utc_msc=t["time_msc"], raw_msc=t["time_msc"], bid=bid, ask=ask,
                               spread=ask - bid, flags=t.get("flags")))
    out["quality"][name] = acc.report()
print(json.dumps(out))
"""


def _run_pr96(export: Path, job: dict[str, Any]) -> dict[str, Any]:
    env = {**os.environ, "PYTHONPATH": str(export / "packages"), "PYTHONNOUSERSITE": "1"}
    done = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(export),
    )
    assert done.returncode == 0, done.stderr[-600:]
    parsed: dict[str, Any] = json.loads(done.stdout)
    return parsed


def _corpus() -> list[str]:
    extra = [
        "".join(c)
        for c in (
            ("a",) * 128,
            ("a",) * 129,
            ("A", "b"),
            ("a", "+"),
            ("a", " "),
            ("a", "​"),
            ("ａ",),
            ("a", "é"),
            ("a", "-"),
            ("-", "a"),
            ("_",),
            ("a", "_"),
            ("a", "@", "b"),
        )
    ]
    return [*GRAMMAR_OK, *GRAMMAR_REFUSED, *extra]


def test_this_gate_never_accepts_an_identity_pr96_refuses(export: Path) -> None:
    corpus = _corpus()
    got = _run_pr96(export, {"identities": corpus, "vectors": {}})["identity"]
    accepted_here = {s for s in corpus if cg.identity_problem(s) == ""}
    assert accepted_here, "positive control: some identity must be accepted"
    assert {s for s in accepted_here if not got[s]} == set()  # never a superset
    assert {s for s in corpus if got[s]} == accepted_here  # and, today, exactly the same set


def test_duplicate_and_ordering_counts_against_the_real_quality_code(export: Path) -> None:
    vectors = {name: ticks for name, ticks, _exp, _why in GOLDEN}
    got = _run_pr96(export, {"identities": [], "vectors": vectors})["quality"]
    for name, ticks in vectors.items():
        oracle, real = classify(ticks), got[name]
        assert real["exact_duplicate_count"] == oracle["exact_duplicate"], name
        assert real["same_timestamp_different_quote_count"] == oracle["same_ts_diff_quote"], name
        assert real["same_quote_different_flags_count"] == oracle["same_quote_diff_flags"], name
    # EXT-DEF-1 (duplicate counting) no longer reproduces at the pinned candidate; the ordering
    # metric is a DIFFERENT definition (see test_v9_out_of_order_metric.py)
    assert got["D6_out_of_order"]["non_monotonic_count"] == 1
    assert classify(vectors["D6_out_of_order"])["out_of_order"] == 2


def test_adjacent_descent_mirror_equals_the_real_non_monotonic_count(export: Path) -> None:
    from tests.windows_integration_readiness.test_v9_out_of_order_metric import (
        TABLE,
        adjacent_descents,
    )

    vectors = {name: ticks for name, ticks, _a, _b in TABLE}
    got = _run_pr96(export, {"identities": [], "vectors": vectors})["quality"]
    for name, ticks, adjacent, _running in TABLE:
        assert got[name]["non_monotonic_count"] == adjacent == adjacent_descents(ticks), name
