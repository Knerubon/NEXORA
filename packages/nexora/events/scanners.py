"""Deterministic News/Social scanners: raw provider payload -> normalized payload.

No network, no wall-clock reads (`fetched_at`/`now` are always caller-supplied), no
randomness. Same input always produces the same normalized output and the same
`event_id`, which is why `scan()` requires an explicit `fetched_at` rather than
reading a clock internally.

Blackout-window math is provided here as a pure function because the pre-flight
design placed "upcoming-news detection" / "pre-news blackout" / "post-news blackout"
under the Scanner's responsibility. It is NOT wired to RiskEngine, EntryReadiness, or
any TradeIntent/authority path in this task — that integration is explicitly out of
scope (see docs/decisions/ADR-033-autonomous-trading-contracts-v1.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from nexora.artifacts import canonical_hash
from nexora.events.config import NewsScannerConfig, SocialScannerConfig
from nexora.events.models import (
    NormalizedNewsItem,
    NormalizedSocialPost,
    ProviderProvenance,
)
from nexora.events.providers import RawNewsItem, RawSocialPost
from nexora.events.relevance import NewsRelevanceClassifier, SocialRelevanceClassifier


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timezone_required:{field_name}")


def _news_event_id(provider_id: str, item: RawNewsItem) -> str:
    return canonical_hash(("economic_calendar", provider_id, item.external_id, item.scheduled_at))


def _social_event_id(provider_id: str, post: RawSocialPost) -> str:
    return canonical_hash(("social", provider_id, post.external_id, post.posted_at))


@dataclass(frozen=True, slots=True)
class EconomicCalendarScanner:
    config: NewsScannerConfig
    classifier: NewsRelevanceClassifier

    def scan(
        self,
        provider_id: str,
        raw_items: Sequence[RawNewsItem],
        received_at: datetime,
        fetched_at: datetime,
    ) -> tuple[NormalizedNewsItem, ...]:
        _require_aware(received_at, "received_at")
        _require_aware(fetched_at, "fetched_at")
        normalized: list[NormalizedNewsItem] = []
        for item in raw_items:
            relevance = self.classifier.classify(item, self.config)
            provenance = ProviderProvenance(
                provider_id=provider_id,
                fetched_at=fetched_at,
                config_version=self.config.config_version,
                source_ref=item.external_id,
            )
            normalized.append(
                NormalizedNewsItem(
                    event_id=_news_event_id(provider_id, item),
                    event_time=item.scheduled_at,
                    received_at=received_at,
                    currencies=item.currencies,
                    symbols=item.symbols,
                    impact=item.impact,
                    category=item.category,
                    title=item.title,
                    reasons=(f"{item.category} scheduled at {item.scheduled_at.isoformat()}",),
                    reason_codes=(f"impact_{item.impact.value.lower()}",),
                    relevance=relevance,
                    provenance=provenance,
                )
            )
        return tuple(sorted(normalized, key=lambda n: (n.event_time, n.event_id)))


@dataclass(frozen=True, slots=True)
class SocialEventScanner:
    config: SocialScannerConfig
    classifier: SocialRelevanceClassifier

    def scan(
        self,
        provider_id: str,
        raw_posts: Sequence[RawSocialPost],
        received_at: datetime,
        fetched_at: datetime,
    ) -> tuple[NormalizedSocialPost, ...]:
        _require_aware(received_at, "received_at")
        _require_aware(fetched_at, "fetched_at")
        normalized: list[NormalizedSocialPost] = []
        for post in raw_posts:
            relevance = self.classifier.classify(post, self.config)
            provenance = ProviderProvenance(
                provider_id=provider_id,
                fetched_at=fetched_at,
                config_version=self.config.config_version,
                source_ref=post.external_id,
            )
            excerpt = post.text if len(post.text) <= 280 else post.text[:277] + "..."
            normalized.append(
                NormalizedSocialPost(
                    event_id=_social_event_id(provider_id, post),
                    account=post.account,
                    posted_at=post.posted_at,
                    received_at=received_at,
                    symbols=post.symbols,
                    category="account_post",
                    excerpt=excerpt,
                    reasons=(f"post by {post.account} at {post.posted_at.isoformat()}",),
                    reason_codes=("social_evidence_only",),
                    relevance=relevance,
                    provenance=provenance,
                )
            )
        return tuple(sorted(normalized, key=lambda n: (n.posted_at, n.event_id)))


def upcoming_news(
    events: Sequence[NormalizedNewsItem], now: datetime, horizon_minutes: int
) -> tuple[NormalizedNewsItem, ...]:
    """Deterministic "upcoming-news detection": events within [now, now+horizon]."""

    _require_aware(now, "now")
    if horizon_minutes < 0:
        raise ValueError("invalid_horizon_minutes")
    horizon_end = now + timedelta(minutes=horizon_minutes)
    return tuple(
        sorted(
            (e for e in events if now <= e.event_time <= horizon_end),
            key=lambda e: e.event_time,
        )
    )


def is_in_blackout_window(
    config: NewsScannerConfig, event: NormalizedNewsItem, now: datetime
) -> bool:
    """True when `now` falls inside the configured pre/post blackout window for
    this event's impact level. Pure function; the caller decides what to do with it
    (new-entry gating is a future Risk Engine concern, not decided here).
    """

    _require_aware(now, "now")
    pre = config.pre_blackout_minutes_for(event.impact)
    post = config.post_blackout_minutes_for(event.impact)
    window_start = event.event_time - timedelta(minutes=pre)
    window_end = event.event_time + timedelta(minutes=post)
    return window_start <= now <= window_end
