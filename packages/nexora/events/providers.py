"""Provider Protocols and raw payload shapes for News/Social infrastructure.

No network, no API keys, no real external provider is implemented or connected here.
`Fake*Provider` are scripted test doubles only, mirroring the
`FakeMarketDataAdapter` convention in `nexora.market_data.adapters`.

Providers return provider-native "raw" payloads; normalization into
`nexora.events.models` types happens in `nexora.events.scanners`, not here, so a
provider implementation never has to know the normalized shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from nexora.events.health import ProviderHealth, ProviderHealthSnapshot
from nexora.events.models import ImpactLevel


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timezone_required:{field_name}")


@dataclass(frozen=True, slots=True)
class RawNewsItem:
    """Provider-native economic calendar entry, pre-normalization."""

    external_id: str
    scheduled_at: datetime
    currencies: tuple[str, ...]
    symbols: tuple[str, ...]
    impact: ImpactLevel
    category: str
    title: str

    def __post_init__(self) -> None:
        _require_aware(self.scheduled_at, "scheduled_at")
        if not self.external_id.strip():
            raise ValueError("missing_external_id")
        if not self.title.strip():
            raise ValueError("missing_title")


@dataclass(frozen=True, slots=True)
class RawSocialPost:
    """Provider-native social/account post, pre-normalization."""

    external_id: str
    account: str
    posted_at: datetime
    symbols: tuple[str, ...]
    text: str

    def __post_init__(self) -> None:
        _require_aware(self.posted_at, "posted_at")
        if not self.external_id.strip():
            raise ValueError("missing_external_id")
        if not self.account.strip():
            raise ValueError("missing_account")
        if not self.text.strip():
            raise ValueError("missing_text")


class ProviderConnectionError(RuntimeError):
    """Raised by a provider when connect/fetch is attempted in an invalid state."""


@runtime_checkable
class EconomicNewsProvider(Protocol):
    provider_id: str

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def fetch_calendar(self, start: datetime, end: datetime) -> Sequence[RawNewsItem]: ...

    def health(self, now: datetime) -> ProviderHealthSnapshot: ...


@runtime_checkable
class SocialEventProvider(Protocol):
    provider_id: str

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def fetch_recent(self, accounts: Sequence[str], since: datetime) -> Sequence[RawSocialPost]: ...

    def health(self, now: datetime) -> ProviderHealthSnapshot: ...


@dataclass(slots=True)
class FakeEconomicNewsProvider:
    """Scripted provider for offline tests. No network."""

    provider_id: str
    items: list[RawNewsItem] = field(default_factory=list)
    connected: bool = False
    fail_on_connect: bool = False
    scripted_health: ProviderHealthSnapshot | None = None

    def connect(self) -> None:
        if self.fail_on_connect:
            raise ProviderConnectionError("fake_news_provider_connect_failed")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def fetch_calendar(self, start: datetime, end: datetime) -> Sequence[RawNewsItem]:
        if not self.connected:
            raise ProviderConnectionError("fake_news_provider_not_connected")
        return tuple(item for item in self.items if start <= item.scheduled_at <= end)

    def health(self, now: datetime) -> ProviderHealthSnapshot:
        if self.scripted_health is not None:
            return self.scripted_health
        status = ProviderHealth.HEALTHY if self.connected else ProviderHealth.UNKNOWN
        reason = "ok" if self.connected else "not_connected"
        return ProviderHealthSnapshot(
            provider_id=self.provider_id, status=status, reason=reason, observed_at=now
        )


@dataclass(slots=True)
class FakeSocialEventProvider:
    """Scripted provider for offline tests. No network."""

    provider_id: str
    posts: list[RawSocialPost] = field(default_factory=list)
    connected: bool = False
    fail_on_connect: bool = False
    scripted_health: ProviderHealthSnapshot | None = None

    def connect(self) -> None:
        if self.fail_on_connect:
            raise ProviderConnectionError("fake_social_provider_connect_failed")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def fetch_recent(self, accounts: Sequence[str], since: datetime) -> Sequence[RawSocialPost]:
        if not self.connected:
            raise ProviderConnectionError("fake_social_provider_not_connected")
        watch = set(accounts)
        return tuple(
            post for post in self.posts if post.account in watch and post.posted_at >= since
        )

    def health(self, now: datetime) -> ProviderHealthSnapshot:
        if self.scripted_health is not None:
            return self.scripted_health
        status = ProviderHealth.HEALTHY if self.connected else ProviderHealth.UNKNOWN
        reason = "ok" if self.connected else "not_connected"
        return ProviderHealthSnapshot(
            provider_id=self.provider_id, status=status, reason=reason, observed_at=now
        )
