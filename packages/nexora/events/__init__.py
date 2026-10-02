"""News/Social infrastructure (ADR-033 section 16 scope).

Provider -> Scanner -> normalized payload -> (future) consumers. No network, no
broker execution, no wiring into RiskEngine/ExperienceEngine/EntryReadiness in this
package — see module docstrings for exact boundaries.
"""

from nexora.events.config import NewsScannerConfig, SocialPolicyMode, SocialScannerConfig
from nexora.events.health import ProviderHealth, ProviderHealthSnapshot
from nexora.events.models import (
    ImpactLevel,
    NormalizedNewsItem,
    NormalizedSocialPost,
    ProviderProvenance,
    RelevanceResult,
)
from nexora.events.providers import (
    EconomicNewsProvider,
    FakeEconomicNewsProvider,
    FakeSocialEventProvider,
    ProviderConnectionError,
    RawNewsItem,
    RawSocialPost,
    SocialEventProvider,
)
from nexora.events.relevance import (
    DefaultNewsRelevanceClassifier,
    DefaultSocialRelevanceClassifier,
    NewsRelevanceClassifier,
    SocialRelevanceClassifier,
)
from nexora.events.scanners import (
    EconomicCalendarScanner,
    SocialEventScanner,
    is_in_blackout_window,
    upcoming_news,
)

__all__ = [
    "DefaultNewsRelevanceClassifier",
    "DefaultSocialRelevanceClassifier",
    "EconomicCalendarScanner",
    "EconomicNewsProvider",
    "FakeEconomicNewsProvider",
    "FakeSocialEventProvider",
    "ImpactLevel",
    "NewsRelevanceClassifier",
    "NewsScannerConfig",
    "NormalizedNewsItem",
    "NormalizedSocialPost",
    "ProviderConnectionError",
    "ProviderHealth",
    "ProviderHealthSnapshot",
    "ProviderProvenance",
    "RawNewsItem",
    "RawSocialPost",
    "RelevanceResult",
    "SocialEventProvider",
    "SocialEventScanner",
    "SocialPolicyMode",
    "SocialRelevanceClassifier",
    "SocialScannerConfig",
    "is_in_blackout_window",
    "upcoming_news",
]
