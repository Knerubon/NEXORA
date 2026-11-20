"""EXPLORATORY loader: Dukascopy-derived XAUUSD tick CSV -> ``NormalizedPriceEvent``.

Reads the weekly ``timestamp,ask,bid,ask_volume,bid_volume`` gzip CSVs published by the
public ``Touhid-HZ/xauusd-tick-data-<year>`` repositories (Dukascopy Bank SA public feed,
UTC millisecond timestamps). Only rows with ``start <= timestamp < end`` are kept, so a
caller can never pull rows from outside the requested window, and a file is never opened
unless the caller names it.

This is NOT an approved development source (decision record D6 is still VALUE REQUIRED).
Output is for exploratory runs only and is never governance evidence.
"""

from __future__ import annotations

import csv
import gzip
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from nexora.market_data.models import NormalizedPriceEvent

SOURCE = "dukascopy-public-via-touhid-hz"
EXPECTED_HEADER = ["timestamp", "ask", "bid", "ask_volume", "bid_volume"]


class DukascopyCsvError(ValueError):
    pass


def _parse_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)


def load_ticks(
    paths: Iterable[Path],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    price_source: str = "bid",
    receive_latency: timedelta = timedelta(milliseconds=150),
    precision: int = 3,
) -> tuple[NormalizedPriceEvent, ...]:
    """Return canonical tick events in ``[start, end)``; fail closed on bad rows.

    ``received_at = event_time + receive_latency`` (an assumption: the feed has no receive
    time). Rows where ``bid > ask`` or a price is not positive raise, rather than being
    dropped silently. Out-of-order timestamps raise.
    """
    if price_source not in ("bid", "ask"):
        raise DukascopyCsvError("unsupported_price_source")
    events: list[NormalizedPriceEvent] = []
    previous: datetime | None = None
    for path in paths:
        with gzip.open(path, "rt", encoding="ascii", newline="") as handle:
            reader = csv.reader(handle)
            if next(reader, None) != EXPECTED_HEADER:
                raise DukascopyCsvError(f"unexpected_header:{path.name}")
            for line_no, row in enumerate(reader, start=2):
                at = _parse_time(row[0])
                if at < start or at >= end:
                    continue
                if previous is not None and at < previous:
                    raise DukascopyCsvError(f"out_of_order:{path.name}:{line_no}")
                previous = at
                ask = Decimal(row[1])
                bid = Decimal(row[2])
                if not (Decimal(0) < bid <= ask):
                    raise DukascopyCsvError(f"invalid_quote:{path.name}:{line_no}")
                seq = len(events) + 1
                key = f"{SOURCE}:{symbol}:{seq}"
                events.append(
                    NormalizedPriceEvent(
                        1,
                        key,
                        SOURCE,
                        symbol,
                        "tick",
                        at,
                        at + receive_latency,
                        seq,
                        seq,
                        key,
                        price_source,  # type: ignore[arg-type]
                        "USD/oz",
                        precision,
                        bid if price_source == "bid" else ask,
                        bid=bid,
                        ask=ask,
                    )
                )
    if not events:
        raise DukascopyCsvError("no_rows_in_window")
    return tuple(events)
