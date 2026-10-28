"""Round 4 defensive assertions for the execution contract (findings N4 and R3-3).

Adversarial cases C1-C12 of the PR #95 Round 4 prompt. Each assertion states the REMEDIATED
behaviour, so it fails on `3cc8869` (no CLI, optional `repo_root`, no path canonicalisation, no
descriptor-relative writes) and passes here. Everything is synthetic and stays in pytest temp dirs.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.test_execution_contract import (
    CHECKLIST,
    REPO,
    SHA,
    SYMBOL,
    _code,
    argv,
    plan,
)
from tests.windows_integration_readiness.vectors import TICKS_SYNTH

needs_dir_fd = pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
    reason="descriptor-relative I/O unavailable on this platform; run_mock refuses to write",
)


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    return repo


def test_c1_output_directly_inside_repository(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    assert _code(lambda: plan(repo / "data" / "x", repo_root=repo)) == "output_dir_inside_checkout"
    real = REPO / "nonexistent-r4-out"
    assert _code(lambda: plan(real)) == "output_dir_inside_checkout"  # the real checkout


def test_c2_output_through_symlinked_parent(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    link = tmp_path / "elsewhere"
    link.symlink_to(repo / "data")
    assert _code(lambda: plan(link / "x", repo_root=repo)) == "output_dir_inside_checkout"


def test_c2_repo_root_given_through_a_symlink(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo)
    assert _code(lambda: plan(repo / "data" / "x", repo_root=alias)) == (
        "output_dir_inside_checkout"
    )


def test_c3_output_through_dot_dot_traversal(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    sneaky = tmp_path / "elsewhere" / ".." / "repo" / "data" / "x"
    assert _code(lambda: plan(sneaky, repo_root=repo)) == "output_dir_inside_checkout"


def test_c3_string_prefix_sibling_is_not_inside(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    sibling = tmp_path / "repo-data-out"  # shares the string prefix "<tmp>/repo"
    assert plan(sibling, repo_root=repo).out_dir == sibling.resolve()


def test_c4_omitted_repository_root(tmp_path: Path) -> None:
    assert _code(lambda: plan(tmp_path / "o", repo_root=None)) == "repo_root_required"
    params: dict[str, Any] = {
        "provider": "mock",
        "offline": True,
        "preregistered_symbol": SYMBOL,
        "extractor_sha": SHA,
        "expected_extractor_sha": SHA,
        "extractor_version": ec.EXTRACTOR_VERSION,
        "manifest_schema": ec.MANIFEST_SCHEMA,
    }
    with pytest.raises(TypeError):
        ec.plan_invocation(argv(tmp_path / "o"), **params)  # no default exists


def test_c4_relative_repository_root(tmp_path: Path) -> None:
    assert _code(lambda: plan(tmp_path / "o", repo_root=Path("repo"))) == (
        "repo_root_must_be_absolute"
    )


def test_c5_relative_output_path(tmp_path: Path) -> None:
    rel = Path("out")
    assert _code(lambda: plan(rel, argv(rel))) == "output_dir_must_be_absolute"
    dotted = Path("../out")
    assert _code(lambda: plan(dotted, argv(dotted))) == "output_dir_must_be_absolute"


def test_c6_repository_root_itself_as_output(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    assert _code(lambda: plan(repo, repo_root=repo)) in (
        "output_dir_inside_checkout",
        "output_dir_not_empty",
    )
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    assert _code(lambda: plan(empty_repo, repo_root=empty_repo)) == "output_dir_inside_checkout"


@needs_dir_fd
def test_c7_parent_swapped_for_a_symlink_after_validation(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    parent = tmp_path / "staging"
    parent.mkdir()
    p = plan(parent / "out", repo_root=repo)
    parent.rename(tmp_path / "staging-moved")
    parent.symlink_to(repo / "data")
    code = _code(lambda: ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op"))
    assert code == "output_parent_changed_since_plan"
    assert list((repo / "data").iterdir()) == []


@needs_dir_fd
def test_c7_parent_replaced_by_another_directory(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    parent = tmp_path / "staging"
    parent.mkdir()
    p = plan(parent / "out", repo_root=repo)
    parent.rename(tmp_path / "staging-moved")
    parent.mkdir()
    code = _code(lambda: ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op"))
    assert code == "output_parent_changed_since_plan"


@needs_dir_fd
def test_c7_validated_parent_moved_into_the_repository(tmp_path: Path) -> None:
    """Same inode, new place: only the descriptor ancestry walk can see it."""
    repo = _fake_repo(tmp_path)
    parent = tmp_path / "staging"
    parent.mkdir()
    p = plan(parent / "out", repo_root=repo)
    parent.rename(repo / "data" / "staging")
    parent.symlink_to(repo / "data" / "staging")
    code = _code(lambda: ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op"))
    assert code in ("output_parent_changed_since_plan", "output_dir_inside_checkout")
    assert not (repo / "data" / "staging" / "out").exists()


@needs_dir_fd
def test_c7_output_leaf_swapped_for_a_symlink_after_validation(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    p = plan(tmp_path / "out", repo_root=repo)
    (tmp_path / "out").symlink_to(repo / "data")
    code = _code(lambda: ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op"))
    assert code == "output_dir_changed_since_plan"
    assert list((repo / "data").iterdir()) == []


@needs_dir_fd
def test_c8_valid_output_outside_repository(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    p = plan(tmp_path / "out", repo_root=repo)
    r = ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op")
    assert r.status == "COMPLETE" and r.manifest is not None
    assert sorted(x.name for x in p.out_dir.iterdir()) == sorted([*r.chunk_files, "manifest.json"])
    assert not any(repo.rglob("chunk-*"))


@needs_dir_fd
def test_c9_mock_output_relabelled_real(tmp_path: Path) -> None:
    r = ec.run_mock(plan(tmp_path / "o"), TICKS_SYNTH, extractor_sha=SHA, operator="op")
    assert r.manifest is not None
    relabelled = {**r.manifest, "evidence_class": "REAL_WINDOWS_RUN"}
    assert _code(lambda: ec.evidence_class_for(relabelled, "REAL_WINDOWS_RUN")) == (
        "mock_source_relabelled"
    )
    assert _code(lambda: ec.evidence_class_for(r.manifest, "REAL_WINDOWS_RUN")) == (
        "mock_output_cannot_be_real_evidence"
    )


def test_c10_execution_steps_requiring_dep03_stay_blocked(tmp_path: Path) -> None:
    dep03 = [s for s, (d, why) in ec.REAL_HALF_STEPS.items() if why.startswith("DEP-03")]
    assert len(dep03) == 8
    assert all(ec.REAL_HALF_STEPS[s][0] == ec.BLOCKED for s in dep03)
    assert ec.EXTRACTOR_CAPABILITIES == frozenset()
    rc = ec.main(_cli_args(tmp_path, extra=["--require-capability", "inject_source"]))
    assert rc == 1


def _checklist_command() -> str:
    text = CHECKLIST.read_text(encoding="utf-8")
    lines: list[str] = re.findall(
        r"^python -m tests\.windows_integration_readiness\.execution_contract plan .*$",
        text,
        flags=re.M,
    )
    assert len(lines) == 1, lines
    return lines[0]


def _cli_args(tmp_path: Path, extra: list[str] | None = None) -> list[str]:
    repo = _fake_repo(tmp_path) if not (tmp_path / "repo").exists() else tmp_path / "repo"
    subs = {
        "<checkout>": str(repo),
        "<symbol>": SYMBOL,
        "<head>": SHA,
        "<SHA>": SHA,
    }
    tokens = shlex.split(_checklist_command())
    assert tokens[:3] == ["python", "-m", "tests.windows_integration_readiness.execution_contract"]
    assert tokens[-1] == "<extractor-arguments>"
    args = [subs.get(t, t) for t in tokens[3:-1]]
    sep = args.index("--")
    return [*args[:sep], *(extra or []), *args[sep:], *argv(tmp_path / "out")]


def test_c11_cli_invocation_matches_documented_checklist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ec.main(_cli_args(tmp_path)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and report["executed"] is False
    assert report["windows_verified"] is False
    assert report["out_dir"] == str((tmp_path / "out").resolve())
    assert "B4 | Check every planned extractor command line with the planning command" in (
        CHECKLIST.read_text(encoding="utf-8")
    )


def test_c11_cli_refusals_and_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _cli_args(tmp_path)
    inside = [*args[: args.index("--out") + 1], str(tmp_path / "repo" / "data" / "x")]
    assert ec.main(inside) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "output_dir_inside_checkout"
    i = args.index("--repo-root")
    assert ec.main(args[:i] + args[i + 2 :]) == 2  # --repo-root is mandatory


def test_c12_no_mt5_import_or_initialization() -> None:
    tree = ast.parse(Path(ec.__file__).read_text(encoding="utf-8"))
    imported = {
        n.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in (
            [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        )
    }
    assert not imported & {"MetaTrader5", "nexora", "socket", "subprocess"}
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"initialize", "login", "order_send", "copy_ticks_range"}
    assert "MetaTrader5" not in sys.modules
