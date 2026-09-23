"""Transactional append-only journals, outside pure domain engines."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from nexora.artifacts import canonical_hash, canonical_serialize


class Journal(Protocol):
    backend: str

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool: ...
    def read(self, stream: str) -> tuple[dict[str, Any], ...]: ...
    def iter_read(self, stream: str) -> Iterator[dict[str, Any]]: ...
    def close(self) -> None: ...


class SQLiteJournal:
    backend = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS research_journal (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, stream TEXT NOT NULL,
            event_key TEXT NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
            UNIQUE(stream, event_key))""")
        self.connection.commit()

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        encoded = json.dumps(canonical_serialize(payload), sort_keys=True)
        digest = canonical_hash(payload)
        with self._lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT content_hash FROM research_journal WHERE stream=? AND event_key=?",
                (stream, key),
            ).fetchone()
            count = self.connection.execute(
                "SELECT COUNT(*) FROM research_journal WHERE stream=?", (stream,)
            ).fetchone()[0]
            if existing is None and expected_count is not None and count != expected_count:
                raise ValueError("journal_concurrent_write")
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO research_journal "
                "(stream,event_key,content_hash,payload) VALUES (?,?,?,?)",
                (stream, key, digest, encoded),
            )
            row = self.connection.execute(
                "SELECT content_hash FROM research_journal WHERE stream=? AND event_key=?",
                (stream, key),
            ).fetchone()
            if row is None or row[0] != digest:
                raise ValueError("journal_identity_conflict")
            return cursor.rowcount == 1

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT content_hash,payload FROM research_journal WHERE stream=? "
                "ORDER BY sequence",
                (stream,),
            ).fetchall()
            return _verified(rows)

    def iter_read(self, stream: str) -> Iterator[dict[str, Any]]:
        """Verify bounded pages through a fixed high-water mark in journal order."""
        for _, value in self.iter_rows(stream):
            yield value

    def iter_rows(self, stream: str, *, after: int = 0) -> Iterator[tuple[int, dict[str, Any]]]:
        """Verified (sequence, payload) pairs strictly after `after`, in journal order."""
        with self._lock:
            end = self.connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM research_journal WHERE stream=?",
                (stream,),
            ).fetchone()[0]
        while after < end:
            with self._lock:
                rows = self.connection.execute(
                    "SELECT sequence,content_hash,payload FROM research_journal NOT INDEXED "
                    "WHERE stream=? AND sequence>? AND sequence<=? "
                    "ORDER BY sequence LIMIT 32",
                    (stream, after, end),
                ).fetchall()
            if not rows:
                break
            for sequence, digest, payload in rows:
                yield sequence, _verify(digest, payload)
                after = sequence

    def row_identity(self, stream: str, sequence: int) -> tuple[str, str] | None:
        """(event_key, content_hash) of one exact row, used to anchor recovery checkpoints."""
        with self._lock:
            row = self.connection.execute(
                "SELECT event_key,content_hash FROM research_journal WHERE sequence=? AND stream=?",
                (sequence, stream),
            ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def count_through(self, stream: str, sequence: int) -> int:
        with self._lock:
            return int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM research_journal WHERE stream=? AND sequence<=?",
                    (stream, sequence),
                ).fetchone()[0]
            )

    def sequence_of(self, stream: str, key: str) -> int | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT sequence FROM research_journal WHERE stream=? AND event_key=?",
                (stream, key),
            ).fetchone()
        return None if row is None else int(row[0])

    def backup(self, destination: Path) -> None:
        if destination.exists():
            raise ValueError("backup_destination_exists")
        with self._lock:
            target = sqlite3.connect(str(destination))
            try:
                self.connection.backup(target)
            finally:
                target.close()

    def close(self) -> None:
        self.connection.close()


def _verify(digest: str, payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    # JSON-decoded values already contain only canonical primitive/container types.
    # sort_keys handles nested dictionaries without a second recursive Python copy.
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(encoded.encode()).hexdigest() != digest:
        raise ValueError("journal_corrupt")
    return value  # type: ignore[no-any-return]


def _verified(rows: Any) -> tuple[dict[str, Any], ...]:
    return tuple(_verify(digest, payload) for digest, payload in rows)


class PostgresJournal:
    backend = "postgresql"

    def __init__(self, conninfo: str) -> None:
        psycopg = importlib.import_module("psycopg")
        self.connection: Any = psycopg.connect(conninfo, autocommit=True, connect_timeout=5)
        self._lock = RLock()
        self.connection.execute("""CREATE TABLE IF NOT EXISTS research_journal (
            sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            stream TEXT NOT NULL, event_key TEXT NOT NULL, content_hash TEXT NOT NULL,
            payload TEXT NOT NULL, UNIQUE(stream, event_key))""")

    def append(
        self, stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        digest = canonical_hash(payload)
        encoded = json.dumps(canonical_serialize(payload), sort_keys=True)
        with self._lock, self.connection.transaction():
            self.connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (stream,))
            existing = self.connection.execute(
                "SELECT content_hash FROM research_journal WHERE stream=%s AND event_key=%s",
                (stream, key),
            ).fetchone()
            count = self.connection.execute(
                "SELECT COUNT(*) FROM research_journal WHERE stream=%s", (stream,)
            ).fetchone()[0]
            if existing is None and expected_count is not None and count != expected_count:
                raise ValueError("journal_concurrent_write")
            row = self.connection.execute(
                "INSERT INTO research_journal (stream,event_key,content_hash,payload) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (stream,event_key) DO NOTHING "
                "RETURNING sequence",
                (stream, key, digest, encoded),
            ).fetchone()
            stored = self.connection.execute(
                "SELECT content_hash FROM research_journal WHERE stream=%s AND event_key=%s",
                (stream, key),
            ).fetchone()
            if stored is None or stored[0] != digest:
                raise ValueError("journal_identity_conflict")
            return row is not None

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT content_hash,payload FROM research_journal WHERE stream=%s "
                "ORDER BY sequence",
                (stream,),
            ).fetchall()
            return _verified(rows)

    def iter_read(self, stream: str) -> Iterator[dict[str, Any]]:
        """Verify bounded pages through a fixed high-water mark in journal order."""
        with self._lock:
            end = self.connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM research_journal WHERE stream=%s",
                (stream,),
            ).fetchone()[0]
        after = 0
        while after < end:
            with self._lock:
                rows = self.connection.execute(
                    "SELECT sequence,content_hash,payload FROM research_journal "
                    "WHERE stream=%s AND sequence>%s AND sequence<=%s "
                    "ORDER BY sequence LIMIT 32",
                    (stream, after, end),
                ).fetchall()
            if not rows:
                break
            for sequence, digest, payload in rows:
                yield _verify(digest, payload)
                after = sequence

    def close(self) -> None:
        self.connection.close()
