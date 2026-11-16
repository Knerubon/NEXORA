"""The rerun script refuses an --out path inside the checkout (Windows review MINOR-5)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "baseline_v1_replay.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("baseline_v1_replay_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_out_inside_checkout_is_refused_before_replay() -> None:
    script = _load()
    inside = script.REPO_ROOT / "nested" / "out.json"
    with pytest.raises(SystemExit) as exit_info:
        script.main(["--synthetic", "--events", "60", "--out", str(inside)])
    assert exit_info.value.code == 2
    assert not inside.exists()


def test_out_outside_checkout_is_written(tmp_path: Path) -> None:
    script = _load()
    target = tmp_path / "a" / "b" / "out.json"
    assert script.main(["--synthetic", "--events", "60", "--out", str(target)]) == 0
    assert b"\r" not in target.read_bytes()
