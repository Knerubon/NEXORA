"""Reviewed execution path for the real half (finding B-M3): command contract plus a mock-only
harness, and an explicit BLOCKED register for every step no reviewed interface supports.

Facts this module is built on (read at the PR #96 CANDIDATE head pinned in
`dependency-manifest.json`; re-verify at the running SHA, checklist A3):
  * `scripts/extract_market_history.py` constructs `Mt5ReadOnlyHistorySource()` itself. It has no
    source/module injection, no mock-source flag and no crash hook, and it does not expose the
    terminal's `dtype.descr`. Since the V4 custody redesign it can also sign an operator
    attestation: `--operator <principal id> --operator-key <private key file> --candidate-out`.
  * Therefore an operator can run the extractor ONLY against the real terminal. Any matrix step that
    needs a mock source, a fault hook, raw dtype access, vector replay through the extractor's
    internals, or a recording proxy around the real module would need operator-written code.
    Those steps are BLOCKED on DEP-03 (a reviewed driver or injection points in the extractor
    stack), not worked around here.

`plan_invocation` validates a future extractor command line against that contract, and
`run_mock` executes the same contract against an in-memory synthetic source only. Its output is
stamped SYNTHETIC_MOCK and can never be promoted to real evidence. Nothing here imports
`nexora.market_history` or `MetaTrader5`, opens a socket or starts a process.

Reviewed CLI for checklist B4 (finding N4), so the operator never writes Python:

    python -m tests.windows_integration_readiness.execution_contract plan \
        --repo-root <checkout> --provider mt5 --preregistered-symbol <symbol> \
        --extractor-sha <sha> --expected-extractor-sha <reviewed sha> \
        --extractor-version <version> --manifest-schema <schema> \
        -- <every argument of scripts/extract_market_history.py>

It prints one JSON object and exits 0 when the plan is valid, 1 on a ContractError, 2 on bad usage.
It only checks the command line; it runs nothing.

Output paths (finding R3-3): `--repo-root` is mandatory; the root and `--out` are canonicalised
(`os.path.realpath`, so symlinks and `..` are resolved) and compared by path components, never by
string prefix. `run_mock` re-opens the planned parent directory without following a final symlink,
checks it is the same directory (device and inode) that was validated, walks its real ancestry by
descriptor to prove it is not inside the checkout, and writes every file relative to that
descriptor with O_EXCL and O_NOFOLLOW, so a path swapped after validation is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness.reference_model import chunk_windows, encode_tick

CONTRACT_REF_SHA = dp.CANDIDATE_SHA  # PR #96 CANDIDATE head (not a gate-approved baseline)
EXTRACTOR_VERSION = dp.CONTRACT["extractor_version"]
MANIFEST_SCHEMA = dp.CONTRACT["manifest_schema"]
EXTRACT_SCRIPT = dp.CONTRACT["extract_script"]

# argparse of scripts/extract_market_history.py as read at CONTRACT_REF_SHA (manifest contract).
EXTRACT_REQUIRED = tuple(dp.CONTRACT["extract_required"])
EXTRACT_ONE_OF = tuple(dp.CONTRACT["extract_one_of"])
EXTRACT_OPTIONAL = tuple(dp.CONTRACT["extract_optional"])
EXTRACT_VALUELESS = frozenset(dp.CONTRACT["extract_valueless_flags"])
EXTRACT_FLAGS = frozenset(EXTRACT_REQUIRED + EXTRACT_ONE_OF + EXTRACT_OPTIONAL)
EXTRACT_ANCHOR_TRIO = ("--anchor-out", "--anchor-recorded-by", "--anchor-storage-ref")
# A real (provider mt5) run must produce the signed operator attestation: without these the
# dataset can never be TRUSTED (#96: "a dataset without an operator can never be TRUSTED").
REAL_RUN_CUSTODY_INPUTS = ("--operator", "--operator-key", "--candidate-out")

# What the current extractor CLI offers to a harness. Empty: see module docstring.
EXTRACTOR_CAPABILITIES: frozenset[str] = frozenset()
MOCK_CAPABILITIES = frozenset({"inject_source", "fail_on_chunk", "deterministic_bytes"})

MOCK_EVIDENCE_CLASS = "SYNTHETIC_MOCK"
MOCK_SOURCE = "synthetic in-memory mock (tests/windows_integration_readiness)"
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
    out_dir: Path  # canonical (symlinks and `..` resolved)
    symbol: str
    window_raw_msc: tuple[int, int]
    chunk_ms: int
    provider: str
    repo_root: Path  # canonical
    out_parent_id: tuple[int, int] = (0, 0)  # (st_dev, st_ino) of the validated parent
    repo_root_id: tuple[int, int] = (0, 0)


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


def _parse_complete(argv: Sequence[str]) -> dict[str, str]:
    args = _parse(argv)
    for flag in EXTRACT_REQUIRED:
        if flag not in args:
            raise ContractError(f"missing_argument:{flag}")
    if sum(f in args for f in EXTRACT_ONE_OF) != 1:
        raise ContractError("protected_range_decision_required")
    _check_numeric_arguments(args)
    return args


# RB2-1: the extractor's integer arguments (`type=int` in its argparse) and the range rules its
# library applies before the terminal is touched (`ExtractionRequest`, `QualityConfig`,
# `validate_margin`). Lexically stricter than `int()`: plain ASCII decimal only, so `1e3`, `+5`,
# `1_000`, ` 5` and non-ASCII digits are refused here even where `int()` would take them.
_INT_TOKEN = re.compile(r"-?[0-9]+")
_MARGIN_BOUND = 2**62
EXTRACT_INT_FLAGS = (
    "--from-raw-msc",
    "--to-raw-msc",
    "--chunk-ms",
    "--max-gap-ms",
    "--max-chunk-ticks",
    "--protected-margin-ms",
)


def _check_numeric_arguments(args: Mapping[str, str]) -> None:
    ints: dict[str, int] = {}
    for flag in EXTRACT_INT_FLAGS:
        if flag in args:
            if not _INT_TOKEN.fullmatch(args[flag]):
                raise ContractError("non_integer_argument")
            ints[flag] = int(args[flag])
    lo, hi, chunk = ints["--from-raw-msc"], ints["--to-raw-msc"], ints["--chunk-ms"]
    if lo < 0 or hi <= lo or chunk <= 0:
        raise ContractError("invalid_window_or_chunk")
    if ints["--max-gap-ms"] <= 0:
        raise ContractError("invalid_max_gap_ms")
    if ints.get("--max-chunk-ticks", 1) < 1:
        raise ContractError("invalid_max_chunk_ticks")
    if not 0 <= ints.get("--protected-margin-ms", 0) <= _MARGIN_BOUND:
        raise ContractError("invalid_protected_margin_ms")


# input and custody files the extractor reads or writes: none of them may sit in the dataset folder
RECORDED_OUTSIDE_DATASET = (
    "--time-conversion",
    "--clock-attestation",
    "--protected-utc-ranges",
    "--operator-key",
    "--candidate-out",
    "--anchor-out",
)


def _windows_parts(path: str) -> tuple[str, ...]:
    """Lexical, case-insensitive parts of a recorded Windows path (`/` or `\\`, `..` folded)."""
    return tuple(p.casefold() for p in PureWindowsPath(ntpath.normpath(path)).parts)


def recorded_extract_args(argv: Sequence[str]) -> dict[str, str]:
    """The arguments of a RECORDED real extractor run (argv after the script name), checked
    against the same contract as `plan_invocation` (flags, integer values and ranges, custody
    arguments) minus the filesystem checks: an evidence record is validated offline, away from the
    Windows node, so the paths are compared lexically as Windows paths instead (RB2-1: no input or
    custody file inside `--out`). Raises ContractError(code)."""
    args = _parse_complete(argv)
    _check_custody_arguments(args, "mt5")
    out = _windows_parts(args["--out"])
    for flag in RECORDED_OUTSIDE_DATASET:
        if flag in args and _windows_parts(args[flag])[: len(out)] == out:
            raise ContractError(f"{flag}_inside_dataset")
    return args


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
    repo_root: Path | None,
) -> Plan:
    """Fail-closed check of one extractor invocation. Raises ContractError(code)."""
    if provider not in ("mock", "mt5"):
        raise ContractError(f"unsupported_provider:{provider}")
    if offline and provider != "mock":
        raise ContractError("real_terminal_forbidden_offline")  # C8
    if extractor_sha != expected_extractor_sha:
        raise ContractError("extractor_sha_mismatch")  # C3
    if provider == "mt5" and not dp.head_matches_candidate(extractor_sha):
        # exact full 40-character match with the pinned candidate: no abbreviation, no stale head
        head_class = dp.classify_head(extractor_sha)
        raise ContractError(f"extractor_sha_not_pinned_pr96_candidate:{head_class}")
    if extractor_version != EXTRACTOR_VERSION:
        raise ContractError(f"extractor_version_mismatch:{extractor_version}")  # C3
    if manifest_schema != MANIFEST_SCHEMA:
        raise ContractError(f"manifest_schema_mismatch:{manifest_schema}")  # C3
    offered = MOCK_CAPABILITIES if provider == "mock" else EXTRACTOR_CAPABILITIES
    missing = sorted(required_capabilities - offered)
    if missing:
        raise ContractError(f"capability_unsupported:{','.join(missing)}")  # C2
    args = _parse_complete(argv)  # C1, integer arguments and ranges included (RB2-1)
    if args["--symbol"] != preregistered_symbol:
        raise ContractError("symbol_differs_from_preregistration")  # C9: verbatim, no mapping
    lo, hi = int(args["--from-raw-msc"]), int(args["--to-raw-msc"])
    chunk = int(args["--chunk-ms"])
    _check_custody_arguments(args, provider)
    root, root_id = _canonical_root(repo_root)
    raw_out = Path(args["--out"])
    if not raw_out.is_absolute():
        raise ContractError("output_dir_must_be_absolute")  # C4
    if raw_out.is_symlink() or (raw_out.exists() and not raw_out.is_dir()):
        raise ContractError("output_dir_invalid")  # C4: the leaf itself is never a link
    out = Path(os.path.realpath(raw_out))
    if not out.parent.is_dir() or out.parent == out:
        raise ContractError("output_dir_invalid")  # C4: existing parent chain required
    if _inside(out, root):
        raise ContractError("output_dir_inside_checkout")  # C4: raw ticks never in Git
    if out.exists() and any(out.iterdir()):
        raise ContractError("output_dir_not_empty")  # C5
    _check_custody_paths(args, root, out)
    st = os.stat(out.parent)
    return Plan(
        tuple(argv),
        out,
        args["--symbol"],
        (lo, hi),
        chunk,
        provider,
        root,
        (st.st_dev, st.st_ino),
        root_id,
    )


def _check_custody_arguments(args: Mapping[str, str], provider: str) -> None:
    """Operator attestation arguments (V4 custody at the pinned candidate)."""
    if "--operator" in args and (problem := cg.identity_problem(args["--operator"])):
        raise ContractError(f"operator_identity_invalid:{problem}")  # strict #96 grammar
    if "--candidate-out" in args and not ("--operator" in args and "--operator-key" in args):
        raise ContractError("candidate_out_needs_operator_and_operator_key")
    if "--operator-key" in args and "--operator" not in args:
        raise ContractError("operator_key_without_operator")
    present = [f for f in EXTRACT_ANCHOR_TRIO if f in args]
    if present and len(present) != len(EXTRACT_ANCHOR_TRIO):
        raise ContractError("anchor_arguments_incomplete")
    if provider == "mt5":
        for flag in REAL_RUN_CUSTODY_INPUTS:
            if flag not in args:
                raise ContractError(f"missing_argument:{flag}")  # no TRUSTED without these


def _check_custody_paths(args: Mapping[str, str], root: Path, out: Path) -> None:
    """The operator key, the candidate and the anchor never live in the checkout or in the dataset
    folder (#96: keys stay with their holder; a candidate or anchor beside the data proves
    nothing)."""
    for flag in ("--operator-key", "--candidate-out", "--anchor-out"):
        if flag not in args:
            continue
        raw = Path(args[flag])
        if not raw.is_absolute():
            raise ContractError(f"{flag}_must_be_absolute")
        real = Path(os.path.realpath(raw))
        if _inside(real, out):
            raise ContractError(f"{flag}_inside_dataset")
        if _inside(real, root):
            raise ContractError(f"{flag}_inside_checkout")
        if flag != "--operator-key" and (raw.exists() or raw.is_symlink()):
            raise ContractError(f"{flag}_file_exists")  # #96 refuses to overwrite either


def _canonical_root(repo_root: Path | None) -> tuple[Path, tuple[int, int]]:
    if repo_root is None:
        raise ContractError("repo_root_required")  # R3-3: the inside-checkout check never skips
    root = Path(repo_root)
    if not root.is_absolute():
        raise ContractError("repo_root_must_be_absolute")
    real = Path(os.path.realpath(root))
    if not real.is_dir():
        raise ContractError("repo_root_not_a_directory")
    st = os.stat(real)
    return real, (st.st_dev, st.st_ino)


def _inside(path: Path, root: Path) -> bool:
    """Component-wise containment of two canonical paths (no string-prefix comparison)."""
    return path == root or root in path.parents


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _fd_inside(dir_fd: int, root_id: tuple[int, int]) -> bool:
    """Walk the real ancestry of an open directory by descriptor and report whether the checkout
    root is on it. Independent of any path string, so a symlink or rename cannot disguise it."""
    fd = os.dup(dir_fd)
    try:
        while True:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == root_id:
                return True
            parent = os.open("..", _DIR_FLAGS, dir_fd=fd)
            pst = os.fstat(parent)
            os.close(fd)
            fd = parent
            if (pst.st_dev, pst.st_ino) == (st.st_dev, st.st_ino):
                return False  # reached the filesystem root
    finally:
        os.close(fd)


def _open_out_dir(plan: Plan) -> int:
    """Open the planned output directory safely, or raise ContractError (see module docstring)."""
    if os.open not in os.supports_dir_fd or not _NOFOLLOW:
        raise ContractError("platform_lacks_descriptor_relative_io")
    try:
        parent = os.open(plan.out_dir.parent, _DIR_FLAGS | _NOFOLLOW)
    except OSError:
        raise ContractError("output_parent_changed_since_plan") from None
    try:
        st = os.fstat(parent)
        if (st.st_dev, st.st_ino) != plan.out_parent_id:
            raise ContractError("output_parent_changed_since_plan")
        if _fd_inside(parent, plan.repo_root_id):
            raise ContractError("output_dir_inside_checkout")
        name = plan.out_dir.name
        try:
            os.mkdir(name, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
        try:
            return os.open(name, _DIR_FLAGS | _NOFOLLOW, dir_fd=parent)
        except OSError:
            raise ContractError("output_dir_changed_since_plan") from None
    finally:
        os.close(parent)


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
    out_fd = _open_out_dir(plan)
    try:
        return _run_mock_in(out_fd, plan, ticks, extractor_sha, operator, fail_on_chunk)
    finally:
        os.close(out_fd)


def _write_new(out_fd: int, name: str, body: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
    fd = os.open(name, flags, 0o644, dir_fd=out_fd)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)


def _run_mock_in(
    out_fd: int,
    plan: Plan,
    ticks: Sequence[tuple[int, str, str, int]],
    extractor_sha: str,
    operator: str,
    fail_on_chunk: int | None,
) -> MockResult:
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
        _write_new(out_fd, name, body)
        hashes.update(body)
        result.chunk_files.append(name)
    result.manifest = {
        "evidence_class": MOCK_EVIDENCE_CLASS,
        "extractor_sha": extractor_sha,
        "extractor_version": EXTRACTOR_VERSION,
        "manifest_schema": MANIFEST_SCHEMA,
        "source": MOCK_SOURCE,
        "symbol": plan.symbol,
        "window_raw_msc": [lo, hi],
        "operator": operator,
        "content_sha256": hashes.hexdigest(),
    }
    _write_new(out_fd, "manifest.json", json.dumps(result.manifest, sort_keys=True).encode())
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
        if manifest.get("source") == MOCK_SOURCE:
            raise ContractError("mock_source_relabelled")  # C9
        raise ContractError("unknown_evidence_class")
    return MOCK_EVIDENCE_CLASS


def _cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tests.windows_integration_readiness.execution_contract",
        description="Offline check of one extractor command line (checklist B4). Runs nothing.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="validate an extractor invocation; args after --")
    plan.add_argument("--repo-root", required=True)
    plan.add_argument("--provider", required=True, choices=("mock", "mt5"))
    plan.add_argument("--offline", action="store_true")
    plan.add_argument("--preregistered-symbol", required=True)
    plan.add_argument("--extractor-sha", required=True)
    plan.add_argument("--expected-extractor-sha", required=True)
    plan.add_argument("--extractor-version", required=True)
    plan.add_argument("--manifest-schema", required=True)
    plan.add_argument("--require-capability", action="append", default=[])
    plan.add_argument("extractor_argv", nargs=argparse.REMAINDER)
    return p


def main(argv: Sequence[str]) -> int:
    try:
        ns = _cli_parser().parse_args(list(argv))
    except SystemExit:
        return 2
    extractor_argv = list(ns.extractor_argv)
    if extractor_argv[:1] == ["--"]:
        extractor_argv = extractor_argv[1:]
    try:
        p = plan_invocation(
            extractor_argv,
            provider=ns.provider,
            offline=ns.offline,
            preregistered_symbol=ns.preregistered_symbol,
            extractor_sha=ns.extractor_sha,
            expected_extractor_sha=ns.expected_extractor_sha,
            extractor_version=ns.extractor_version,
            manifest_schema=ns.manifest_schema,
            required_capabilities=frozenset(ns.require_capability),
            repo_root=Path(ns.repo_root),
        )
    except ContractError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
        return 1
    report = {
        "ok": True,
        "provider": p.provider,
        "symbol": p.symbol,
        "window_raw_msc": list(p.window_raw_msc),
        "chunk_ms": p.chunk_ms,
        "out_dir": str(p.out_dir),
        "repo_root": str(p.repo_root),
        "executed": False,  # this command only checks; it never runs the extractor
        "windows_verified": False,
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
