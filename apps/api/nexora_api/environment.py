"""Local runtime isolation; independent of domain/strategy settings."""

from __future__ import annotations

import importlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
PROFILES = json.loads(Path(__file__).with_name("environments.json").read_text(encoding="utf-8-sig"))
MARKER = ".nexora-environment.json"


def load_environment_file() -> None:
    name = os.environ.get("NEXORA_ENV", "development")
    if name not in {"development", "production"}:
        raise ValueError("invalid_nexora_environment")
    # Never inherit the legacy shared .env file.
    path = REPOSITORY / f".env.{name}"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            value = value.strip("\"'")
            if key == "NEXORA_ENV" and value != name:
                raise ValueError("environment_file_identity_mismatch")
            if key.startswith("NEXORA_"):
                os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Environment:
    name: str
    code: Path
    root: Path
    storage: Path
    state: Path
    checkpoints: Path
    logs: Path
    cache: Path
    api_host: str
    api_port: int
    web_port: int

    @property
    def api_url(self) -> str:
        return f"http://{self.api_host}:{self.api_port}"

    @classmethod
    def resolve(
        cls, values: Mapping[str, str] | None = None, *, code: Path | None = None
    ) -> Environment:
        env = os.environ if values is None else values
        name = env.get("NEXORA_ENV", "development")
        if name not in {"development", "production"}:
            raise ValueError("invalid_nexora_environment")
        code = (code or REPOSITORY).resolve()
        root = Path(env.get("NEXORA_RUNTIME_ROOT", str(code / ".runtime" / name))).resolve()
        profile = PROFILES[name]
        host = env.get("NEXORA_API_HOST", profile["api_host"])
        api = int(env.get("NEXORA_API_PORT", str(profile["api_port"])))
        web = int(env.get("NEXORA_WEB_PORT", str(profile["web_port"])))
        if (host, api, web) != (profile["api_host"], profile["api_port"], profile["web_port"]):
            raise ValueError("environment_listener_mismatch")
        for key in ("NEXORA_RESEARCH_CONFIG", "NEXORA_BACKTEST_CONFIG_DIR"):
            if env.get(key):
                source = Path(env[key]).resolve()
                if not (source.is_relative_to(code) or source.is_relative_to(root)):
                    raise ValueError("configuration_outside_environment")
        paths = []
        for key, default in (
            ("NEXORA_JOURNAL_PATH", "storage/research.sqlite"),
            ("NEXORA_STATE_PATH", "state"),
            ("NEXORA_CHECKPOINT_PATH", "checkpoints"),
            ("NEXORA_LOG_PATH", "logs"),
            ("NEXORA_CACHE_PATH", "cache"),
        ):
            path = Path(env.get(key, str(root / default))).resolve()
            if not path.is_relative_to(root) or path == root:
                raise ValueError("writable_path_outside_environment")
            if path.exists() and path.is_file() and path.stat().st_nlink > 1:
                raise ValueError("shared_hardlinked_storage")
            paths.append(path)
        return cls(
            name, code, root, paths[0], paths[1], paths[2], paths[3], paths[4], host, api, web
        )

    def prepare(self) -> None:
        identity = {"environment": self.name, "code": str(self.code), "root": str(self.root)}
        for path in (
            self.code,
            self.root,
            self.storage.parent,
            self.state,
            self.checkpoints,
            self.logs,
            self.cache,
        ):
            for parent in (path, *path.parents):
                marker = parent / MARKER
                if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != identity:
                    raise ValueError("environment_ownership_mismatch")
        if not (self.root / MARKER).exists() and self.root.exists() and any(self.root.iterdir()):
            raise ValueError("unowned_runtime_requires_explicit_migration")
        for target in (self.code, self.root):
            target.mkdir(parents=True, exist_ok=True)
            marker = target / MARKER
            try:
                with marker.open("x", encoding="utf-8") as handle:
                    json.dump(identity, handle, sort_keys=True)
            except FileExistsError:
                if json.loads(marker.read_text(encoding="utf-8")) != identity:
                    raise ValueError("environment_ownership_mismatch") from None
        for path in (self.storage.parent, self.state, self.checkpoints, self.logs, self.cache):
            path.mkdir(parents=True, exist_ok=True)
        logging.getLogger("uvicorn.error").info(
            "NEXORA ENV: %s | API: %s | Storage: %s", self.name.upper(), self.api_url, self.storage
        )


def validate_postgres_identity(conninfo: str, environment: str) -> None:
    """Provision identity and separate DB roles before application access."""
    try:
        psycopg = importlib.import_module("psycopg")
        with psycopg.connect(conninfo) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(
                "SELECT environment FROM public.nexora_environment_identity"
            ).fetchall()
            if rows != [(environment,)]:
                raise ValueError("postgres_environment_mismatch")
    except Exception:
        raise ValueError("postgres_environment_identity_unverified") from None
