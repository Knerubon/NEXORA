"""Sidecar market-data quality and observability contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from nexora.market_data.models import NormalizedPriceEvent

QualityStatus = Literal[
    "live",
    "stale",
    "clock_skew",
    "disconnected",
    "unavailable",
    "error",
    "market_closed",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class QualityConfig:
    symbol: str
    freshness_threshold_seconds: int = 10
    clock_skew_threshold_seconds: int = 5
    latency_threshold_ms: int = 3000
    session_start_hour_utc: int = 0
    session_end_hour_utc: int = 24
    version: str = "dq1-v1"

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("missing_symbol")
        if self.freshness_threshold_seconds < 1:
            raise ValueError("invalid_freshness_threshold")
        if self.clock_skew_threshold_seconds < 1:
            raise ValueError("invalid_clock_skew_threshold")
        if self.latency_threshold_ms < 1:
            raise ValueError("invalid_latency_threshold")
        if not (0 <= self.session_start_hour_utc <= 23):
            raise ValueError("invalid_session_start")
        if not (1 <= self.session_end_hour_utc <= 24):
            raise ValueError("invalid_session_end")
        if self.session_start_hour_utc >= self.session_end_hour_utc:
            raise ValueError("invalid_session_window")
        if not self.version:
            raise ValueError("missing_version")


@dataclass(frozen=True, slots=True)
class QualityCounters:
    observed: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    gaps: int = 0
    backfills: int = 0
    reconnects: int = 0
    disconnects: int = 0
    rejected: int = 0


@dataclass(frozen=True, slots=True)
class QualitySnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    observed_at: datetime
    status: QualityStatus
    code: str
    age_seconds: float | None
    latency_ms: int | None
    completeness: Literal["complete", "partial", "unknown"]
    counters: QualityCounters
    config_version: str


class MarketDataQualityMonitor:
    """Tracks sidecar quality state without mutating normalized events."""

    def __init__(self, config: QualityConfig) -> None:
        self._config = config
        self._sequence = 0
        self._last_source_sequence: int | None = None
        self._last_status: QualityStatus = "unknown"
        self._counters = QualityCounters()
        self._history: list[QualitySnapshot] = [
            QualitySnapshot(
                schema_version=1,
                symbol=config.symbol,
                sequence=0,
                observed_at=datetime.now(UTC),
                status="unknown",
                code="warming_up",
                age_seconds=None,
                latency_ms=None,
                completeness="unknown",
                counters=self._counters,
                config_version=config.version,
            )
        ]

    def observe_event(
        self,
        event: NormalizedPriceEvent,
        *,
        observed_at: datetime | None = None,
    ) -> QualitySnapshot:
        when = (observed_at or datetime.now(UTC)).astimezone(UTC)
        self._counters = replace(self._counters, observed=self._counters.observed + 1)
        if event.is_duplicate:
            self._counters = replace(self._counters, duplicates=self._counters.duplicates + 1)
        if event.is_out_of_order:
            self._counters = replace(self._counters, out_of_order=self._counters.out_of_order + 1)
        if event.is_gap:
            self._counters = replace(self._counters, gaps=self._counters.gaps + 1)
        if (
            self._last_source_sequence is not None
            and event.source_sequence <= self._last_source_sequence
        ):
            if not event.is_out_of_order and not event.is_duplicate:
                self._counters = replace(self._counters, backfills=self._counters.backfills + 1)
        if (
            self._last_source_sequence is None
            or event.source_sequence >= self._last_source_sequence
        ):
            self._last_source_sequence = event.source_sequence
        age_seconds = (when - event.event_time).total_seconds()
        latency_ms = int((event.received_at - event.event_time).total_seconds() * 1000)

        if not self._is_market_session_open(event.event_time):
            status: QualityStatus = "market_closed"
            code = "market_closed"
            completeness: Literal["complete", "partial", "unknown"] = "unknown"
        elif age_seconds < (-self._config.clock_skew_threshold_seconds):
            status = "clock_skew"
            code = "clock_skew"
            completeness = "partial"
        elif age_seconds > self._config.freshness_threshold_seconds:
            status = "stale"
            code = "stale_event"
            completeness = "partial"
        elif latency_ms > self._config.latency_threshold_ms:
            status = "stale"
            code = "high_latency"
            completeness = "partial"
        else:
            status = "live"
            code = "ok"
            completeness = "complete"
        return self._append_snapshot(
            observed_at=when,
            status=status,
            code=code,
            age_seconds=age_seconds,
            latency_ms=latency_ms,
            completeness=completeness,
        )

    def observe_transport(
        self,
        *,
        status: QualityStatus,
        code: str,
        observed_at: datetime | None = None,
    ) -> QualitySnapshot:
        when = (observed_at or datetime.now(UTC)).astimezone(UTC)
        if status == "disconnected" and self._last_status != "disconnected":
            self._counters = replace(self._counters, disconnects=self._counters.disconnects + 1)
        if status == "live" and self._last_status == "disconnected":
            self._counters = replace(self._counters, reconnects=self._counters.reconnects + 1)
        if status in {"error", "unavailable"}:
            self._counters = replace(self._counters, rejected=self._counters.rejected + 1)
        completeness: Literal["complete", "partial", "unknown"] = (
            "complete" if status == "live" else "unknown"
        )
        return self._append_snapshot(
            observed_at=when,
            status=status,
            code=code,
            age_seconds=None,
            latency_ms=None,
            completeness=completeness,
        )

    def snapshot(self) -> QualitySnapshot:
        return self._history[-1]

    def history(self, *, limit: int = 100) -> tuple[QualitySnapshot, ...]:
        bounded = max(1, min(limit, len(self._history)))
        return tuple(self._history[-bounded:])

    def _append_snapshot(
        self,
        *,
        observed_at: datetime,
        status: QualityStatus,
        code: str,
        age_seconds: float | None,
        latency_ms: int | None,
        completeness: Literal["complete", "partial", "unknown"],
    ) -> QualitySnapshot:
        self._sequence += 1
        snapshot = QualitySnapshot(
            schema_version=1,
            symbol=self._config.symbol,
            sequence=self._sequence,
            observed_at=observed_at,
            status=status,
            code=code,
            age_seconds=age_seconds,
            latency_ms=latency_ms,
            completeness=completeness,
            counters=self._counters,
            config_version=self._config.version,
        )
        self._history.append(snapshot)
        self._last_status = status
        return snapshot

    def _is_market_session_open(self, event_time: datetime) -> bool:
        hour = event_time.astimezone(UTC).hour
        return self._config.session_start_hour_utc <= hour < self._config.session_end_hour_utc
