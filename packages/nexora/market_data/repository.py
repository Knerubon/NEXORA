"""Normalized and raw market-data persistence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from nexora.market_data.adapters import MarketObservation
from nexora.market_data.models import (
    MarketTick,
    NormalizedPriceEvent,
    ObservationKind,
    PriceSource,
)


@dataclass(frozen=True, slots=True)
class StoreResult:
    raw_id: int
    normalized_inserted: bool


def _serialize_decimal(value: object | None) -> str | None:
    if value is None:
        return None
    return format(value, "f") if hasattr(value, "__format__") else str(value)


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _observation_payload(observation: MarketObservation) -> str:
    if isinstance(observation, MarketTick):
        payload = {
            "kind": "tick",
            "source": observation.source,
            "symbol": observation.symbol,
            "event_time": _serialize_datetime(observation.event_time),
            "received_at": _serialize_datetime(observation.received_at),
            "sequence": observation.sequence,
            "digits": observation.digits,
            "bid": _serialize_decimal(observation.bid),
            "ask": _serialize_decimal(observation.ask),
            "last": _serialize_decimal(observation.last),
            "volume": _serialize_decimal(observation.volume),
            "source_event_id": observation.source_event_id,
        }
    else:
        payload = {
            "kind": "bar",
            "source": observation.source,
            "symbol": observation.symbol,
            "event_time": _serialize_datetime(observation.event_time),
            "received_at": _serialize_datetime(observation.received_at),
            "sequence": observation.sequence,
            "digits": observation.digits,
            "open_price": _serialize_decimal(observation.open_price),
            "high": _serialize_decimal(observation.high),
            "low": _serialize_decimal(observation.low),
            "close": _serialize_decimal(observation.close),
            "volume": _serialize_decimal(observation.volume),
            "source_event_id": observation.source_event_id,
        }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class SQLiteMarketDataRepository:
    def __init__(self, path_or_connection: str | Path | sqlite3.Connection) -> None:
        if isinstance(path_or_connection, sqlite3.Connection):
            self.connection = path_or_connection
            self._owns_connection = False
        else:
            self.connection = sqlite3.connect(str(path_or_connection))
            self._owns_connection = True
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> SQLiteMarketDataRepository:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_data_raw_events (
                raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                source_sequence INTEGER NOT NULL,
                source_order INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                received_at TEXT NOT NULL,
                price_source TEXT NOT NULL,
                units TEXT NOT NULL,
                precision INTEGER NOT NULL,
                price TEXT NOT NULL,
                open_price TEXT,
                high_price TEXT,
                low_price TEXT,
                close_price TEXT,
                bid_price TEXT,
                ask_price TEXT,
                last_price TEXT,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                is_out_of_order INTEGER NOT NULL DEFAULT 0,
                is_gap INTEGER NOT NULL DEFAULT 0,
                gap_from_sequence INTEGER,
                normalized_identity_key TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_data_normalized_events (
                normalized_id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_id INTEGER NOT NULL REFERENCES market_data_raw_events(raw_id)
                    ON DELETE RESTRICT,
                schema_version INTEGER NOT NULL,
                identity_key TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                source_sequence INTEGER NOT NULL,
                source_order INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                received_at TEXT NOT NULL,
                price_source TEXT NOT NULL,
                units TEXT NOT NULL,
                precision INTEGER NOT NULL,
                price TEXT NOT NULL,
                open_price TEXT,
                high_price TEXT,
                low_price TEXT,
                close_price TEXT,
                bid_price TEXT,
                ask_price TEXT,
                last_price TEXT,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                is_out_of_order INTEGER NOT NULL DEFAULT 0,
                is_gap INTEGER NOT NULL DEFAULT 0,
                gap_from_sequence INTEGER
            );
            """
        )
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_market_data_raw_lookup
                ON market_data_raw_events (
                    source, symbol, source_sequence, event_time, received_at, raw_id
                );

            CREATE INDEX IF NOT EXISTS idx_market_data_normalized_lookup
                ON market_data_normalized_events (
                    source, symbol, event_time, received_at, source_sequence,
                    source_order, normalized_id
                );
            """
        )

    def record(self, observation: MarketObservation, event: NormalizedPriceEvent) -> StoreResult:
        raw_id = self._insert_raw(observation, event)
        normalized_inserted = False
        if not event.is_duplicate:
            normalized_inserted = self._insert_normalized(raw_id, event)
        return StoreResult(raw_id=raw_id, normalized_inserted=normalized_inserted)

    def _insert_raw(self, observation: MarketObservation, event: NormalizedPriceEvent) -> int:
        payload_json = _observation_payload(observation)
        open_price: str | None
        high_price: str | None
        low_price: str | None
        close_price: str | None
        bid_price: str | None
        ask_price: str | None
        last_price: str | None
        if isinstance(observation, MarketTick):
            open_price = high_price = low_price = close_price = None
            bid_price = format(observation.bid, "f")
            ask_price = format(observation.ask, "f")
            last_price = format(observation.last, "f") if observation.last is not None else None
        else:
            open_price = format(observation.open_price, "f")
            high_price = format(observation.high, "f")
            low_price = format(observation.low, "f")
            close_price = format(observation.close, "f")
            bid_price = ask_price = last_price = None
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO market_data_raw_events (
                    source, symbol, event_kind, source_event_id, source_sequence, source_order,
                    event_time, received_at, price_source, units, precision, price,
                    open_price, high_price, low_price, close_price, bid_price, ask_price,
                    last_price, is_duplicate, is_out_of_order, is_gap, gap_from_sequence,
                    normalized_identity_key,
                    payload_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?
                )
                """,
                (
                    event.source,
                    event.symbol,
                    event.kind,
                    event.source_event_id,
                    event.source_sequence,
                    event.source_order,
                    event.event_time.isoformat(),
                    event.received_at.isoformat(),
                    event.price_source,
                    event.units,
                    event.precision,
                    format(event.price, "f"),
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    bid_price,
                    ask_price,
                    last_price,
                    int(event.is_duplicate),
                    int(event.is_out_of_order),
                    int(event.is_gap),
                    event.gap_from_sequence,
                    event.identity_key,
                    payload_json,
                ),
            )
        lastrowid = cursor.lastrowid
        if lastrowid is None:
            raise RuntimeError("raw_id_missing")
        return int(lastrowid)

    def _insert_normalized(self, raw_id: int, event: NormalizedPriceEvent) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO market_data_normalized_events (
                    raw_id, schema_version, identity_key, source, symbol, event_kind,
                    source_event_id, source_sequence, source_order, event_time, received_at,
                    price_source, units, precision, price, open_price, high_price, low_price,
                    close_price, bid_price, ask_price, last_price, is_duplicate, is_out_of_order,
                    is_gap, gap_from_sequence
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?
                )
                ON CONFLICT(identity_key) DO NOTHING
                """,
                (
                    raw_id,
                    event.schema_version,
                    event.identity_key,
                    event.source,
                    event.symbol,
                    event.kind,
                    event.source_event_id,
                    event.source_sequence,
                    event.source_order,
                    event.event_time.isoformat(),
                    event.received_at.isoformat(),
                    event.price_source,
                    event.units,
                    event.precision,
                    format(event.price, "f"),
                    format(event.open_price, "f") if event.open_price is not None else None,
                    format(event.high, "f") if event.high is not None else None,
                    format(event.low, "f") if event.low is not None else None,
                    format(event.close, "f") if event.close is not None else None,
                    format(event.bid, "f") if event.bid is not None else None,
                    format(event.ask, "f") if event.ask is not None else None,
                    format(event.last, "f") if event.last is not None else None,
                    int(event.is_duplicate),
                    int(event.is_out_of_order),
                    int(event.is_gap),
                    event.gap_from_sequence,
                ),
            )
        return cursor.rowcount > 0

    def last_sequence(self, source: str, symbol: str) -> int | None:
        row = self.connection.execute(
            """
            SELECT MAX(source_sequence) AS last_sequence
            FROM market_data_normalized_events
            WHERE source = ? AND symbol = ?
            """,
            (source, symbol),
        ).fetchone()
        if row is None or row["last_sequence"] is None:
            return None
        return int(row["last_sequence"])

    def iter_normalized(
        self, *, source: str | None = None, symbol: str | None = None
    ) -> Iterable[NormalizedPriceEvent]:
        query = [
            """
            SELECT schema_version, identity_key, source, symbol, event_kind, source_event_id,
                   source_sequence, source_order, event_time, received_at, price_source, units,
                   precision, price, open_price, high_price, low_price, close_price, bid_price,
                   ask_price, last_price, is_duplicate, is_out_of_order, is_gap, gap_from_sequence
            FROM market_data_normalized_events
            """
        ]
        params: list[object] = []
        clauses: list[str] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if clauses:
            query.append("WHERE " + " AND ".join(clauses))
        query.append(
            """
            ORDER BY event_time, received_at, source_sequence, source_order, source_event_id,
                     identity_key, normalized_id
            """
        )
        rows = self.connection.execute(" ".join(query), params).fetchall()
        for row in rows:
            yield _row_to_event(row)

    def iter_raw(
        self, *, source: str | None = None, symbol: str | None = None
    ) -> Iterable[sqlite3.Row]:
        query = ["SELECT * FROM market_data_raw_events"]
        params: list[object] = []
        clauses: list[str] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if clauses:
            query.append("WHERE " + " AND ".join(clauses))
        query.append("ORDER BY raw_id")
        return self.connection.execute(" ".join(query), params).fetchall()


def _row_to_event(row: sqlite3.Row) -> NormalizedPriceEvent:
    from datetime import datetime
    from decimal import Decimal

    event_kind = cast(ObservationKind, str(row["event_kind"]))
    price_source = cast(PriceSource, str(row["price_source"]))
    gap_from_sequence_value = row["gap_from_sequence"]
    gap_from_sequence = (
        int(gap_from_sequence_value) if gap_from_sequence_value is not None else None
    )
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=str(row["identity_key"]),
        source=str(row["source"]),
        symbol=str(row["symbol"]),
        kind=event_kind,
        event_time=datetime.fromisoformat(str(row["event_time"])),
        received_at=datetime.fromisoformat(str(row["received_at"])),
        source_sequence=int(row["source_sequence"]),
        source_order=int(row["source_order"]),
        source_event_id=str(row["source_event_id"]),
        price_source=price_source,
        units=str(row["units"]),
        precision=int(row["precision"]),
        price=Decimal(str(row["price"])),
        open_price=Decimal(str(row["open_price"])) if row["open_price"] is not None else None,
        high=Decimal(str(row["high_price"])) if row["high_price"] is not None else None,
        low=Decimal(str(row["low_price"])) if row["low_price"] is not None else None,
        close=Decimal(str(row["close_price"])) if row["close_price"] is not None else None,
        bid=Decimal(str(row["bid_price"])) if row["bid_price"] is not None else None,
        ask=Decimal(str(row["ask_price"])) if row["ask_price"] is not None else None,
        last=Decimal(str(row["last_price"])) if row["last_price"] is not None else None,
        is_duplicate=bool(int(row["is_duplicate"])),
        is_out_of_order=bool(int(row["is_out_of_order"])),
        is_gap=bool(int(row["is_gap"])),
        gap_from_sequence=gap_from_sequence,
    )
