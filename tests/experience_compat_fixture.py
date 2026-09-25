"""Golden journal written by the pre-Trendline Experience writer (commit 9016004).

The fixture is plain journal rows, so tests replay the exact bytes that the old
writer committed instead of an emulation of it. It is synthetic data only.

Regenerate (never against a real journal) from a checkout of the old writer:

    git archive 9016004 packages tests | tar -x -C <old>
    cd <old>
    PYTHONPATH="<old>/packages;<old>" python <this file> <output.json>

The generator only uses APIs that exist unchanged in both versions.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).with_name("fixtures") / "experience_journal_9016004.json"
WRITER_COMMIT = "9016004"
EVENT_COUNT = 16
_PATTERN = (100, 104, 99, 106, 98, 108, 102, 110, 104, 112)

Row = tuple[int, str, str, str, str]


def fixture_events(count: int = EVENT_COUNT, *, first: int = 1) -> list[Any]:
    """Deterministic zig-zag bars with enough reversals for signals and horizons."""
    from nexora.market_data.models import NormalizedPriceEvent

    start = datetime(2026, 3, 1, 9, tzinfo=UTC)
    result = []
    for i in range(first, first + count):
        price = Decimal(_PATTERN[(i - 1) % 10] + 3 * ((i - 1) // 10))
        at = start + timedelta(minutes=i)
        result.append(
            NormalizedPriceEvent(
                1,
                f"event:{i}",
                "recorded-test",
                "XAUUSD",
                "bar",
                at,
                at,
                i,
                i,
                f"event:{i}",
                "close",
                "USD/oz",
                1,
                price,
                close=price,
            )
        )
    return result


def dump_rows(path: Path) -> list[Row]:
    connection = sqlite3.connect(path)
    try:
        return [
            (int(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]))
            for row in connection.execute(
                "SELECT sequence,stream,event_key,content_hash,payload "
                "FROM research_journal ORDER BY sequence"
            )
        ]
    finally:
        connection.close()


def load_rows(path: Path, rows: list[Row]) -> None:
    """Insert committed rows verbatim, preserving their sequence numbers and bytes."""
    from nexora.storage import SQLiteJournal

    SQLiteJournal(path).close()
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.executemany(
                "INSERT INTO research_journal (sequence,stream,event_key,content_hash,payload) "
                "VALUES (?,?,?,?,?)",
                rows,
            )
    finally:
        connection.close()


def load_fixture() -> list[Row]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in data["rows"]]


def generate(output: Path) -> None:
    from nexora.research.runtime import ResearchRuntime, RuntimeConfig
    from nexora.storage import SQLiteJournal

    from tests.test_readiness_regressions import pipeline_config

    with tempfile.TemporaryDirectory(prefix="nexora-compat-") as directory:
        database = Path(directory) / "journal.sqlite"
        journal = SQLiteJournal(database)
        runtime = ResearchRuntime(RuntimeConfig(pipeline_config(), "USD/oz"), journal)
        for event in fixture_events():
            runtime.ingest(event, completeness="complete")
        journal.close()
        rows = dump_rows(database)
    document = {"writer_commit": WRITER_COMMIT, "event_count": EVENT_COUNT, "rows": rows}
    output.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
