"""Disposable research recovery checkpoints (ADR-022).

The research journal is the authoritative source of truth. A checkpoint is an
optional accelerator: an integrity-checked copy of the runtime's in-memory
state after exactly the journal rows up to ``last_sequence``, stored as one
file in the environment's own checkpoint directory. Every check in ``verify``
must pass before any of its state is trusted; any failure means the caller
performs a full journal replay. Deleting a checkpoint never destroys history.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nexora.artifacts import canonical_hash

FORMAT = "nexora-research-checkpoint"
SCHEMA_VERSION = 1
_PICKLE_PROTOCOL = 5
_MAX_HEADER_BYTES = 64 * 1024


@dataclass(frozen=True)
class CheckpointHeader:
    format: str
    schema_version: int
    environment: str
    stream: str
    code_fingerprint: str
    last_sequence: int
    last_event_key: str
    last_content_hash: str
    event_count: int
    state_hash: str
    blob_hash: str
    blob_size: int
    created_at: str


@runtime_checkable
class AnchoredJournal(Protocol):
    """Journal row identity needed to anchor a checkpoint to exact journal rows."""

    def iter_rows(self, stream: str, *, after: int = 0) -> Iterator[tuple[int, dict[str, Any]]]: ...
    def row_identity(self, stream: str, sequence: int) -> tuple[str, str] | None: ...
    def count_through(self, stream: str, sequence: int) -> int: ...
    def sequence_of(self, stream: str, key: str) -> int | None: ...


class CheckpointRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@cache
def code_fingerprint() -> str:
    """Any change to package code or interpreter invalidates every checkpoint.

    State captured by different code could differ from what a full replay by
    the current code produces, so a checkpoint is never reused across versions.
    """
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(f"{FORMAT};{SCHEMA_VERSION};{sys.version}".encode())
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def state_hash(engine_snapshot: dict[str, Any]) -> str:
    return canonical_hash(engine_snapshot)


class CheckpointStore:
    """One checkpoint file per research stream inside one environment's directory."""

    def __init__(self, directory: Path, *, environment: str) -> None:
        if not environment:
            raise ValueError("checkpoint_environment_required")
        self.directory, self.environment = Path(directory), environment

    def path_for(self, stream: str) -> Path:
        return self.directory / (stream.replace(":", "-") + ".checkpoint")

    def save(self, header: CheckpointHeader, blob: bytes) -> Path:
        """Write atomically: a crash leaves either the previous file or the new one."""
        path = self.path_for(header.stream)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(json.dumps(asdict(header), sort_keys=True).encode() + b"\n")
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def load(self, stream: str) -> tuple[CheckpointHeader, bytes] | None:
        path = self.path_for(stream)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        end = data.find(b"\n", 0, _MAX_HEADER_BYTES)
        if end < 0:
            raise CheckpointRejected("header_corrupt")
        try:
            header = CheckpointHeader(**json.loads(data[:end]))
        except Exception:
            raise CheckpointRejected("header_corrupt") from None
        return header, data[end + 1 :]


def encode(state: Any) -> tuple[bytes, str]:
    blob = pickle.dumps(state, protocol=_PICKLE_PROTOCOL)
    return blob, hashlib.sha256(blob).hexdigest()


def verify(
    header: CheckpointHeader,
    blob: bytes,
    *,
    environment: str,
    stream: str,
    journal: AnchoredJournal,
) -> Any:
    """Return the decoded state, or raise CheckpointRejected before trusting any of it."""
    if header.format != FORMAT or header.schema_version != SCHEMA_VERSION:
        raise CheckpointRejected("schema_version_mismatch")
    if header.environment != environment:
        raise CheckpointRejected("environment_mismatch")
    if header.stream != stream:
        raise CheckpointRejected("stream_mismatch")
    if header.code_fingerprint != code_fingerprint():
        raise CheckpointRejected("code_fingerprint_mismatch")
    # Integrity of the bytes is established before they are ever unpickled.
    if len(blob) != header.blob_size or hashlib.sha256(blob).hexdigest() != header.blob_hash:
        raise CheckpointRejected("blob_hash_mismatch")
    identity = journal.row_identity(stream, header.last_sequence)
    if identity != (header.last_event_key, header.last_content_hash):
        raise CheckpointRejected("event_reference_invalid")
    if journal.count_through(stream, header.last_sequence) != header.event_count:
        raise CheckpointRejected("event_count_mismatch")
    try:
        # Only integrity-checked bytes this environment's own runtime wrote.
        return pickle.loads(blob)  # noqa: S301
    except Exception:
        raise CheckpointRejected("decode_failed") from None
