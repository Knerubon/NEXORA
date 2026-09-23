"""Environment isolation boundaries; no workstation runtime resources are used."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nexora_api.environment import MARKER, Environment
from nexora_api.main import create_app
from nexora_api.research import configured_journal
from starlette.websockets import WebSocketDisconnect


def settings(tmp_path: Path, name: str) -> Environment:
    return Environment.resolve({"NEXORA_ENV": name}, code=tmp_path / name)


def test_separate_defaults_and_ownership(tmp_path: Path) -> None:
    dev, prod = (settings(tmp_path, name) for name in ("development", "production"))
    assert (dev.api_port, dev.web_port, prod.api_port, prod.web_port) == (8000, 3000, 8100, 3100)
    for key in ("storage", "state", "checkpoints", "logs", "cache"):
        assert getattr(dev, key) != getattr(prod, key)
    dev.prepare()
    prod.prepare()
    assert json.loads((prod.root / MARKER).read_text())["environment"] == "production"
    dev.prepare()  # Same owner restart is allowed.


@pytest.mark.parametrize(
    "key",
    [
        "NEXORA_JOURNAL_PATH",
        "NEXORA_STATE_PATH",
        "NEXORA_CHECKPOINT_PATH",
        "NEXORA_LOG_PATH",
        "NEXORA_CACHE_PATH",
    ],
)
def test_external_writable_paths_rejected(tmp_path: Path, key: str) -> None:
    prod = settings(tmp_path, "production")
    prod.prepare()
    with pytest.raises(ValueError, match="writable_path_outside"):
        Environment.resolve({key: str(prod.root / "target")}, code=tmp_path / "development")


def test_relabelled_root_and_worktree_rejected(tmp_path: Path) -> None:
    prod = settings(tmp_path, "production")
    prod.prepare()
    before = (prod.root / MARKER).read_bytes()
    with pytest.raises(ValueError, match="ownership_mismatch"):
        Environment.resolve(
            {"NEXORA_RUNTIME_ROOT": str(prod.root)}, code=tmp_path / "dev"
        ).prepare()
    with pytest.raises(ValueError, match="ownership_mismatch"):
        Environment.resolve({}, code=prod.code).prepare()
    assert (prod.root / MARKER).read_bytes() == before


def test_reverse_environment_and_nested_root_rejected(tmp_path: Path) -> None:
    dev = settings(tmp_path, "development")
    dev.prepare()
    with pytest.raises(ValueError, match="ownership_mismatch"):
        Environment.resolve(
            {"NEXORA_ENV": "production", "NEXORA_RUNTIME_ROOT": str(dev.root / "nested")},
            code=tmp_path / "prod",
        ).prepare()


def test_existing_unowned_data_is_never_adopted(tmp_path: Path) -> None:
    dev = settings(tmp_path, "development")
    dev.storage.parent.mkdir(parents=True)
    dev.storage.write_bytes(b"do not modify")
    with pytest.raises(ValueError, match="explicit_migration"):
        dev.prepare()
    assert dev.storage.read_bytes() == b"do not modify"


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    dev = settings(tmp_path, "development")
    dev.prepare()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = dev.root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError, match="writable_path_outside"):
        Environment.resolve(
            {"NEXORA_RUNTIME_ROOT": str(dev.root), "NEXORA_STATE_PATH": str(link)}, code=dev.code
        )


def test_hardlink_storage_rejected(tmp_path: Path) -> None:
    dev = settings(tmp_path, "development")
    dev.prepare()
    original = tmp_path / "prod.sqlite"
    original.write_bytes(b"protected")
    dev.storage.hardlink_to(original)
    with pytest.raises(ValueError, match="hardlinked"):
        Environment.resolve({}, code=dev.code)


@pytest.mark.parametrize(
    "values",
    [
        {"NEXORA_ENV": "typo"},
        {"NEXORA_API_PORT": "8100"},
        {"NEXORA_WEB_PORT": "3100"},
        {"NEXORA_API_HOST": "0.0.0.0"},
    ],
)
def test_invalid_environment_or_listeners_rejected(tmp_path: Path, values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Environment.resolve(values, code=tmp_path)


def test_configured_journal_refuses_prod_before_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prod = settings(tmp_path, "production")
    prod.prepare()
    monkeypatch.setenv("NEXORA_JOURNAL_PATH", str(prod.storage))
    with pytest.raises(ValueError, match="outside"):
        configured_journal()
    assert not prod.storage.exists()


def test_api_exposes_environment_and_rejects_cross_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_ENV", "production")
    with TestClient(create_app(start_worker=False), base_url="http://localhost") as client:
        assert client.get("/health").json()["environment"] == "production"
        assert client.get("/config", headers={"origin": "http://localhost:3100"}).status_code == 200
        assert client.get("/state", headers={"origin": "http://localhost:3000"}).status_code == 403


def test_external_origin_is_opt_in_and_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """REMOTE1: an arbitrary external Origin stays rejected unless explicitly configured."""
    monkeypatch.delenv("NEXORA_EXTERNAL_ORIGIN", raising=False)
    with TestClient(create_app(start_worker=False), base_url="http://localhost") as client:
        rejected = client.get("/state", headers={"origin": "https://nexora.example.com"})
    assert rejected.status_code == 403


def test_external_origin_is_accepted_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", "https://nexora.example.com")
    with TestClient(create_app(start_worker=False), base_url="http://localhost") as client:
        allowed = client.get("/state", headers={"origin": "https://nexora.example.com"})
        still_rejected = client.get("/state", headers={"origin": "https://attacker.example.com"})
        local_still_works = client.get("/config", headers={"origin": "http://localhost:3000"})
    assert allowed.status_code == 200
    assert still_rejected.status_code == 403
    assert local_still_works.status_code == 200


def test_external_origin_websocket_requires_configured_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", "https://nexora.example.com")
    with TestClient(create_app(start_worker=False), base_url="http://localhost") as client:
        with client.websocket_connect(
            "/ws/events", headers={"origin": "https://nexora.example.com"}
        ) as ws:
            message = ws.receive_json()
        assert message["event_type"] == "state_snapshot"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/events", headers={"origin": "https://attacker.example.com"}
            ):
                pass


EXTERNAL_ORIGIN = "https://nexora.example.com"
IDENTITY_HEADER = "tailscale-user-login"
IDENTITY_VALUE = "alice@example.com"


def _client_from(peer: str) -> TestClient:
    return TestClient(create_app(start_worker=False), base_url="http://localhost", client=(peer, 0))


# 1-2: local loopback REST/WS remain accepted, with no identity header at all.


def test_loopback_rest_accepted_without_identity_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("127.0.0.1") as client:
        response = client.get("/state", headers={"origin": "http://localhost:3000"})
    assert response.status_code == 200


def test_loopback_ws_accepted_without_identity_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("127.0.0.1") as client:
        with client.websocket_connect("/ws/events") as ws:
            message = ws.receive_json()
        assert message["event_type"] == "state_snapshot"


# 3-4: external origin + Tailscale identity + correct Origin -> REST and WS accepted.


def test_remote_rest_accepted_with_identity_and_correct_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        response = client.get(
            "/state", headers={"origin": EXTERNAL_ORIGIN, IDENTITY_HEADER: IDENTITY_VALUE}
        )
    assert response.status_code == 200


def test_remote_ws_accepted_with_identity_and_correct_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        with client.websocket_connect(
            "/ws/events", headers={"origin": EXTERNAL_ORIGIN, IDENTITY_HEADER: IDENTITY_VALUE}
        ) as ws:
            message = ws.receive_json()
        assert message["event_type"] == "state_snapshot"


# 5-6: external origin configured, correct Origin, but NO identity -> rejected.


def test_remote_rest_rejected_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        response = client.get("/state", headers={"origin": EXTERNAL_ORIGIN})
    assert response.status_code == 403


def test_remote_ws_rejected_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events", headers={"origin": EXTERNAL_ORIGIN}):
                pass


# 7: whitespace-only identity -> rejected, not silently trusted.


@pytest.mark.parametrize("whitespace_identity", ["", "   ", "\t"])
def test_whitespace_identity_rejected(
    monkeypatch: pytest.MonkeyPatch, whitespace_identity: str
) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        response = client.get(
            "/state", headers={"origin": EXTERNAL_ORIGIN, IDENTITY_HEADER: whitespace_identity}
        )
    assert response.status_code == 403


# 8: valid identity, wrong Origin -> rejected. Transport trust alone is not enough.


def test_valid_identity_wrong_origin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        response = client.get(
            "/state",
            headers={"origin": "https://attacker.example.com", IDENTITY_HEADER: IDENTITY_VALUE},
        )
    assert response.status_code == 403


# 9: valid identity, but NEXORA_EXTERNAL_ORIGIN unset -> remote path fully disabled.


def test_valid_identity_without_external_origin_configured_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEXORA_EXTERNAL_ORIGIN", raising=False)
    with _client_from("100.79.209.92") as client:
        response = client.get(
            "/state", headers={"origin": EXTERNAL_ORIGIN, IDENTITY_HEADER: IDENTITY_VALUE}
        )
    assert response.status_code == 403


# 10: an arbitrary non-loopback peer with no identity -> rejected.


def test_arbitrary_non_loopback_peer_without_identity_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("203.0.113.7") as client:
        response = client.get("/state", headers={"origin": EXTERNAL_ORIGIN})
    assert response.status_code == 403


# 11: a peer inside the Tailscale CGNAT range with no identity -> still rejected.
# Proves the design never trusts 100.64.0.0/10 by IP membership alone.


def test_cgnat_range_peer_without_identity_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.100.100.100") as client:
        rest = client.get("/state", headers={"origin": EXTERNAL_ORIGIN})
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events", headers={"origin": EXTERNAL_ORIGIN}):
                pass
    assert rest.status_code == 403


# 12: existing mutation/origin protections remain green.


def test_mutation_still_requires_origin_even_with_valid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXORA_EXTERNAL_ORIGIN", EXTERNAL_ORIGIN)
    with _client_from("100.79.209.92") as client:
        no_origin = client.post(
            "/paper/control", json={"action": "pause"}, headers={IDENTITY_HEADER: IDENTITY_VALUE}
        )
    assert no_origin.status_code == 403


def test_environment_file_selected_not_shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nexora_api.environment import load_environment_file

    monkeypatch.setattr("nexora_api.environment.REPOSITORY", tmp_path)
    (tmp_path / ".env").write_text("NEXORA_MT5_SYMBOL=WRONG")
    (tmp_path / ".env.development").write_text("NEXORA_MT5_SYMBOL=DEV")
    (tmp_path / ".env.production").write_text("NEXORA_MT5_SYMBOL=PROD")
    monkeypatch.setenv("NEXORA_ENV", "production")
    load_environment_file()
    import os

    assert os.environ["NEXORA_MT5_SYMBOL"] == "PROD"
    monkeypatch.setenv("NEXORA_MT5_SYMBOL", "SHELL")
    load_environment_file()
    assert os.environ["NEXORA_MT5_SYMBOL"] == "SHELL"


def test_stop_validates_identity_and_leaves_other_environment_alive(tmp_path: Path) -> None:
    import subprocess
    import sys

    import psutil
    from nexora_api.launch import stop_owned

    dev, prod = tmp_path / "dev", tmp_path / "prod"
    dev.mkdir()
    prod.mkdir()
    processes = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=code)
        for code in (dev, prod)
    ]
    try:
        process = psutil.Process(processes[0].pid)
        record = {
            "pid": process.pid,
            "created": process.create_time(),
            "cwd": str(dev.resolve()),
            "command": process.cmdline(),
        }
        with pytest.raises(ValueError, match="identity_mismatch"):
            stop_owned([{**record, "created": process.create_time() - 1}], dev)
        assert processes[0].poll() is None
        stop_owned([record], dev)
        assert processes[1].poll() is None
    finally:
        for child_process in processes:
            try:
                owned = psutil.Process(child_process.pid)
                children = owned.children(recursive=True)
                for child in children:
                    child.kill()
                owned.kill()
                psutil.wait_procs([owned, *children], timeout=5)
            except psutil.NoSuchProcess:
                pass


def test_postgres_wrong_identity_fails_before_journal_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexora_api.environment import validate_postgres_identity

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, statement: str) -> Connection:
            assert statement.startswith(("SET TRANSACTION READ ONLY", "SELECT environment"))
            return self

        def fetchall(self) -> list[tuple[str]]:
            return [("production",)]

    import sys
    from types import ModuleType

    module = ModuleType("psycopg")
    monkeypatch.setattr(module, "connect", lambda _: Connection(), raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", module)
    validate_postgres_identity("unused", "production")
    with pytest.raises(ValueError, match="identity_unverified"):
        validate_postgres_identity("unused", "development")


def test_two_live_api_processes_keep_separate_storage(tmp_path: Path) -> None:
    """Real simultaneous HTTP servers on ephemeral test ports; never displace host apps."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    import psutil

    code = """
import os
from pathlib import Path
import nexora_api.environment as environment
import uvicorn
environment.REPOSITORY = Path(os.environ['NEXORA_TEST_CODE'])
from nexora_api.main import create_app
uvicorn.run(create_app(start_worker=False), host='127.0.0.1',
            port=int(os.environ['NEXORA_TEST_PORT']), log_level='error')
"""
    children = []
    endpoints = []
    try:
        for name in ("development", "production"):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            env = {k: v for k, v in os.environ.items() if not k.startswith("NEXORA_")}
            env.update(
                NEXORA_ENV=name,
                NEXORA_TEST_CODE=str(tmp_path / name),
                NEXORA_TEST_PORT=str(port),
                NEXORA_RUNTIME_ROOT=str(tmp_path / name / "runtime"),
            )
            children.append(
                subprocess.Popen(
                    [sys.executable, "-c", code],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            endpoints.append((name, f"http://127.0.0.1:{port}/health"))
        deadline = time.monotonic() + 25
        for name, url in endpoints:
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        assert json.load(response)["environment"] == name
                    break
                except (urllib.error.URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        pytest.fail("isolated API startup timed out")
                    time.sleep(0.1)
        for name, url in endpoints:
            assert (tmp_path / name / "runtime/storage/research.sqlite").exists()
            with urllib.request.urlopen(url, timeout=2) as response:
                assert response.status == 200
    finally:
        for child in children:
            try:
                process = psutil.Process(child.pid)
                descendants = process.children(recursive=True)
                for descendant in descendants:
                    descendant.kill()
                process.kill()
                psutil.wait_procs([process, *descendants], timeout=5)
            except psutil.NoSuchProcess:
                pass
