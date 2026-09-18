"""Keep API tests isolated from the workstation's local runtime configuration."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in tuple(os.environ):
        if name.startswith("NEXORA_") and not name.startswith("NEXORA_TEST_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NEXORA_JOURNAL_PATH", str(tmp_path / "research.sqlite"))
