"""Offline guards for the readiness harness (minor F8, case C8).

For every test in this directory: outbound sockets are refused, importing `MetaTrader5` is refused,
and a tripwire touched anywhere during the test fails it at teardown even if the code under test
swallowed the TripwireTouched exception."""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import socket
import sys
from collections.abc import Iterator, Sequence
from types import ModuleType
from typing import Any

import pytest

from tests.windows_integration_readiness import fakes


class NetworkAccessForbidden(BaseException):  # noqa: N818
    pass


class RealTerminalImportForbidden(BaseException):  # noqa: N818
    pass


class _BlockMetaTrader5(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname.split(".")[0] == "MetaTrader5":
            raise RealTerminalImportForbidden(fullname)
        return None


def _refuse(*_a: Any, **_k: Any) -> Any:
    raise NetworkAccessForbidden("offline harness: network access refused")


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    finder = _BlockMetaTrader5()
    sys.meta_path.insert(0, finder)
    fakes.TRIPWIRE_LOG.clear()
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
    leaked = list(fakes.TRIPWIRE_LOG)
    fakes.TRIPWIRE_LOG.clear()
    assert not leaked, f"tripwire touched during test: {leaked}"


@pytest.fixture
def expect_tripwires() -> Iterator[list[str]]:
    """For tests that deliberately touch a tripwire: yields the live log, cleared at teardown."""
    yield fakes.TRIPWIRE_LOG
    fakes.TRIPWIRE_LOG.clear()


def _pinned_git(*args: str) -> Any:
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)


@pytest.fixture(scope="session")
def export(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Read-only `git archive` of the pinned PR #96 candidate head (skip, never pass, if absent)."""
    import subprocess

    from tests.windows_integration_readiness import dependency_pin as dp

    sha = dp.CANDIDATE_SHA
    if _pinned_git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        pytest.skip(
            f"pinned PR #96 candidate {sha[:12]} is not in this clone's object store "
            "(shallow CI checkout); live contract check NOT run here"
        )
    dest = tmp_path_factory.mktemp("pr96-export")
    archive = _pinned_git("archive", sha, "packages/nexora", "scripts")
    assert archive.returncode == 0, archive.stderr[:200]
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
    return dest
