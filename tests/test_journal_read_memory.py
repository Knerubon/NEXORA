"""Verified journal reads keep only a small page of raw row text alive (REPLAY-MEM-1)."""

from __future__ import annotations

import tracemalloc
from pathlib import Path

from nexora.storage import _PAGE_ROWS, SQLiteJournal


def test_paged_rows_keep_order_resume_point_and_fixed_boundary(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "paged.sqlite")
    try:
        count = 3 * _PAGE_ROWS + 5
        for i in range(count):
            journal.append("a", str(i), {"index": i})
            journal.append("other", str(i), {"index": -i})
        rows = list(journal.iter_rows("a"))
        assert [value for _, value in rows] == list(journal.read("a"))
        assert [value["index"] for _, value in rows] == list(range(count))
        sequences = [sequence for sequence, _ in rows]
        assert sequences == sorted(sequences) and len(set(sequences)) == count
        resumed = journal.iter_rows("a", after=sequences[_PAGE_ROWS])
        assert list(resumed) == rows[_PAGE_ROWS + 1 :]
        reader = journal.iter_rows("a")
        assert next(reader) == rows[0]
        journal.append("a", "later", {"index": count})
        assert list(reader) == rows[1:]
    finally:
        journal.close()


def test_reading_large_rows_keeps_one_small_page_of_raw_text(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "large.sqlite")
    size = 256 * 1024
    try:
        # Forty rows span two pages of the former 32-row reader, which held both pages'
        # raw text (~40 rows) while fetching the second.
        for i in range(40):
            journal.append("a", str(i), {"index": i, "blob": str(i % 10) * size})
        tracemalloc.start()
        try:
            start = tracemalloc.get_traced_memory()[0]
            read = 0
            for _, value in journal.iter_rows("a"):
                assert len(value["blob"]) == size
                read += 1
            peak = tracemalloc.get_traced_memory()[1] - start
        finally:
            tracemalloc.stop()
        assert read == 40
        # One page of raw text plus one row being verified (text, decoded, re-encoded)
        # and the value the caller still holds.
        assert peak < (_PAGE_ROWS + 8) * size
    finally:
        journal.close()
