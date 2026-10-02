"""Normalized News/Social payloads.

ADR-033 section 16 freezes only the mechanism boundary (News/Social may influence
NewTradeAuthority; social evidence may never directly emit a TradeIntent) and
explicitly leaves the shared `MarketEvent` envelope shape UNFROZEN ("Normalized
News/Social event boundary: PROVISIONAL ... envelope shape open", ADR-033 section
25). This module therefore does NOT define a shared envelope type. `NormalizedNewsItem`
and `NormalizedSocialPost` are separate, source-specific, provisional shapes scoped to
this infrastructure task only. Reconciling them into a single frozen contract is an
Architect/Quant decision for a future ADR, not something implemented here.

Hard invariant (instruction K / ADR-033 section 16, enforced by construction): neither
type below carries an `action`, `side`, or any other field that could be read as a
trading instruction. There is nothing here for a future caller to "upgrade" into a
BUY/SELL by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ImpactLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timezone_required:{field_name}")


@dataclass(frozen=True, slots=True)
class ProviderProvenance:
    """Source references and version info, matching the ADR-011 evidence discipline."""

    provider_id: str
    fetched_at: datetime
    config_version: str
    source_ref: str

    def __post_init__(self) -> None:
        _require_aware(self.fetched_at, "fetched_at")
        if not self.provider_id.strip():
            raise ValueError("missing_provider_id")
        if not self.config_version.strip():
            raise ValueError("missing_config_version")
        if not self.source_ref.strip():
            raise ValueError("missing_source_ref")


@dataclass(frozen=True, slots=True)
class RelevanceResult:
    """Output of a relevance classifier. Evidence only — not a filter decision."""

    relevant: bool
    matched_symbols: tuple[str, ...]
    matched_currencies: tuple[str, ...]
    score: Decimal
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.score.is_finite() or not (Decimal("0") <= self.score <= Decimal("1")):
            raise ValueError("invalid_relevance_score")
        if not self.reason_codes:
            raise ValueError("missing_relevance_reason_codes")


@dataclass(frozen=True, slots=True)
class NormalizedNewsItem:
    """Provisional, source-specific normalized economic-calendar event.

    NOT the frozen MarketEvent envelope referenced in ADR-033 section 16 — no such
    envelope exists yet. This shape is local to the News/Social infrastructure task.
    """

    event_id: str
    event_time: datetime
    received_at: datetime
    currencies: tuple[str, ...]
    symbols: tuple[str, ...]
    impact: ImpactLevel
    category: str
    title: str
    reasons: tuple[str, ...]
    reason_codes: tuple[str, ...]
    relevance: RelevanceResult
    provenance: ProviderProvenance

    def __post_init__(self) -> None:
        _require_aware(self.event_time, "event_time")
        _require_aware(self.received_at, "received_at")
        if not self.event_id.strip():
            raise ValueError("missing_event_id")
        if not self.title.strip():
            raise ValueError("missing_title")
        if not self.category.strip():
            raise ValueError("missing_category")
        if not self.currencies and not self.symbols:
            raise ValueError("news_item_requires_currency_or_symbol")
        if not self.reason_codes:
            raise ValueError("missing_reason_codes")


@dataclass(frozen=True, slots=True)
class NormalizedSocialPost:
    """Provisional, source-specific normalized social/account post.

    Deliberately carries no `action`/`side`/`decision` field (hard invariant: social
    evidence can never directly command BUY or SELL). Always weaker evidence than a
    `NormalizedNewsItem` — consumers must not treat the two as interchangeable.
    """

    event_id: str
    account: str
    posted_at: datetime
    received_at: datetime
    symbols: tuple[str, ...]
    category: str
    excerpt: str
    reasons: tuple[str, ...]
    reason_codes: tuple[str, ...]
    relevance: RelevanceResult
    provenance: ProviderProvenance

    def __post_init__(self) -> None:
        _require_aware(self.posted_at, "posted_at")
        _require_aware(self.received_at, "received_at")
        if not self.event_id.strip():
            raise ValueError("missing_event_id")
        if not self.account.strip():
            raise ValueError("missing_account")
        if not self.excerpt.strip():
            raise ValueError("missing_excerpt")
        if not self.category.strip():
            raise ValueError("missing_category")
        if not self.reason_codes:
            raise ValueError("missing_reason_codes")
