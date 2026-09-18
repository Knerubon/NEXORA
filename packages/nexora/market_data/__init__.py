"""Market-data normalization, persistence, adapter, and replay contracts."""

from nexora.market_data.adapters import (
    FakeMarketDataAdapter,
    MarketDataAdapter,
    MarketDataError,
    Mt5ReadOnlyMarketDataAdapter,
)
from nexora.market_data.fixtures import (
    canonical_bar_fixtures,
    canonical_tick_fixtures,
    gap_backfill_fixtures,
    invalid_tick_fixtures,
)
from nexora.market_data.models import (
    MarketBar,
    MarketDataPolicy,
    MarketTick,
    NormalizedPriceEvent,
    PriceSource,
    SchemaVersion,
)
from nexora.market_data.policy import MarketDataNormalizer, StreamState
from nexora.market_data.quality import (
    MarketDataQualityMonitor,
    QualityConfig,
    QualityCounters,
    QualitySnapshot,
    QualityStatus,
)
from nexora.market_data.quality_repository import QualitySnapshotStore
from nexora.market_data.replay import ReplayReader, semantic_fingerprint
from nexora.market_data.repository import SQLiteMarketDataRepository, StoreResult

__all__ = [
    "FakeMarketDataAdapter",
    "MarketBar",
    "MarketDataAdapter",
    "MarketDataError",
    "MarketDataNormalizer",
    "MarketDataPolicy",
    "MarketTick",
    "Mt5ReadOnlyMarketDataAdapter",
    "NormalizedPriceEvent",
    "PriceSource",
    "QualityConfig",
    "QualityCounters",
    "QualitySnapshot",
    "QualityStatus",
    "QualitySnapshotStore",
    "ReplayReader",
    "SchemaVersion",
    "SQLiteMarketDataRepository",
    "StoreResult",
    "StreamState",
    "MarketDataQualityMonitor",
    "canonical_bar_fixtures",
    "canonical_tick_fixtures",
    "gap_backfill_fixtures",
    "invalid_tick_fixtures",
    "semantic_fingerprint",
]
