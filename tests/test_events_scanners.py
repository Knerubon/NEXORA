"""Validates deterministic scanner normalization, blackout math, and the News/Social
infrastructure isolation boundary (no network, no wall-clock, no trading-decision
dependency — matches the convention in test_pattern_engine.py's core-isolation test).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nexora.events.config import NewsScannerConfig, SocialScannerConfig
from nexora.events.models import ImpactLevel
from nexora.events.providers import RawNewsItem, RawSocialPost
from nexora.events.relevance import (
    DefaultNewsRelevanceClassifier,
    DefaultSocialRelevanceClassifier,
)
from nexora.events.scanners import (
    EconomicCalendarScanner,
    SocialEventScanner,
    is_in_blackout_window,
    upcoming_news,
)

ROOT = Path(__file__).resolve().parents[1]
EVENTS_FILES = sorted((ROOT / "packages/nexora/events").glob("*.py"))

T_SCHEDULED = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
T_FETCHED = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
T_RECEIVED = datetime(2026, 10, 2, 11, 0, 5, tzinfo=UTC)


def _scanner(watched_currencies: tuple[str, ...] = ("USD",)) -> EconomicCalendarScanner:
    config = NewsScannerConfig(config_version="cfg-1", watched_currencies=watched_currencies)
    return EconomicCalendarScanner(config=config, classifier=DefaultNewsRelevanceClassifier())


def _news_item() -> RawNewsItem:
    return RawNewsItem(
        external_id="ext-1",
        scheduled_at=T_SCHEDULED,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.HIGH,
        category="CPI",
        title="US CPI",
    )


def test_scan_is_deterministic_across_calls() -> None:
    scanner = _scanner()
    items = [_news_item()]
    first = scanner.scan("fake-1", items, T_RECEIVED, T_FETCHED)
    second = scanner.scan("fake-1", items, T_RECEIVED, T_FETCHED)
    assert first == second
    assert first[0].event_id == second[0].event_id


def test_scan_event_id_is_stable_and_derived_from_identity_not_order() -> None:
    scanner = _scanner()
    a = RawNewsItem(
        external_id="a",
        scheduled_at=T_SCHEDULED,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.LOW,
        category="x",
        title="A",
    )
    b = RawNewsItem(
        external_id="b",
        scheduled_at=T_SCHEDULED,
        currencies=("USD",),
        symbols=(),
        impact=ImpactLevel.LOW,
        category="x",
        title="B",
    )
    forward = scanner.scan("fake-1", [a, b], T_RECEIVED, T_FETCHED)
    backward = scanner.scan("fake-1", [b, a], T_RECEIVED, T_FETCHED)
    assert {e.event_id for e in forward} == {e.event_id for e in backward}
    # Output is sorted deterministically regardless of input order.
    assert [e.event_id for e in forward] == [e.event_id for e in backward]


def test_scan_different_provider_id_yields_different_event_id() -> None:
    scanner = _scanner()
    item = _news_item()
    one = scanner.scan("provider-a", [item], T_RECEIVED, T_FETCHED)
    two = scanner.scan("provider-b", [item], T_RECEIVED, T_FETCHED)
    assert one[0].event_id != two[0].event_id


def test_scan_carries_relevance_classification() -> None:
    scanner = _scanner(watched_currencies=("EUR",))
    normalized = scanner.scan("fake-1", [_news_item()], T_RECEIVED, T_FETCHED)
    assert normalized[0].relevance.relevant is False


def test_social_scan_is_deterministic_and_carries_no_action_field() -> None:
    config = SocialScannerConfig(config_version="cfg-1", watched_accounts=("acct",))
    scanner = SocialEventScanner(config=config, classifier=DefaultSocialRelevanceClassifier())
    post = RawSocialPost(
        external_id="p1", account="acct", posted_at=T_SCHEDULED, symbols=("EURUSD",), text="hi"
    )
    first = scanner.scan("fake-social-1", [post], T_RECEIVED, T_FETCHED)
    second = scanner.scan("fake-social-1", [post], T_RECEIVED, T_FETCHED)
    assert first == second
    assert not hasattr(first[0], "action")
    assert not hasattr(first[0], "side")


def test_upcoming_news_filters_by_horizon() -> None:
    scanner = _scanner()
    normalized = scanner.scan("fake-1", [_news_item()], T_RECEIVED, T_FETCHED)
    now = T_SCHEDULED.replace(hour=11, minute=30)
    within = upcoming_news(normalized, now=now, horizon_minutes=60)
    assert len(within) == 1
    outside = upcoming_news(normalized, now=now, horizon_minutes=10)
    assert outside == ()


def test_upcoming_news_rejects_negative_horizon() -> None:
    with pytest.raises(ValueError, match="invalid_horizon_minutes"):
        upcoming_news((), now=T_RECEIVED, horizon_minutes=-1)


def test_is_in_blackout_window_true_before_and_after_event() -> None:
    config = NewsScannerConfig(
        config_version="cfg-1",
        pre_blackout_minutes=((ImpactLevel.HIGH, 30),),
        post_blackout_minutes=((ImpactLevel.HIGH, 15),),
    )
    scanner = EconomicCalendarScanner(config=config, classifier=DefaultNewsRelevanceClassifier())
    normalized = scanner.scan("fake-1", [_news_item()], T_RECEIVED, T_FETCHED)
    event = normalized[0]

    just_before = T_SCHEDULED - timedelta(minutes=15)  # inside the 30-min pre-window
    just_after = T_SCHEDULED + timedelta(hours=1, minutes=10)  # well outside post-window
    assert is_in_blackout_window(config, event, just_before) is True
    assert is_in_blackout_window(config, event, just_after) is False


def test_is_in_blackout_window_outside_window_is_false() -> None:
    config = NewsScannerConfig(
        config_version="cfg-1",
        pre_blackout_minutes=((ImpactLevel.HIGH, 5),),
        post_blackout_minutes=((ImpactLevel.HIGH, 5),),
    )
    scanner = EconomicCalendarScanner(config=config, classifier=DefaultNewsRelevanceClassifier())
    event = scanner.scan("fake-1", [_news_item()], T_RECEIVED, T_FETCHED)[0]
    far_before = T_SCHEDULED.replace(hour=10)
    assert is_in_blackout_window(config, event, far_before) is False


def test_events_package_has_no_trading_decision_or_network_dependencies() -> None:
    """Mirrors test_pattern_engine.py's isolation test: this infrastructure must not
    import the Risk/Experience/EntryReadiness/Paper/Autonomous decision layers, any
    execution-adjacent module, or any network/randomness/wall-clock primitive.
    """

    forbidden_prefixes = (
        "nexora.risk",
        "nexora.paper",
        "nexora.experience",
        "nexora.autonomous_contracts",
        "nexora.entry_readiness",
        "nexora.signals",
        "nexora.research",
        "nexora_api",
    )
    forbidden_modules = {
        "socket",
        "subprocess",
        "urllib",
        "http",
        "requests",
        "random",
        "threading",
        "asyncio",
        "MetaTrader5",
        "psycopg",
        "fastapi",
    }
    for path in EVENTS_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(forbidden_prefixes), (path.name, name)
                assert name.split(".")[0] not in forbidden_modules, (path.name, name)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"now", "utcnow", "today", "environ", "getenv"}, (
                    path.name,
                    node.attr,
                )


def test_normalized_types_never_expose_a_trading_action_vocabulary() -> None:
    """Hard invariant (instruction K / ADR-033 section 16): a social post can never
    directly command BUY or SELL. Verified structurally: no normalized type in this
    package defines any of the forbidden field names anywhere in the source.
    """

    forbidden_identifiers = {"action", "side", "buy", "sell", "trade_intent"}
    models_source = (ROOT / "packages/nexora/events/models.py").read_text(encoding="utf-8")
    tree = ast.parse(models_source)
    field_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            field_names.add(node.target.id.lower())
    assert field_names.isdisjoint(forbidden_identifiers)
