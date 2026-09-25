"""Environment-specific local process launcher. Never controls another worktree."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from nexora_api.environment import Environment


def child_environment(settings: Environment) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "NEXORA_ENV": settings.name,
            "NEXORA_RUNTIME_ROOT": str(settings.root),
            "NEXT_PUBLIC_NEXORA_ENV": settings.name,
            "NEXT_PUBLIC_API_BASE_URL": settings.api_url,
            "NEXORA_WEB_PORT": str(settings.web_port),
            "NEXORA_API_PORT": str(settings.api_port),
        }
    )
    # Next dev uses NODE_ENV=development; Next build/start uses production itself.
    env.pop("NODE_ENV", None)
    return env


# Graceful stop (ADR-029 H2). The API child watches a per-run request file in its own
# environment's state directory, so its lifespan shutdown (feed stop, research checkpoint)
# can finish before the existing terminate/kill path. Nothing listens on the network.
GRACEFUL_STOP_SECONDS = 30.0
# Open connections (e.g. dashboard WebSockets) must not hold the lifespan shutdown.
CONNECTION_DRAIN_SECONDS = 5
STOP_TOKEN_VARIABLE = "NEXORA_API_STOP_TOKEN"
_STOP_POLL_SECONDS = 0.25


def stop_request_path(state: Path) -> Path:
    return state / "api-stop.request"


def request_stop(path: Path, token: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(token, encoding="utf-8")
    os.replace(temporary, path)


def watch_stop_request(
    path: Path,
    token: str,
    on_stop: Callable[[], None],
    done: threading.Event,
    poll: float = _STOP_POLL_SECONDS,
) -> None:
    """Call `on_stop` once when `path` holds this run's token; stale requests never match."""
    while not done.wait(poll):
        try:
            requested = path.read_text(encoding="utf-8") == token
        except (OSError, UnicodeDecodeError):
            continue
        if requested:
            on_stop()
            return


def serve_api(app: Any, host: str, port: int, *, stop_request: Path, token: str | None) -> None:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app, host=host, port=port, timeout_graceful_shutdown=CONNECTION_DRAIN_SECONDS
        )
    )
    done = threading.Event()
    if token:

        def stop() -> None:
            server.should_exit = True

        threading.Thread(
            target=watch_stop_request, args=(stop_request, token, stop, done), daemon=True
        ).start()
    try:
        server.run()
    finally:
        done.set()


def stop_owned(
    records: list[dict[str, Any]],
    code: Path,
    *,
    stop_request: Path | None = None,
    grace: float = GRACEFUL_STOP_SECONDS,
) -> None:
    for record in records:
        try:
            process = psutil.Process(record["pid"])
            if process.create_time() != record["created"] or Path(process.cwd()).resolve() != Path(
                record["cwd"]
            ):
                raise ValueError("process_identity_mismatch")
            if not Path(process.cwd()).resolve().is_relative_to(code):
                raise ValueError("process_outside_worktree")
            if process.cmdline() != record["command"]:
                raise ValueError("process_command_mismatch")
            descendants = process.children(recursive=True)
            token = record.get("stop_token")
            if stop_request is not None and isinstance(token, str) and token:
                # Only after the ownership checks above; bounded, then the hard path below.
                request_stop(stop_request, token)
                _, remaining = psutil.wait_procs([process, *descendants], timeout=grace)
                for child in reversed(remaining):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(remaining, timeout=10)
                for child in alive:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                continue
            # All descendants were captured from this verified root; no name/port kill.
            for child in reversed(descendants):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            process.terminate()
            _, alive = psutil.wait_procs([process, *descendants], timeout=10)
            for child in alive:
                child.kill()
        except psutil.NoSuchProcess:
            continue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "build", "api", "describe"])
    args = parser.parse_args()
    settings = Environment.resolve()
    settings.prepare()
    if args.action == "api":
        run_action(settings, args.action)
        return
    lock = settings.state / "launcher.lock"
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        raise RuntimeError("launcher_busy_or_stale_lock_inspect_before_retry") from None
    try:
        run_action(settings, args.action)
    finally:
        lock.unlink()


def run_action(settings: Environment, action: str) -> None:
    if action == "api":
        # Not inherited by the application or anything it starts.
        token = os.environ.pop(STOP_TOKEN_VARIABLE, None)
        serve_api(
            "nexora_api.main:app",
            settings.api_host,
            settings.api_port,
            stop_request=stop_request_path(settings.state),
            token=token,
        )
        return
    node = shutil.which("node")
    web = settings.code / "apps" / "web"
    next_bin = str(web / "node_modules/next/dist/bin/next")
    env = child_environment(settings)
    registry = settings.state / "processes.json"
    if action == "describe":
        print(
            json.dumps(
                {
                    "environment": settings.name,
                    "api": settings.api_url,
                    "web": f"http://127.0.0.1:{settings.web_port}",
                    "storage": str(settings.storage),
                    "root": str(settings.root),
                }
            )
        )
        return
    if action == "stop":
        if registry.exists():
            stop_owned(
                json.loads(registry.read_text()),
                settings.code,
                stop_request=stop_request_path(settings.state),
            )
            registry.unlink()
        stop_request_path(settings.state).unlink(missing_ok=True)
        return
    if not node:
        raise RuntimeError("node_unavailable")
    if action == "build":
        if registry.exists():
            raise RuntimeError("stop_this_environment_before_build")
        subprocess.run([node, next_bin, "build"], cwd=web, env=env, check=True)
        return
    if registry.exists():
        raise RuntimeError("process_registry_exists_use_stop_first")
    # Check both before starting either. Never displace an existing listener.
    sockets = []
    try:
        for port in (settings.api_port, settings.web_port):
            check = socket.socket()
            sockets.append(check)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            check.bind((settings.api_host, port))
    finally:
        for check in sockets:
            check.close()
    if settings.name == "production":
        build_id = web / ".next" / "BUILD_ID"
        if not build_id.exists() or not build_id.read_text().startswith("production-"):
            raise RuntimeError("production_build_required")
    stop_request_path(settings.state).unlink(missing_ok=True)
    stop_token = secrets.token_hex(16)
    records: list[dict[str, Any]] = []
    try:
        for label, command, cwd in (
            ("api", [sys.executable, "-m", "nexora_api.launch", "api"], settings.code),
            (
                "web",
                [
                    node,
                    next_bin,
                    "start" if settings.name == "production" else "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(settings.web_port),
                ],
                web,
            ),
        ):
            with (settings.logs / f"{label}.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env={**env, STOP_TOKEN_VARIABLE: stop_token} if label == "api" else env,
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    creationflags=(
                        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                    ),
                )
            identity = psutil.Process(process.pid)
            records.append(
                {
                    "pid": process.pid,
                    "created": identity.create_time(),
                    "cwd": str(cwd.resolve()),
                    "command": identity.cmdline(),
                    **({"stop_token": stop_token} if label == "api" else {}),
                }
            )
        registry.write_text(json.dumps(records), encoding="utf-8")
    except Exception:
        stop_owned(records, settings.code)
        raise
    print(f"NEXORA {settings.name.upper()} started; startup readiness is not yet confirmed.")
    print(
        f"Web http://127.0.0.1:{settings.web_port} | API {settings.api_url} | Logs {settings.logs}"
    )


if __name__ == "__main__":
    main()
