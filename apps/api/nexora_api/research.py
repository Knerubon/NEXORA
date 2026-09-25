"""Runtime composition; configuration and storage are explicit local inputs."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from nexora.artifacts import canonical_hash, decode
from nexora.backtest.models import BacktestConfig
from nexora.market_data.models import NormalizedPriceEvent, PriceSource
from nexora.research.checkpoint import CheckpointStore
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import Journal, PostgresJournal, SQLiteJournal

from nexora_api.environment import Environment, validate_postgres_identity
from nexora_api.quotes import Quote


def configured_journal() -> Journal:
    environment = Environment.resolve()
    environment.prepare()
    if conninfo := os.environ.get("NEXORA_POSTGRES_DSN"):
        validate_postgres_identity(conninfo, environment.name)
        try:
            return PostgresJournal(conninfo)
        except Exception:
            raise RuntimeError("postgres_unavailable") from None
    path = environment.storage
    path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteJournal(path)


def configured_checkpoints() -> CheckpointStore | None:
    """Recovery checkpoints live only in the resolved environment's own directory (ADR-022)."""
    setting = os.environ.get("NEXORA_RESEARCH_CHECKPOINTS", "on")
    if setting not in {"on", "off"}:
        raise ValueError("invalid_research_checkpoints")
    if setting == "off":
        return None
    environment = Environment.resolve()
    return CheckpointStore(environment.checkpoints, environment=environment.name)


def configured_checkpoint_max_age() -> float | None:
    """Optional age trigger in seconds (ADR-029 H3). Unset means off; no default is decided."""
    setting = os.environ.get("NEXORA_RESEARCH_CHECKPOINT_MAX_AGE_SECONDS", "").strip()
    if not setting:
        return None
    try:
        seconds = float(setting)
    except ValueError:
        raise ValueError("invalid_research_checkpoint_max_age") from None
    if not 0 < seconds < float("inf"):
        raise ValueError("invalid_research_checkpoint_max_age")
    return seconds


def configured_runtime(journal: Journal) -> ResearchRuntime | None:
    filename = os.environ.get("NEXORA_RESEARCH_CONFIG")
    if not filename:
        return None
    config = decode(RuntimeConfig, json.loads(Path(filename).read_text(encoding="utf-8")))
    return ResearchRuntime(
        config,
        journal,
        checkpoints=configured_checkpoints(),
        checkpoint_max_age=configured_checkpoint_max_age(),
    )


def configured_backtests() -> dict[str, BacktestConfig]:
    directory = os.environ.get("NEXORA_BACKTEST_CONFIG_DIR")
    if not directory:
        return {}
    return {
        p.stem: decode(BacktestConfig, json.loads(p.read_text(encoding="utf-8")))
        for p in Path(directory).glob("*.json")
    }


def observe_quote(
    runtime: ResearchRuntime,
    quote: Quote,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Quote polling is observation with unknown coverage, never full tick capture."""
    try:
        # Reject invalid chronology before ingest's expensive recovery path.
        # Keep the source timestamp intact; a later poll may become eligible.
        if quote.event_time > quote.received_at:
            return
        events = runtime.events()
        if events and quote.event_time < events[-1].event_time:
            return
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
        runtime.ingest(
            event,
            completeness="unknown",
            observation_metadata={"quote": quote.model_dump(mode="json"), "feed": metadata},
        )
    except Exception:
        runtime.error = "research_processing_failed"
