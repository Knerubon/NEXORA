"""SystemHealthGate: pure deterministic health aggregation (ADR-033 section 18).

No real monitoring/network wiring. Callers construct a SystemHealthSnapshot from
whatever health sources exist (today: none); every axis defaults to UNKNOWN, which
is fail-closed by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

HEALTH_AXES = (
    "market_data",
    "broker",
    "account",
    "capabilities",
    "reconciliation",
    "journal",
)


class Health(StrEnum):
    HEALTHY = "HEALTHY"
    UNKNOWN = "UNKNOWN"
    UNHEALTHY = "UNHEALTHY"
    STALE = "STALE"
    UNSYNCHRONIZED = "UNSYNCHRONIZED"


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemHealthSnapshot:
    """ADR-033 section 18. Every axis defaults to UNKNOWN (fail-closed default)."""

    observed_at: datetime
    market_data: Health = Health.UNKNOWN
    broker: Health = Health.UNKNOWN
    account: Health = Health.UNKNOWN
    capabilities: Health = Health.UNKNOWN
    reconciliation: Health = Health.UNKNOWN
    journal: Health = Health.UNKNOWN

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("system_health_observed_at_requires_timezone")

    def axes(self) -> tuple[tuple[str, Health], ...]:
        return tuple((axis, getattr(self, axis)) for axis in HEALTH_AXES)


class SystemHealthGate:
    """Pure deterministic health aggregation contract (ADR-033 section 18).

    No real monitoring/network wiring yet; this class only aggregates whatever
    snapshot it is given. It has no slot for an AIAnalysis input (ADR-033 section 19).
    """

    @staticmethod
    def degraded_axes(snapshot: SystemHealthSnapshot) -> tuple[str, ...]:
        """Every axis not HEALTHY, in fixed axis order."""

        return tuple(axis for axis, value in snapshot.axes() if value is not Health.HEALTHY)

    @staticmethod
    def blocks_new_trade(snapshot: SystemHealthSnapshot) -> bool:
        """ADR-033 section 18: any axis in {UNKNOWN, UNHEALTHY, STALE, UNSYNCHRONIZED} blocks."""

        return len(SystemHealthGate.degraded_axes(snapshot)) > 0

    @staticmethod
    def broker_unhealthy(snapshot: SystemHealthSnapshot) -> bool:
        """ADR-033 section 11 fail-closed broker-connectivity rule."""

        return snapshot.broker is Health.UNHEALTHY
