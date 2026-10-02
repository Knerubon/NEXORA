"""Relevance classification interfaces and deterministic default implementations.

Classification is evidence only. A `RelevanceResult.relevant == True` is not a
filter/block decision by itself — that is left to a future scanner/risk consumer.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from nexora.events.config import NewsScannerConfig, SocialScannerConfig
from nexora.events.models import RelevanceResult
from nexora.events.providers import RawNewsItem, RawSocialPost


@runtime_checkable
class NewsRelevanceClassifier(Protocol):
    def classify(self, item: RawNewsItem, config: NewsScannerConfig) -> RelevanceResult: ...


@runtime_checkable
class SocialRelevanceClassifier(Protocol):
    def classify(self, post: RawSocialPost, config: SocialScannerConfig) -> RelevanceResult: ...


class DefaultNewsRelevanceClassifier:
    """Deterministic currency/symbol watchlist match. No I/O, no scoring model."""

    def classify(self, item: RawNewsItem, config: NewsScannerConfig) -> RelevanceResult:
        matched_currencies = tuple(c for c in item.currencies if c in config.watched_currencies)
        matched_symbols = tuple(s for s in item.symbols if s in config.watched_symbols)
        relevant = bool(matched_currencies or matched_symbols)
        reason_codes: tuple[str, ...] = ()
        if matched_currencies:
            reason_codes += ("currency_match",)
        if matched_symbols:
            reason_codes += ("symbol_match",)
        if not reason_codes:
            reason_codes = ("no_watchlist_match",)
        return RelevanceResult(
            relevant=relevant,
            matched_symbols=matched_symbols,
            matched_currencies=matched_currencies,
            score=Decimal("1") if relevant else Decimal("0"),
            reason_codes=reason_codes,
        )


class DefaultSocialRelevanceClassifier:
    """Deterministic account-watchlist + symbol match. Fail-closed: an empty
    watchlist matches nothing, it does not default to "relevant".
    """

    def classify(self, post: RawSocialPost, config: SocialScannerConfig) -> RelevanceResult:
        account_matched = post.account in config.watched_accounts
        matched_symbols = tuple(s for s in post.symbols if s in config.watched_symbols)
        relevant = account_matched and bool(config.watched_accounts)
        reason_codes: tuple[str, ...] = ()
        if account_matched:
            reason_codes += ("account_match",)
        if matched_symbols:
            reason_codes += ("symbol_match",)
        if not reason_codes:
            reason_codes = ("no_watchlist_match",)
        score = Decimal("1") if relevant else Decimal("0")
        return RelevanceResult(
            relevant=relevant,
            matched_symbols=matched_symbols,
            matched_currencies=(),
            score=score,
            reason_codes=reason_codes,
        )
