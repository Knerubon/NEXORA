"""Local research API boundary."""

from __future__ import annotations

import os
from pathlib import Path


def _load_local_runtime_env() -> None:
    """Bootstrap local-only overrides from the repo-root .env file.

    Shell variables still take precedence so operators can override the configured
    MT5 path/symbol/offset without committing machine-specific values.
    """
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_runtime_env()
