"""Validates deterministic default relevance classifiers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nexora.events.config import NewsScannerConfig, SocialScannerConfig
from nexora.events.models import ImpactLevel
from nexora.events.providers import RawNewsItem, RawSocialPost
from nexora.events.relevance import (
    DefaultNewsRelevanceClassifier,
    DefaultSocialRelevanceClassifier,
)

T1 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_news_classifier_matches_watched_currency() -> None:
    config = NewsScannerConfig(config_version="cfg-1", watched_currencies=("USD",))
    item = RawNewsItem(
        external_id="e1",
        scheduled_at=T1,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.HIGH,
        category="CPI",
        title="US CPI",
    )
    result = DefaultNewsRelevanceClassifier().classify(item, config)
    assert result.relevant is True
    assert result.matched_currencies == ("USD",)
    assert result.score == Decimal("1")
    assert "currency_match" in result.reason_codes


def test_news_classifier_no_match_is_not_relevant() -> None:
    config = NewsScannerConfig(config_version="cfg-1", watched_currencies=("EUR",))
    item = RawNewsItem(
        external_id="e1",
        scheduled_at=T1,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.LOW,
        category="x",
        title="y",
    )
    result = DefaultNewsRelevanceClassifier().classify(item, config)
    assert result.relevant is False
    assert result.score == Decimal("0")
    assert result.reason_codes == ("no_watchlist_match",)


def test_social_classifier_requires_nonempty_watchlist_to_be_relevant() -> None:
    config = SocialScannerConfig(config_version="cfg-1", watched_accounts=())
    post = RawSocialPost(external_id="p1", account="anyone", posted_at=T1, symbols=(), text="x")
    result = DefaultSocialRelevanceClassifier().classify(post, config)
    assert result.relevant is False


def test_social_classifier_matches_watched_account() -> None:
    config = SocialScannerConfig(config_version="cfg-1", watched_accounts=("watched",))
    post = RawSocialPost(external_id="p1", account="watched", posted_at=T1, symbols=(), text="x")
    result = DefaultSocialRelevanceClassifier().classify(post, config)
    assert result.relevant is True
    assert "account_match" in result.reason_codes


def test_social_classifier_unwatched_account_not_relevant() -> None:
    config = SocialScannerConfig(config_version="cfg-1", watched_accounts=("watched",))
    post = RawSocialPost(external_id="p1", account="stranger", posted_at=T1, symbols=(), text="x")
    result = DefaultSocialRelevanceClassifier().classify(post, config)
    assert result.relevant is False
    assert result.reason_codes == ("no_watchlist_match",)
