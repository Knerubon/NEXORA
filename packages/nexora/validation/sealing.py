"""Observation sealing shared by every stage (ADR-030 G2). No engine imports."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.validation.models import ObservationRecord, SealBroken

GENESIS_CHAIN = "valid1-chain-genesis"


def as_of_json(value: Any) -> str:
    return json.dumps(canonical_serialize(value), sort_keys=True, separators=(",", ":"))


def record_hash(record: ObservationRecord) -> str:
    """Canonical hash of every field except `record_hash` itself."""
    payload = canonical_serialize(record)
    payload.pop("record_hash")
    return canonical_hash(payload)


def chain_hash(previous: str, record_digest: str) -> str:
    return canonical_hash((previous, record_digest))


def observation_chain(records: Iterable[ObservationRecord]) -> str:
    current = GENESIS_CHAIN
    for record in records:
        current = chain_hash(current, record.record_hash)
    return current


def verify_seals(records: Iterable[ObservationRecord]) -> None:
    for record in records:
        if record_hash(record) != record.record_hash:
            raise SealBroken("observation_seal_broken")
