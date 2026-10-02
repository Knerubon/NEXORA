"""Provider health/failure representation. Pure data, no I/O, no wall-clock reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ProviderHealth(StrEnum):
    """Subset of the ADR-033 section 18 SystemHealthGate vocabulary relevant to a
    single news/social provider. Fail-closed default is UNKNOWN, matching that ADR.
    """

    HEALTHY = "HEALTHY"
    UNKNOWN = "UNKNOWN"
    UNHEALTHY = "UNHEALTHY"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    """Caller supplies `observed_at`; this module never reads the clock itself."""

    provider_id: str
    status: ProviderHealth
    reason: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("missing_provider_id")
        if not self.reason.strip():
            raise ValueError("missing_health_reason")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("timezone_required")
