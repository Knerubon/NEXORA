"""Validates Fake provider scripted behavior. No network calls occur anywhere."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nexora.events.health import ProviderHealth
from nexora.events.models import ImpactLevel
from nexora.events.providers import (
    EconomicNewsProvider,
    FakeEconomicNewsProvider,
    FakeSocialEventProvider,
    ProviderConnectionError,
    RawNewsItem,
    RawSocialPost,
    SocialEventProvider,
)

T1 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _news_item(at: datetime = T1) -> RawNewsItem:
    return RawNewsItem(
        external_id="ext-1",
        scheduled_at=at,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.HIGH,
        category="CPI",
        title="US CPI",
    )


def test_fake_news_provider_satisfies_protocol() -> None:
    provider = FakeEconomicNewsProvider(provider_id="fake-1")
    assert isinstance(provider, EconomicNewsProvider)


def test_fake_social_provider_satisfies_protocol() -> None:
    provider = FakeSocialEventProvider(provider_id="fake-1")
    assert isinstance(provider, SocialEventProvider)


def test_fake_news_provider_requires_connect_before_fetch() -> None:
    provider = FakeEconomicNewsProvider(provider_id="fake-1", items=[_news_item()])
    with pytest.raises(ProviderConnectionError, match="not_connected"):
        provider.fetch_calendar(T1, T2)


def test_fake_news_provider_fetch_calendar_filters_by_window() -> None:
    provider = FakeEconomicNewsProvider(
        provider_id="fake-1", items=[_news_item(T1), _news_item(T2)]
    )
    provider.connect()
    result = provider.fetch_calendar(T1, T1)
    assert len(result) == 1
    assert result[0].scheduled_at == T1


def test_fake_news_provider_connect_failure_is_scripted() -> None:
    provider = FakeEconomicNewsProvider(provider_id="fake-1", fail_on_connect=True)
    with pytest.raises(ProviderConnectionError, match="connect_failed"):
        provider.connect()
    assert provider.connected is False


def test_fake_news_provider_health_reflects_connection_state() -> None:
    provider = FakeEconomicNewsProvider(provider_id="fake-1")
    assert provider.health(T1).status is ProviderHealth.UNKNOWN
    provider.connect()
    assert provider.health(T1).status is ProviderHealth.HEALTHY


def test_fake_news_provider_health_can_be_scripted_unhealthy() -> None:
    from nexora.events.health import ProviderHealthSnapshot

    scripted = ProviderHealthSnapshot(
        provider_id="fake-1", status=ProviderHealth.STALE, reason="feed_lag", observed_at=T1
    )
    provider = FakeEconomicNewsProvider(provider_id="fake-1", scripted_health=scripted)
    provider.connect()
    assert provider.health(T2) == scripted


def test_fake_social_provider_fetch_recent_filters_by_account_and_time() -> None:
    posts = [
        RawSocialPost(external_id="p1", account="watched", posted_at=T1, symbols=(), text="a"),
        RawSocialPost(external_id="p2", account="unwatched", posted_at=T1, symbols=(), text="b"),
        RawSocialPost(external_id="p3", account="watched", posted_at=T2, symbols=(), text="c"),
    ]
    provider = FakeSocialEventProvider(provider_id="fake-social-1", posts=posts)
    provider.connect()
    result = provider.fetch_recent(["watched"], since=T1)
    assert {p.external_id for p in result} == {"p1", "p3"}


def test_fake_social_provider_requires_connect_before_fetch() -> None:
    provider = FakeSocialEventProvider(provider_id="fake-social-1")
    with pytest.raises(ProviderConnectionError, match="not_connected"):
        provider.fetch_recent(["watched"], since=T1)


def test_raw_news_item_rejects_blank_external_id() -> None:
    with pytest.raises(ValueError, match="missing_external_id"):
        RawNewsItem(
            external_id="  ",
            scheduled_at=T1,
            currencies=("USD",),
            symbols=(),
            impact=ImpactLevel.LOW,
            category="x",
            title="y",
        )


def test_raw_social_post_rejects_blank_text() -> None:
    with pytest.raises(ValueError, match="missing_text"):
        RawSocialPost(external_id="p1", account="a", posted_at=T1, symbols=(), text="   ")
