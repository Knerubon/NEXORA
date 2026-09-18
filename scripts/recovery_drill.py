"""Run local recovery drill checks for P13 hardening evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from nexora.backtest import BacktestLabService

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    REPO_ROOT / "infra" / "migrations" / "004_backtest_runs.sql",
    REPO_ROOT / "infra" / "migrations" / "005_risk_decisions.sql",
    REPO_ROOT / "infra" / "migrations" / "006_paper_trading.sql",
)


def _hash_payload(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def main() -> None:
    missing = [path for path in MIGRATIONS if not path.exists()]
    if missing:
        missing_list = ", ".join(str(item) for item in missing)
        raise RuntimeError(f"missing_migrations:{missing_list}")

    service = BacktestLabService.bootstrap()
    first = service.paper_replay()
    second = service.paper_replay()
    first_hash = _hash_payload(first)
    second_hash = _hash_payload(second)
    if first_hash != second_hash:
        raise RuntimeError("non_deterministic_paper_replay")
    accepted = cast(int, first["accepted"])
    rejected = cast(int, first["rejected"])
    if accepted < 1 or rejected < 1:
        raise RuntimeError("insufficient_replay_coverage")

    checkpoint_rows = cast(list[dict[str, object]], first["checkpoints"])
    if len(checkpoint_rows) < 1:
        raise RuntimeError("missing_checkpoint_rows")

    print("PASS: migrations-present")
    print(f"PASS: deterministic-paper-replay {first_hash}")
    print(f"PASS: checkpoints {len(checkpoint_rows)}")


if __name__ == "__main__":
    main()
