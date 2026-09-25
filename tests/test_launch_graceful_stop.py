"""Graceful API stop before the existing hard termination path (ADR-029 H2)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
from nexora_api.launch import request_stop, stop_owned, stop_request_path, watch_stop_request

TOKEN = "a" * 32

SERVER = """
import contextlib, sys
from pathlib import Path
from fastapi import FastAPI
from nexora_api.launch import serve_api

root = Path(sys.argv[1])

@contextlib.asynccontextmanager
async def lifespan(app):
    (root / "started").write_text("1")
    yield
    # Stands in for the research checkpoint written in the real lifespan shutdown.
    (root / "shutdown").write_text("1")

serve_api(FastAPI(lifespan=lifespan), "127.0.0.1", int(sys.argv[2]),
          stop_request=root / "api-stop.request", token=sys.argv[3])
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def record_of(process: subprocess.Popen[bytes], cwd: Path, **extra: Any) -> dict[str, Any]:
    identity = psutil.Process(process.pid)
    return {
        "pid": process.pid,
        "created": identity.create_time(),
        "cwd": str(cwd.resolve()),
        "command": identity.cmdline(),
        **extra,
    }


@pytest.fixture
def spawned() -> Iterator[list[subprocess.Popen[bytes]]]:
    processes: list[subprocess.Popen[bytes]] = []
    yield processes
    for process in processes:
        try:
            owned = psutil.Process(process.pid)
            children = owned.children(recursive=True)
            for child in children:
                child.kill()
            owned.kill()
            psutil.wait_procs([owned, *children], timeout=5)
        except psutil.NoSuchProcess:
            pass


def sleeper(cwd: Path, spawned: list[subprocess.Popen[bytes]]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=cwd)
    spawned.append(process)
    return process


def wait_for(path: Path, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        time.sleep(0.05)


def test_graceful_stop_runs_application_shutdown_before_exit(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path), str(free_port()), TOKEN], cwd=tmp_path
    )
    spawned.append(process)
    wait_for(tmp_path / "started")
    started = time.monotonic()
    stop_owned(
        [record_of(process, tmp_path, stop_token=TOKEN)],
        tmp_path,
        stop_request=stop_request_path(tmp_path),
        grace=30,
    )
    assert (tmp_path / "shutdown").exists()
    assert process.wait(timeout=5) == 0  # exited by itself, not terminated
    assert time.monotonic() - started < 30


def test_ignored_request_falls_back_to_bounded_termination(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    process = sleeper(tmp_path, spawned)
    started = time.monotonic()
    stop_owned(
        [record_of(process, tmp_path, stop_token=TOKEN)],
        tmp_path,
        stop_request=stop_request_path(tmp_path),
        grace=0.5,
    )
    assert process.wait(timeout=5) is not None
    assert time.monotonic() - started < 15  # grace + terminate wait bound
    assert stop_request_path(tmp_path).read_text(encoding="utf-8") == TOKEN


def test_ownership_checks_precede_any_stop_request(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    process = sleeper(tmp_path, spawned)
    record = record_of(process, tmp_path, stop_token=TOKEN)
    request = stop_request_path(tmp_path)
    with pytest.raises(ValueError, match="identity_mismatch"):
        stop_owned([{**record, "created": record["created"] - 1}], tmp_path, stop_request=request)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="process_outside_worktree"):
        stop_owned([record], other, stop_request=request)
    assert not request.exists()
    assert process.poll() is None


def test_record_without_token_keeps_the_hard_path(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    """Registries written before ADR-029 (or the web record) carry no stop token."""
    process = sleeper(tmp_path, spawned)
    stop_owned([record_of(process, tmp_path)], tmp_path, stop_request=stop_request_path(tmp_path))
    assert process.wait(timeout=5) is not None
    assert not stop_request_path(tmp_path).exists()


def test_watcher_ignores_stale_or_foreign_requests(tmp_path: Path) -> None:
    path = stop_request_path(tmp_path)
    request_stop(path, "b" * 32)  # left over from an earlier run
    stopped = threading.Event()
    done = threading.Event()
    watcher = threading.Thread(
        target=watch_stop_request, args=(path, TOKEN, stopped.set, done, 0.01), daemon=True
    )
    watcher.start()
    try:
        assert not stopped.wait(0.3)
        request_stop(path, TOKEN)
        assert stopped.wait(5)
        watcher.join(5)
        assert not watcher.is_alive()
    finally:
        done.set()


def test_watcher_ends_with_the_server(tmp_path: Path) -> None:
    done = threading.Event()
    watcher = threading.Thread(
        target=watch_stop_request,
        args=(stop_request_path(tmp_path), TOKEN, lambda: None, done, 0.01),
        daemon=True,
    )
    watcher.start()
    done.set()
    watcher.join(5)
    assert not watcher.is_alive()


def test_api_action_does_not_pass_the_token_to_the_application(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import nexora_api.launch as launch

    calls: list[dict[str, Any]] = []

    def fake_serve(app: Any, host: str, port: int, **kwargs: Any) -> None:
        calls.append({"app": app, "environment": dict(os.environ), **kwargs})

    class Settings:
        api_host, api_port, state = "127.0.0.1", 1, tmp_path

    monkeypatch.setattr(launch, "serve_api", fake_serve)
    monkeypatch.setenv(launch.STOP_TOKEN_VARIABLE, TOKEN)
    launch.run_action(Settings(), "api")  # type: ignore[arg-type]
    assert calls[0]["token"] == TOKEN
    assert calls[0]["stop_request"] == stop_request_path(tmp_path)
    assert launch.STOP_TOKEN_VARIABLE not in calls[0]["environment"]
