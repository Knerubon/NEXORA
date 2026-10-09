"""Data Quality Guard V1 (ADR-036): deterministic, fail-closed, no repair."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from nexora.artifacts import canonical_hash
from nexora.data_quality import (
    DataQualityGuard,
    MarketDataSnapshot,
    QualityExpectation,
    QualityGuardConfig,
)
from nexora.data_quality import models as m
from nexora.market_data.models import NormalizedPriceEvent

from tests.validation_fixtures import START, tick

EXPECTED = QualityExpectation("synthetic-test", "XAUUSD", "USD/oz")


def _config(**overrides: object) -> QualityGuardConfig:
    base: dict[str, object] = {
        "version": "dq-guard-test-v1",
        "max_quote_age_seconds": 60,
        "max_future_skew_seconds": 5,
        "max_latency_ms": 3000,
        "max_gap_seconds": 120,
        "min_events": 1,
        "require_bid_ask": True,
        "allow_zero_spread": False,
        "max_spread": D("1"),
        "max_spread_to_price": None,
    }
    base.update(overrides)
    return QualityGuardConfig(**base)  # type: ignore[arg-type]


def _guard(**overrides: object) -> DataQualityGuard:
    return DataQualityGuard(_config(**overrides), EXPECTED)


def _now(event: NormalizedPriceEvent) -> datetime:
    return event.received_at


def _codes(verdict: object) -> set[str]:
    return {f.code for f in verdict.findings}  # type: ignore[attr-defined]


def test_clean_snapshot_is_ok_and_permits_new_trade() -> None:
    events = (tick(1, "100"), tick(2, "101"))
    verdict = _guard().evaluate(MarketDataSnapshot(events), evaluated_at=_now(events[-1]))
    assert (verdict.state, verdict.new_trade_permitted, verdict.findings) == ("ok", True, ())
    assert verdict.event_keys == ("t:1", "t:2")
    assert verdict.snapshot_hash == canonical_hash(events)
    assert verdict.config_version == "dq-guard-test-v1"


def test_verdict_is_deterministic_and_repeatable() -> None:
    events = (tick(1, "100"), replace(tick(2, "101"), bid=D("102"), ask=D("101")))
    snapshot = MarketDataSnapshot(events)
    first = _guard().evaluate(snapshot, evaluated_at=_now(events[-1]))
    second = _guard().evaluate(snapshot, evaluated_at=_now(events[-1]))
    assert first == second
    assert canonical_hash(first) == canonical_hash(second)


def test_missing_market_data_is_unknown_and_denies_trade() -> None:
    verdict = _guard().evaluate(MarketDataSnapshot(()), evaluated_at=START)
    assert (verdict.state, verdict.new_trade_permitted) == ("unknown", False)
    assert _codes(verdict) == {m.MISSING_MARKET_DATA}


def test_incomplete_snapshot_below_min_events() -> None:
    event = tick(1, "100")
    verdict = _guard(min_events=3).evaluate(MarketDataSnapshot((event,)), evaluated_at=_now(event))
    assert m.INCOMPLETE_SNAPSHOT in _codes(verdict)
    assert verdict.new_trade_permitted is False


@pytest.mark.parametrize("evaluated_at", [None, datetime(2026, 3, 2, 9, 0, 1)])
def test_missing_or_naive_clock_is_unsynchronized(evaluated_at: datetime | None) -> None:
    verdict = _guard().evaluate(MarketDataSnapshot((tick(1, "100"),)), evaluated_at=evaluated_at)
    assert (verdict.state, verdict.new_trade_permitted) == ("unknown", False)
    assert _codes(verdict) == {m.UNSYNCHRONIZED_CLOCK}


def test_duplicate_record_rejected() -> None:
    event = tick(1, "100")
    verdict = _guard().evaluate(MarketDataSnapshot((event, event)), evaluated_at=_now(event))
    assert m.DUPLICATE_RECORD in _codes(verdict)
    assert verdict.new_trade_permitted is False


def test_duplicate_flag_rejected() -> None:
    event = replace(tick(1, "100"), is_duplicate=True)
    verdict = _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=_now(event))
    assert m.DUPLICATE_RECORD in _codes(verdict) and verdict.state == "blocked"


def test_same_identity_different_content_is_conflict() -> None:
    a = tick(1, "100")
    b = replace(a, bid=D("100.1"), price=D("100.1"), ask=D("100.4"))
    verdict = _guard().evaluate(MarketDataSnapshot((a, b)), evaluated_at=_now(b))
    assert m.IDENTITY_CONFLICT in _codes(verdict)


def test_out_of_order_timestamp_rejected() -> None:
    first, second = tick(2, "100"), tick(3, "101", seconds=0)
    verdict = _guard().evaluate(MarketDataSnapshot((first, second)), evaluated_at=_now(first))
    assert m.OUT_OF_ORDER_TIMESTAMP in _codes(verdict)
    assert verdict.new_trade_permitted is False


def test_out_of_order_sequence_and_flag_rejected() -> None:
    a, b = tick(5, "100"), replace(tick(6, "101"), source_sequence=3)
    assert m.OUT_OF_ORDER_SEQUENCE in _codes(
        _guard().evaluate(MarketDataSnapshot((a, b)), evaluated_at=_now(b))
    )
    flagged = replace(tick(7, "100"), is_out_of_order=True)
    assert m.OUT_OF_ORDER_SEQUENCE in _codes(
        _guard().evaluate(MarketDataSnapshot((flagged,)), evaluated_at=_now(flagged))
    )


def test_stale_quote_rejected_with_boundary() -> None:
    event = tick(1, "100")
    at_limit = event.event_time + timedelta(seconds=60)
    assert _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=at_limit).state == "ok"
    past = event.event_time + timedelta(seconds=61)
    verdict = _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=past)
    assert m.STALE_QUOTE in _codes(verdict) and verdict.new_trade_permitted is False


@pytest.mark.parametrize(
    ("bid", "ask", "expected"),
    [
        (D("0"), D("0.3"), m.INVALID_BID_ASK),
        (D("-1"), D("1"), m.INVALID_BID_ASK),
        (D("100.5"), D("100"), m.NEGATIVE_SPREAD),
        (D("100"), D("100"), m.ZERO_SPREAD),
        (D("100"), D("102"), m.EXCESSIVE_SPREAD),
    ],
)
def test_invalid_bid_ask_and_spread(bid: D, ask: D, expected: str) -> None:
    event = replace(tick(1, "100"), bid=bid, ask=ask)
    verdict = _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=_now(event))
    assert expected in _codes(verdict)
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)


def test_zero_spread_allowed_only_when_configured() -> None:
    event = replace(tick(1, "100"), bid=D("100"), ask=D("100"))
    snapshot = MarketDataSnapshot((event,))
    assert _guard(allow_zero_spread=True).evaluate(snapshot, evaluated_at=_now(event)).state == "ok"


def test_spread_to_price_ratio_cap() -> None:
    event = replace(tick(1, "100"), bid=D("100"), ask=D("100.5"))
    verdict = _guard(max_spread=None, max_spread_to_price=D("0.001")).evaluate(
        MarketDataSnapshot((event,)), evaluated_at=_now(event)
    )
    assert m.EXCESSIVE_SPREAD in _codes(verdict)


def test_missing_bid_ask_is_unknown_when_required_and_ignored_when_not() -> None:
    # Self-consistent last-price event: only the bid/ask pair is absent.
    event = replace(tick(1, "100"), bid=None, ask=None, price_source="last", last=D("100"))
    snapshot = MarketDataSnapshot((event,))
    required = _guard().evaluate(snapshot, evaluated_at=_now(event))
    assert (required.state, required.new_trade_permitted) == ("unknown", False)
    assert _codes(required) == {m.MISSING_BID_ASK}
    relaxed = _guard(require_bid_ask=False, max_spread=None)
    assert relaxed.evaluate(snapshot, evaluated_at=_now(event)).state == "ok"


@pytest.mark.parametrize("field", ["price", "bid", "ask", "last", "close"])
def test_non_finite_numeric_values_rejected(field: str) -> None:
    changes: dict[str, Any] = {field: D("NaN")}
    event = replace(tick(1, "100"), **changes)
    verdict = _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=_now(event))
    assert m.NON_FINITE_VALUE in _codes(verdict)
    assert verdict.new_trade_permitted is False


def test_wrong_numeric_type_rejected() -> None:
    event = replace(tick(1, "100"), last="100")  # type: ignore[arg-type]
    verdict = _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=_now(event))
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)


def test_clock_anomalies() -> None:
    event = tick(1, "100")
    backwards = replace(event, received_at=event.event_time - timedelta(seconds=1))
    assert m.CLOCK_ANOMALY_RECEIVED_BEFORE_EVENT in _codes(
        _guard().evaluate(MarketDataSnapshot((backwards,)), evaluated_at=_now(event))
    )
    future_clock = event.event_time - timedelta(seconds=6)  # event is 6s in our future
    assert m.CLOCK_ANOMALY_FUTURE_EVENT in _codes(
        _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=future_clock)
    )
    slow = replace(event, received_at=event.event_time + timedelta(seconds=5))
    assert m.HIGH_LATENCY in _codes(
        _guard().evaluate(MarketDataSnapshot((slow,)), evaluated_at=slow.received_at)
    )


def test_timestamp_and_sequence_gaps() -> None:
    a, b = tick(1, "100"), tick(2, "101", seconds=20 + 121)
    verdict = _guard().evaluate(MarketDataSnapshot((a, b)), evaluated_at=_now(b))
    assert m.TIMESTAMP_GAP in _codes(verdict) and verdict.new_trade_permitted is False
    flagged = tick(3, "100", is_gap=True)
    assert m.SEQUENCE_GAP in _codes(
        _guard().evaluate(MarketDataSnapshot((flagged,)), evaluated_at=_now(flagged))
    )


@pytest.mark.parametrize(
    "override",
    [{"source": "other-broker"}, {"symbol": "EURUSD"}, {"units": "EUR"}],
)
def test_source_and_instrument_identity_mismatch(override: dict[str, str]) -> None:
    changes: dict[str, Any] = dict(override)
    event = replace(tick(1, "100"), **changes)
    verdict = _guard().evaluate(MarketDataSnapshot((event,)), evaluated_at=_now(event))
    assert m.IDENTITY_MISMATCH in _codes(verdict)
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)


def test_mixed_identity_inside_one_snapshot_rejected() -> None:
    a, b = tick(1, "100"), replace(tick(2, "101"), symbol="XAGUSD")
    verdict = _guard().evaluate(MarketDataSnapshot((a, b)), evaluated_at=_now(b))
    assert m.IDENTITY_MISMATCH in _codes(verdict)


def test_findings_preserve_raw_evidence_and_never_mutate_input() -> None:
    event = replace(tick(1, "100"), bid=D("100.5"), ask=D("100"))
    snapshot = MarketDataSnapshot((event,))
    before = canonical_hash(snapshot.events)
    verdict = _guard().evaluate(snapshot, evaluated_at=_now(event))
    finding = next(f for f in verdict.findings if f.code == m.NEGATIVE_SPREAD)
    assert finding.event_key == "t:1"
    assert dict(finding.evidence) == {"ask": "100", "bid": "100.5"}
    assert canonical_hash(snapshot.events) == before  # nothing repaired or interpolated
    assert verdict.snapshot_hash == before


def test_internal_error_fails_closed_without_leaking_message() -> None:
    class Boom:
        @property
        def events(self) -> tuple[NormalizedPriceEvent, ...]:
            raise RuntimeError("secret-connection-string")

    verdict = _guard().evaluate(Boom(), evaluated_at=START)  # type: ignore[arg-type]
    assert (verdict.state, verdict.new_trade_permitted) == ("unknown", False)
    assert _codes(verdict) == {m.GUARD_INTERNAL_ERROR}
    assert "secret" not in repr(verdict)


def test_config_requires_every_threshold_and_validates_values() -> None:
    with pytest.raises(TypeError):
        QualityGuardConfig(version="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="invalid_max_quote_age_seconds"):
        _config(max_quote_age_seconds=-1)
    with pytest.raises(ValueError, match="invalid_min_events"):
        _config(min_events=0)
    with pytest.raises(ValueError, match="invalid_max_spread"):
        _config(max_spread=D("NaN"))


def test_guard_has_no_wall_clock_or_io_dependencies() -> None:
    import inspect

    import nexora.data_quality.guard as guard_module

    source = inspect.getsource(guard_module)
    for forbidden in (
        "datetime.now",
        "utcnow",
        "time.time",
        "sleep",
        "socket",
        "requests",
        "os.environ",
    ):
        assert forbidden not in source
