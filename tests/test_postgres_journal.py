"""Real PostgreSQL integration; explicit skip when isolated test service is absent."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from nexora.storage import PostgresJournal


def test_postgres_transaction_durability_and_conflict() -> None:
    conninfo = os.environ.get("NEXORA_TEST_POSTGRES_DSN")
    if not conninfo:
        pytest.skip("isolated PostgreSQL test service not configured")
    stream = f"integration:{uuid4()}"
    writer = PostgresJournal(conninfo)
    try:
        assert writer.append(stream, "one", {"value": "100.01"}, expected_count=0)
        assert not writer.append(stream, "one", {"value": "100.01"})
        with pytest.raises(ValueError, match="identity"):
            writer.append(stream, "one", {"value": "999"})
        reader = PostgresJournal(conninfo)
        try:
            assert reader.read(stream) == ({"value": "100.01"},)
            with pytest.raises(ValueError, match="concurrent"):
                reader.append(stream, "two", {"value": "2"}, expected_count=0)
        finally:
            reader.close()
    finally:
        # Unique test namespace only; never touch existing data.
        writer.connection.execute("DELETE FROM research_journal WHERE stream=%s", (stream,))
        writer.close()
