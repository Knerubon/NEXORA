"""Immutable research artifacts; mutable JSON is returned only as detached copies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nexora.artifacts import canonical_serialize

POLICY = "experience-v1"
HORIZONS = (5, 15, 30, 60)


def frozen_json(value: Any) -> str:
    return json.dumps(canonical_serialize(value), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Experience:
    schema_version: int
    policy_version: str
    experience_id: str
    scope: str
    fingerprint: str
    t0: datetime
    action: str | None
    context_json: str

    def context(self) -> dict[str, Any]:
        return dict(json.loads(self.context_json))
