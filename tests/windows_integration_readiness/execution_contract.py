"""Reviewed execution path for the real half (finding B-M3): command contract plus a mock-only
harness, and an explicit BLOCKED register for every step no reviewed interface supports.

Facts this module is built on (read at PR #96 `d6a7cda72a88600e2ccf6554605f1c50478893ea`; re-verify
at the running SHA, checklist A3):
  * `scripts/extract_market_history.py` constructs `Mt5ReadOnlyHistorySource()` itself. It has no
    source/module injection, no mock-source flag and no crash hook, and it does not expose the
    terminal's `dtype.descr`.
  * Therefore an operator can run the extractor ONLY against the real terminal. Any matrix step that
    needs a mock source, a fault hook, raw dtype access, vector replay through the extractor's
    internals, or a recording proxy around the real module would need operator-written code.
    Those steps are BLOCKED on DEP-03 (a reviewed driver or injection points in the extractor
    stack), not worked around here.

`plan_invocation` validates a future extractor command line against that contract, and
`run_mock` executes the same contract against an in-memory synthetic source only. Its output is
stamped SYNTHETIC_MOCK and can never be promoted to real evidence. Nothing here imports
`nexora.market_history` or `MetaTrader5`, opens a socket or starts a process.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.windows_integration_readiness.reference_model import chunk_windows, encode_tick

CONTRACT_REF_SHA = "d6a7cda72a88600e2ccf6554605f1c50478893ea"
EXTRACTOR_VERSION = "nexora.market-history-extractor/5"
MANIFEST_SCHEMA = "nexora.market-history/4"
EXTRACT_SCRIPT = "scripts/extract_market_history.py"

# argparse of scripts/extract_market_history.py as read at CONTRACT_REF_SHA.
EXTRACT_REQUIRED = (
    "--symbol",
    "--from-raw-msc",
    "--to-raw-msc",
    "--chunk-ms",
    "--max-gap-ms",
    "--time-conversion",
    "--clock-attestation",
    "--out",
)
EXTRACT_ONE_OF = ("--protected-utc-ranges", "--no-protected-ranges")
EXTRACT_OPTIONAL = (
    "--max-chunk-ticks",
    "--protected-margin-ms",
    "--operator",
    "--candidate-out",
    "--anchor-out",
    "--anchor-recorded-by",
    "--anchor-storage-ref",
)
EXTRACT_VALUELESS = frozenset({"--no-protected-ranges"})
EXTRACT_FLAGS = frozenset(EXTRACT_REQUIRED + EXTRACT_ONE_OF + EXTRACT_OPTIONAL)

# What the current extractor CLI offers to a harness. Empty: see module docstring.
EXTRACTOR_CAPABILITIES: frozenset[str] = frozenset()
MOCK_CAPABILITIES = frozenset({"inject_source", "fail_on_chunk", "deterministic_bytes"})

MOCK_EVIDENCE_CLASS = "SYNTHETIC_MOCK"
PROVENANCE_FIELDS = (
    "evidence_class",
    "extractor_sha",
    "extractor_version",
    "manifest_schema",
    "source",
    "symbol",
    "window_raw_msc",
    "operator",
)

EXISTING = "EXISTING_COMMAND"
BLOCKED = "BLOCKED"

# Every real-half matrix step: either an existing, reviewed command, or BLOCKED with the missing
# capability. The checklist must list exactly these ids under the same disposition (test).
REAL_HALF_STEPS: dict[str, tuple[str, str]] = {
    "case1.step1-3.dtype_and_layout": (BLOCKED, "DEP-03: no reviewed way to read dtype.descr"),
    "case1.step4.empty_window": (BLOCKED, "DEP-03: extractor CLI does not report raw rows"),
    "case2.step1-2.vector_replay": (
        BLOCKED,
        "DEP-03: no entry point feeds vectors to the extractor",
    ),
    "case2.step3.quality_counts": (EXISTING, f"{EXTRACT_SCRIPT}; phase C, manifest quality"),
    "case3.step2-3.preflight_refusals": (EXISTING, f"{EXTRACT_SCRIPT}; phase C, preflight first"),
    "case4.step2.derived_fold_ranges": (BLOCKED, "DEP-03: no CLI prints derived fold ranges"),
    "case4.step3.preflight_refusals": (EXISTING, f"{EXTRACT_SCRIPT}; phase C, preflight first"),
    "case5.step1-3.mock_source_runs": (BLOCKED, "DEP-03: extractor has no mock-source injection"),
    "case5.step4.idempotent_rerun": (EXISTING, f"{EXTRACT_SCRIPT}; phase C, same --out"),
    "case6.step1.filesystem_probes": (
        EXISTING,
        "python -m tests.windows_integration_readiness.filesystem_probes",
    ),
    "case6.step3.reparse_out_path": (EXISTING, f"{EXTRACT_SCRIPT}; phase C, --out <junction>"),
    "case6.step4.crash_hook": (BLOCKED, "DEP-03: extractor has no reviewed fault hook"),
    "case6.step5.second_writer": (EXISTING, f"{EXTRACT_SCRIPT}; phase C, twice on one --out"),
    "case7.step1.recording_proxy_real_module": (
        BLOCKED,
        "DEP-03: wrapping the real module needs operator-written code",
    ),
    "case7.step2.shutdown_paths": (BLOCKED, "DEP-03: no reviewed fault injection"),
    "case7.step3-4.custody_and_verify": (
        BLOCKED,
        "DEP-01: TRUSTED unreachable (V4-CUSTODY-COMPATIBILITY.md)",
    ),
}


class ContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Plan:
    argv: tuple[str, ...]
    out_dir: Path
    symbol: str
    window_raw_msc: tuple[int, int]
    chunk_ms: int
    provider: str


@dataclass
class MockResult:
    status: str  # COMPLETE | PARTIAL_FAILED
    chunk_files: list[str] = field(default_factory=list)
    manifest: dict[str, Any] | None = None
    failure: str | None = None


def _parse(argv: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag not in EXTRACT_FLAGS:
            raise ContractError(f"unknown_argument:{flag}")
        if flag in out:
            raise ContractError(f"duplicate_argument:{flag}")
        if flag in EXTRACT_VALUELESS:
            out[flag] = ""
            i += 1
            continue
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            raise ContractError(f"missing_value:{flag}")
        out[flag] = argv[i + 1]
        i += 2
    return out


def plan_invocation(
    argv: Sequence[str],
    *,
    provider: str,
    offline: bool,
    preregistered_symbol: str,
    extractor_sha: str,
    expected_extractor_sha: str,
    extractor_version: str,
    manifest_schema: str,
    required_capabilities: frozenset[str] = frozenset(),
    repo_root: Path | None = None,
) -> Plan:
    """Fail-closed check of one extractor invocation. Raises ContractError(code)."""
    if provider not in ("mock", "mt5"):
        raise ContractError(f"unsupported_provider:{provider}")
    if offline and provider != "mock":
        raise ContractError("real_terminal_forbidden_offline")  # C8
    if extractor_sha != expected_extractor_sha:
        raise ContractError("extractor_sha_mismatch")  # C3
    if extractor_version != EXTRACTOR_VERSION:
        raise ContractError(f"extractor_version_mismatch:{extractor_version}")  # C3
    if manifest_schema != MANIFEST_SCHEMA:
        raise ContractError(f"manifest_schema_mismatch:{manifest_schema}")  # C3
    offered = MOCK_CAPABILITIES if provider == "mock" else EXTRACTOR_CAPABILITIES
    missing = sorted(required_capabilities - offered)
    if missing:
        raise ContractError(f"capability_unsupported:{','.join(missing)}")  # C2
    args = _parse(argv)  # C1
    for flag in EXTRACT_REQUIRED:
        if flag not in args:
            raise ContractError(f"missing_argument:{flag}")
    if sum(f in args for f in EXTRACT_ONE_OF) != 1:
        raise ContractError("protected_range_decision_required")
    if args["--symbol"] != preregistered_symbol:
        raise ContractError("symbol_differs_from_preregistration")  # C9: verbatim, no mapping
    try:
        lo, hi = int(args["--from-raw-msc"]), int(args["--to-raw-msc"])
        chunk = int(args["--chunk-ms"])
    except ValueError:
        raise ContractError("non_integer_argument") from None
    if hi <= lo or chunk <= 0:
        raise ContractError("invalid_window_or_chunk")
    out = Path(args["--out"])
    if not out.is_absolute():
        raise ContractError("output_dir_must_be_absolute")  # C4
    if not out.parent.is_dir() or out.is_symlink() or (out.exists() and not out.is_dir()):
        raise ContractError("output_dir_invalid")  # C4
    if repo_root is not None and (out == repo_root or repo_root in out.parents):
        raise ContractError("output_dir_inside_checkout")  # C4: raw ticks never in Git
    if out.exists() and any(out.iterdir()):
        raise ContractError("output_dir_not_empty")  # C5
    return Plan(tuple(argv), out, args["--symbol"], (lo, hi), chunk, provider)


def run_mock(
    plan: Plan,
    ticks: Sequence[tuple[int, str, str, int]],
    *,
    extractor_sha: str,
    operator: str,
    fail_on_chunk: int | None = None,
) -> MockResult:
    """Execute the contract against the in-memory synthetic source only."""
    if plan.provider != "mock":
        raise ContractError("run_mock_requires_mock_provider")
    plan.out_dir.mkdir(exist_ok=True)
    result = MockResult(status="COMPLETE")
    lo, hi = plan.window_raw_msc
    hashes = hashlib.sha256()
    for i, (a, b) in enumerate(chunk_windows(lo, hi, plan.chunk_ms)):
        if i == fail_on_chunk:
            # C6: stop, keep what was written, write NO manifest, claim nothing
            result.status, result.failure = "PARTIAL_FAILED", f"source_failed_at_chunk:{i}"
            return result
        body = b"".join(encode_tick(t) for t in ticks if a <= t[0] < b)
        name = f"chunk-{i:05d}.jsonl"
        fd = os.open(plan.out_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        hashes.update(body)
        result.chunk_files.append(name)
    result.manifest = {
        "evidence_class": MOCK_EVIDENCE_CLASS,
        "extractor_sha": extractor_sha,
        "extractor_version": EXTRACTOR_VERSION,
        "manifest_schema": MANIFEST_SCHEMA,
        "source": "synthetic in-memory mock (tests/windows_integration_readiness)",
        "symbol": plan.symbol,
        "window_raw_msc": [lo, hi],
        "operator": operator,
        "content_sha256": hashes.hexdigest(),
    }
    (plan.out_dir / "manifest.json").write_text(
        json.dumps(result.manifest, sort_keys=True), encoding="utf-8"
    )
    return result


def check_provenance(manifest: Mapping[str, Any] | None) -> list[str]:
    """C7: every provenance field present and non-blank."""
    if manifest is None:
        return ["manifest_absent"]
    return [
        f"provenance_missing:{k}"
        for k in PROVENANCE_FIELDS
        if k not in manifest or manifest[k] in (None, "", [])
    ]


def evidence_class_for(manifest: Mapping[str, Any] | None, requested: str) -> str:
    """C10: mock output can only ever be SYNTHETIC_MOCK evidence."""
    problems = check_provenance(manifest)
    if problems:
        raise ContractError(problems[0])
    assert manifest is not None
    if manifest["evidence_class"] == MOCK_EVIDENCE_CLASS and requested != MOCK_EVIDENCE_CLASS:
        raise ContractError("mock_output_cannot_be_real_evidence")
    if manifest["evidence_class"] != MOCK_EVIDENCE_CLASS:
        raise ContractError("unknown_evidence_class")
    return MOCK_EVIDENCE_CLASS
