"""Canonical hashing for reproducible backtest artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


def canonical_serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return canonical_serialize(asdict(value))
    if isinstance(value, dict):
        return {key: canonical_serialize(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [canonical_serialize(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(canonical_serialize(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
