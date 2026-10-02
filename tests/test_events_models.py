"""Validates normalized payload shapes, health snapshot, and config toggles."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexora.events.config import NewsScannerConfig, SocialScannerConfig
from nexora.events.health import ProviderHealth, ProviderHealthSnapshot
from nexora.events.models import (
    ImpactLevel,
    NormalizedNewsItem,
    NormalizedSocialPost,
    ProviderProvenance,
    RelevanceResult,
)

AWARE_TIME = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _provenance() -> ProviderProvenance:
    return ProviderProvenance(
        provider_id="fake-news-v1",
        fetched_at=AWARE_TIME,
        config_version="cfg-1",
        source_ref="ext-1",
    )


def _relevance(relevant: bool = True) -> RelevanceResult:
    return RelevanceResult(
        relevant=relevant,
        matched_symbols=("EURUSD",) if relevant else (),
        matched_currencies=("USD",) if relevant else (),
        score=Decimal("1") if relevant else Decimal("0"),
        reason_codes=("currency_match",) if relevant else ("no_watchlist_match",),
    )


def test_normalized_news_item_requires_aware_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone_required"):
        NormalizedNewsItem(
            event_id="e1",
            event_time=datetime(2026, 10, 2, 12, 0),
            received_at=AWARE_TIME,
            currencies=("USD",),
            symbols=(),
            impact=ImpactLevel.HIGH,
            category="CPI",
            title="US CPI",
            reasons=("x",),
            reason_codes=("impact_high",),
            relevance=_relevance(),
            provenance=_provenance(),
        )


def test_normalized_news_item_requires_currency_or_symbol() -> None:
    with pytest.raises(ValueError, match="news_item_requires_currency_or_symbol"):
        NormalizedNewsItem(
            event_id="e1",
            event_time=AWARE_TIME,
            received_at=AWARE_TIME,
            currencies=(),
            symbols=(),
            impact=ImpactLevel.HIGH,
            category="CPI",
            title="US CPI",
            reasons=("x",),
            reason_codes=("impact_high",),
            relevance=_relevance(),
            provenance=_provenance(),
        )


def test_normalized_news_item_has_no_trading_action_fields() -> None:
    item = NormalizedNewsItem(
        event_id="e1",
        event_time=AWARE_TIME,
        received_at=AWARE_TIME,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.HIGH,
        category="CPI",
        title="US CPI",
        reasons=("x",),
        reason_codes=("impact_high",),
        relevance=_relevance(),
        provenance=_provenance(),
    )
    for forbidden in ("action", "side", "decision", "signal_id"):
        assert not hasattr(item, forbidden)


def test_normalized_social_post_has_no_trading_action_fields() -> None:
    post = NormalizedSocialPost(
        event_id="s1",
        account="acct",
        posted_at=AWARE_TIME,
        received_at=AWARE_TIME,
        symbols=("EURUSD",),
        category="account_post",
        excerpt="hello",
        reasons=("x",),
        reason_codes=("social_evidence_only",),
        relevance=_relevance(),
        provenance=_provenance(),
    )
    for forbidden in ("action", "side", "decision", "signal_id"):
        assert not hasattr(post, forbidden)


def test_normalized_social_post_rejects_blank_excerpt() -> None:
    with pytest.raises(ValueError, match="missing_excerpt"):
        NormalizedSocialPost(
            event_id="s1",
            account="acct",
            posted_at=AWARE_TIME,
            received_at=AWARE_TIME,
            symbols=(),
            category="account_post",
            excerpt="   ",
            reasons=("x",),
            reason_codes=("social_evidence_only",),
            relevance=_relevance(),
            provenance=_provenance(),
        )


@pytest.mark.parametrize("score", [Decimal("-0.01"), Decimal("1.01")])
def test_relevance_result_rejects_out_of_range_score(score: Decimal) -> None:
    with pytest.raises(ValueError, match="invalid_relevance_score"):
        RelevanceResult(
            relevant=False,
            matched_symbols=(),
            matched_currencies=(),
            score=score,
            reason_codes=("x",),
        )


def test_provider_health_snapshot_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone_required"):
        ProviderHealthSnapshot(
            provider_id="p1",
            status=ProviderHealth.UNKNOWN,
            reason="not_connected",
            observed_at=datetime(2026, 10, 2, 12, 0),
        )


def test_provider_health_default_vocabulary() -> None:
    assert {h.value for h in ProviderHealth} == {"HEALTHY", "UNKNOWN", "UNHEALTHY", "STALE"}


def test_news_scanner_config_keeps_enabled_and_trade_during_news_independent() -> None:
    config = NewsScannerConfig(config_version="cfg-1", enabled=True, trade_during_news=False)
    assert config.enabled is True
    assert config.trade_during_news is False

    config2 = NewsScannerConfig(config_version="cfg-1", enabled=False, trade_during_news=True)
    assert config2.enabled is False
    assert config2.trade_during_news is True


def test_news_scanner_config_rejects_negative_blackout_minutes() -> None:
    with pytest.raises(ValueError, match="invalid_blackout_minutes"):
        NewsScannerConfig(
            config_version="cfg-1",
            pre_blackout_minutes=((ImpactLevel.HIGH, -5),),
        )


def test_news_scanner_config_blackout_lookup_defaults_to_zero() -> None:
    config = NewsScannerConfig(
        config_version="cfg-1",
        pre_blackout_minutes=((ImpactLevel.HIGH, 30),),
    )
    assert config.pre_blackout_minutes_for(ImpactLevel.HIGH) == 30
    assert config.pre_blackout_minutes_for(ImpactLevel.LOW) == 0
    assert config.post_blackout_minutes_for(ImpactLevel.HIGH) == 0


def test_social_scanner_config_defaults_to_warn_and_disabled() -> None:
    config = SocialScannerConfig(config_version="cfg-1")
    assert config.enabled is False
    assert config.policy_mode == "warn"


def test_social_scanner_config_rejects_invalid_policy_mode() -> None:
    with pytest.raises(ValueError, match="invalid_policy_mode"):
        SocialScannerConfig(config_version="cfg-1", policy_mode="escalate")  # type: ignore[arg-type]
