"""Exploratory Dukascopy CSV loader: window filter and fail-closed rows."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.backtest.baseline_v1.dukascopy_csv import DukascopyCsvError, load_ticks

HEADER = "timestamp,ask,bid,ask_volume,bid_volume\n"


def _write(path: Path, rows: list[str]) -> Path:
    with gzip.open(path, "wt", encoding="ascii", newline="") as handle:
        handle.write(HEADER + "".join(r + "\n" for r in rows))
    return path


def test_window_is_half_open_and_quotes_preserved(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "w.csv.gz",
        [
            "2024-01-08 00:00:00.000,2044.585,2044.235,0.1,0.1",
            "2024-01-08 00:00:01.000,2044.555,2044.205,0.1,0.1",
            "2024-01-08 00:00:02.000,2044.600,2044.300,0.1,0.1",
        ],
    )
    events = load_ticks(
        [path],
        symbol="XAUUSD",
        start=datetime(2024, 1, 8, 0, 0, 1, tzinfo=UTC),
        end=datetime(2024, 1, 8, 0, 0, 2, tzinfo=UTC),
    )
    assert len(events) == 1
    event = events[0]
    assert event.bid == Decimal("2044.205") and event.ask == Decimal("2044.555")
    assert event.price == event.bid and event.kind == "tick"
    assert event.received_at > event.event_time


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        (["2024-01-08 00:00:00.000,2044.0,2044.5,1,1"], "invalid_quote"),
        (
            ["2024-01-08 00:00:02.000,2045,2044,1,1", "2024-01-08 00:00:01.000,2045,2044,1,1"],
            "out_of_order",
        ),
    ],
)
def test_bad_rows_fail_closed(tmp_path: Path, rows: list[str], code: str) -> None:
    path = _write(tmp_path / "w.csv.gz", rows)
    with pytest.raises(DukascopyCsvError, match=code):
        load_ticks(
            [path],
            symbol="XAUUSD",
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 2, 1, tzinfo=UTC),
        )
