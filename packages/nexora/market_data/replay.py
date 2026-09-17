"""Replay helpers for normalized market-data events."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_data.repository import SQLiteMarketDataRepository


def semantic_fingerprint(event: NormalizedPriceEvent) -> tuple[str, ...]:
    return event.semantic_key()


@dataclass(slots=True)
class ReplayReader:
    repository: SQLiteMarketDataRepository

    def iter_events(
        self, *, source: str | None = None, symbol: str | None = None
    ) -> Iterator[NormalizedPriceEvent]:
        yield from self.repository.iter_normalized(source=source, symbol=symbol)

    def fingerprints(
        self, *, source: str | None = None, symbol: str | None = None
    ) -> list[tuple[str, ...]]:
        return [
            semantic_fingerprint(event)
            for event in self.iter_events(source=source, symbol=symbol)
        ]

    def compare(
        self, left: Iterable[NormalizedPriceEvent], right: Iterable[NormalizedPriceEvent]
    ) -> bool:
        return [semantic_fingerprint(event) for event in left] == [
            semantic_fingerprint(event) for event in right
        ]
