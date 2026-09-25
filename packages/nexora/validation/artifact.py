"""Write-once, content-verified validation result bundles (ADR-030 Decision 11).

A bundle is assembled in a unique `<run_id>.partial-*` directory and renamed to
`<run_id>` only when complete. An existing result is never overwritten, and nothing
is ever deleted: stale partial directories stay for inspection. The optional
`envelope.json` (wall-clock time, host, duration) is written by the caller and is
excluded from integrity and run identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from nexora.artifacts import canonical_hash, canonical_serialize, decode
from nexora.validation.models import (
    RUN_ID_PREFIX,
    ObservationRecord,
    SealBroken,
    ValidationError,
    ValidationResult,
)
from nexora.validation.sealing import observation_chain, verify_seals

HASHED_FILES = (
    "manifest.json",
    "result.json",
    "observations.jsonl",
    "outcomes.jsonl",
    "metrics.json",
)


def _line(value: Any) -> bytes:
    return (
        json.dumps(canonical_serialize(value), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def bundle_files(result: ValidationResult) -> dict[str, bytes]:
    files = {
        "manifest.json": _line(result.manifest),
        "result.json": _line(
            {
                "run_id": result.run_id,
                "status": result.status,
                "invalid_reason": result.invalid_reason,
                "qualification": result.qualification,
                "causal": result.causal,
                "observation_chain_hash": result.observation_chain_hash,
                "observation_count": len(result.observations),
                "outcome_count": len(result.outcomes),
                "notes": result.notes,
            }
        ),
        "observations.jsonl": b"".join(_line(o) for o in result.observations),
        "outcomes.jsonl": b"".join(_line(o) for o in result.outcomes),
        "metrics.json": _line(result.metrics),
    }
    files["integrity.json"] = _line(
        {
            "run_id": result.run_id,
            "observation_chain_hash": result.observation_chain_hash,
            "files": {name: hashlib.sha256(files[name]).hexdigest() for name in HASHED_FILES},
        }
    )
    return files


def write_bundle(
    validation_root: Path, result: ValidationResult, *, envelope: dict[str, Any] | None = None
) -> Path:
    validation_root.mkdir(parents=True, exist_ok=True)
    final = validation_root / result.run_id
    if final.exists():
        raise ValidationError("validation_result_exists")
    partial = Path(tempfile.mkdtemp(prefix=f"{result.run_id}.partial-", dir=validation_root))
    for name, payload in bundle_files(result).items():
        (partial / name).write_bytes(payload)
    if envelope is not None:
        (partial / "envelope.json").write_bytes(_line(envelope))
    try:
        os.rename(partial, final)
    except OSError:
        raise ValidationError("validation_result_exists") from None
    return final


def verify_bundle(directory: Path) -> str:
    """Re-verify file hashes, run identity, observation seals and chain; returns the run id."""
    integrity = json.loads((directory / "integrity.json").read_text(encoding="utf-8"))
    for name in HASHED_FILES:
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if integrity["files"].get(name) != digest:
            raise SealBroken("bundle_file_hash_mismatch")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    run_id = RUN_ID_PREFIX + canonical_hash(manifest)
    if run_id != integrity["run_id"] or directory.name != run_id:
        raise SealBroken("bundle_run_id_mismatch")
    lines = (directory / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    observations = tuple(decode(ObservationRecord, json.loads(line)) for line in lines)
    verify_seals(observations)
    if observation_chain(observations) != integrity["observation_chain_hash"]:
        raise SealBroken("bundle_observation_chain_mismatch")
    return run_id
