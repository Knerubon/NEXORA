"""Runtime composition; configuration and storage are explicit local inputs."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import cast

from nexora.artifacts import canonical_hash, decode
from nexora.backtest.models import BacktestConfig
from nexora.market_data.models import NormalizedPriceEvent, PriceSource
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import Journal, PostgresJournal, SQLiteJournal

from nexora_api.quotes import Quote


def configured_journal() -> Journal:
    if conninfo := os.environ.get("NEXORA_POSTGRES_DSN"):
        try:
            return PostgresJournal(conninfo)
        except Exception:
            raise RuntimeError("postgres_unavailable") from None
    path = Path(os.environ.get("NEXORA_JOURNAL_PATH", "data/research.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteJournal(path)


def configured_runtime(journal: Journal) -> ResearchRuntime | None:
    filename = os.environ.get("NEXORA_RESEARCH_CONFIG")
    if not filename:
        return None
    config = decode(RuntimeConfig, json.loads(Path(filename).read_text(encoding="utf-8")))
    return ResearchRuntime(config, journal)


def configured_backtests() -> dict[str, BacktestConfig]:
    directory = os.environ.get("NEXORA_BACKTEST_CONFIG_DIR")
    if not directory:
        return {}
    return {
        p.stem: decode(BacktestConfig, json.loads(p.read_text(encoding="utf-8")))
        for p in Path(directory).glob("*.json")
    }


def observe_quote(runtime: ResearchRuntime, quote: Quote) -> None:
    """Quote polling is observation with unknown coverage, never full tick capture."""
    try:
        events = runtime.events()
        identity = "quote:" + canonical_hash(
            (
                quote.symbol,
                quote.event_time,
                quote.bid,
                quote.ask,
                quote.raw_event_time,
                quote.time_offset_seconds,
            )
        )
        if any(event.identity_key == identity for event in events):
            return
        source = runtime.config.pipeline.resolutions[0].pnf.price_source
        if source not in {"bid", "ask", "mid"}:
            raise ValueError("quote_price_source_unsupported")
        bid, ask = Decimal(quote.bid), Decimal(quote.ask)
        price = bid if source == "bid" else ask if source == "ask" else (bid + ask) / 2
        sequence = events[-1].source_sequence + 1 if events else 1
        event = NormalizedPriceEvent(
            schema_version=1,
            identity_key=identity,
            source=f"MT5-quote-observation:time-offset={quote.time_offset_seconds}",
            symbol=quote.symbol,
            kind="tick",
            event_time=quote.event_time,
            received_at=quote.received_at,
            source_sequence=sequence,
            source_order=sequence,
            source_event_id=(
                f"{identity}:raw-time={quote.raw_event_time or quote.event_time}"
                f":offset={quote.time_offset_seconds}"
            ),
            price_source=cast(PriceSource, source),
            units=runtime.config.units,
            precision=quote.digits,
            price=price,
            bid=bid,
            ask=ask,
        )
        runtime.ingest(event, completeness="unknown")
    except Exception:
        runtime.error = "research_processing_failed"
