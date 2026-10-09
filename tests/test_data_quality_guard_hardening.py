"""Data Quality Guard hardening round 1 (ADR-036): F2 configuration, price/OHLC, verdict forgery.

Regression for finding F2 (invalid Guard configuration) and the price/OHLC trust boundary.
Every invalid configuration must be unconstructible, and must never reach an ``ok`` verdict.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

import pytest
from nexora.data_quality import (
    DataQualityGuard,
    MarketDataSnapshot,
    QualityExpectation,
    QualityFinding,
    QualityGuardConfig,
    QualityVerdict,
)
from nexora.data_quality import models as m
from nexora.market_data.models import NormalizedPriceEvent

from tests.validation_fixtures import START, tick

EXPECTED = QualityExpectation("synthetic-test", "XAUUSD", "USD/oz")
FIELDS = {
    "version": "dq-guard-hardening-v1",
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


def _config(**overrides: object) -> QualityGuardConfig:
    return QualityGuardConfig(**{**FIELDS, **overrides})  # type: ignore[arg-type]


def _guard(**overrides: object) -> DataQualityGuard:
    return DataQualityGuard(_config(**overrides), EXPECTED)


def _eval(guard: DataQualityGuard, *events: NormalizedPriceEvent) -> QualityVerdict:
    return guard.evaluate(MarketDataSnapshot(tuple(events)), evaluated_at=events[-1].received_at)


def _codes(verdict: QualityVerdict) -> set[str]:
    return {f.code for f in verdict.findings}


def test_baseline_configuration_is_valid_and_yields_ok() -> None:
    verdict = _eval(_guard(), tick(1, "100"))
    assert (verdict.state, verdict.new_trade_permitted) == ("ok", True)


# F2: every configuration field is validated strictly --------------------------------------

INT_FIELDS = [
    "max_quote_age_seconds",
    "max_future_skew_seconds",
    "max_latency_ms",
    "max_gap_seconds",
    "min_events",
]


@pytest.mark.parametrize("name", INT_FIELDS)
@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        1e309,  # overflows to inf
        60.0,  # float masquerading as an integer
        True,  # bool masquerading as a number
        False,
        "60",  # numeric string
        None,
        D("60"),  # Decimal where an int is required
        -1,
        10**12,  # absurdly permissive: would turn the check into a no-op
    ],
)
def test_integer_thresholds_reject_nan_inf_bool_wrong_types_negative_and_absurd(
    name: str, bad: object
) -> None:
    with pytest.raises(ValueError, match=f"invalid_{name}"):
        _config(**{name: bad})


@pytest.mark.parametrize("name", ["max_quote_age_seconds", "max_latency_ms", "max_gap_seconds"])
def test_zero_is_nonsensical_for_age_latency_and_gap(name: str) -> None:
    with pytest.raises(ValueError, match=f"invalid_{name}"):
        _config(**{name: 0})


def test_zero_future_skew_is_allowed_but_zero_min_events_is_not() -> None:
    assert _config(max_future_skew_seconds=0).max_future_skew_seconds == 0
    with pytest.raises(ValueError, match="invalid_min_events"):
        _config(min_events=0)


@pytest.mark.parametrize("name", ["require_bid_ask", "allow_zero_spread"])
@pytest.mark.parametrize("bad", ["false", "False", "true", "0", "1", "", 0, 1, None, 1.0])
def test_boolean_flags_reject_strings_and_numbers_masquerading_as_booleans(
    name: str, bad: object
) -> None:
    with pytest.raises(ValueError, match=f"invalid_{name}"):
        _config(**{name: bad})


@pytest.mark.parametrize("name", ["max_spread", "max_spread_to_price"])
@pytest.mark.parametrize(
    "bad",
    [
        D("NaN"),
        D("sNaN"),
        D("Infinity"),
        D("-Infinity"),
        D("0"),
        D("-0.5"),
        0.5,  # float, never coerced
        1,  # int
        True,
        "1",
    ],
)
def test_spread_caps_reject_non_finite_nonpositive_and_non_decimal(name: str, bad: object) -> None:
    other = "max_spread_to_price" if name == "max_spread" else "max_spread"
    with pytest.raises(ValueError, match=f"invalid_{name}"):
        _config(**{name: bad, other: None})


def test_spread_to_price_ratio_above_one_is_nonsensical() -> None:
    with pytest.raises(ValueError, match="invalid_max_spread_to_price"):
        _config(max_spread=None, max_spread_to_price=D("1.01"))
    assert _config(max_spread=None, max_spread_to_price=D("1")).max_spread_to_price == D("1")


@pytest.mark.parametrize("bad", ["", None, 7, "has space", "ctl\x00", "x" * 129, " lead"])
def test_version_must_be_a_clean_identifier(bad: object) -> None:
    with pytest.raises(ValueError, match="missing_version|invalid_version"):
        _config(version=bad)


def test_missing_required_settings_are_rejected_not_defaulted() -> None:
    for name in FIELDS:
        partial = {k: v for k, v in FIELDS.items() if k != name}
        with pytest.raises(TypeError):
            QualityGuardConfig(**partial)  # type: ignore[arg-type]


def test_contradictory_spread_settings_are_rejected() -> None:
    # Spread caps on a quote that is allowed to be absent would silently never run.
    with pytest.raises(ValueError, match="contradictory_spread_cap_without_bid_ask"):
        _config(require_bid_ask=False)
    with pytest.raises(ValueError, match="contradictory_spread_cap_without_bid_ask"):
        _config(require_bid_ask=False, max_spread=None, max_spread_to_price=D("0.1"))
    # Required bid/ask with nothing bounding the spread would make the spread check a no-op.
    with pytest.raises(ValueError, match="unbounded_spread"):
        _config(max_spread=None, max_spread_to_price=None)
    assert _config(require_bid_ask=False, max_spread=None).require_bid_ask is False


def test_guard_revalidates_a_config_that_bypassed_construction() -> None:
    config = _config()
    object.__setattr__(config, "max_quote_age_seconds", 10**12)  # bypass frozen + __post_init__
    with pytest.raises(ValueError, match="invalid_max_quote_age_seconds"):
        DataQualityGuard(config, EXPECTED)
    config = _config()
    object.__setattr__(config, "require_bid_ask", "false")
    with pytest.raises(ValueError, match="invalid_require_bid_ask"):
        DataQualityGuard(config, EXPECTED)


@pytest.mark.parametrize(
    ("config", "expectation"),
    [
        (None, EXPECTED),
        ({"version": "x"}, EXPECTED),
        (_config(), None),
        (_config(), ("a", "b", "c")),
    ],
)
def test_guard_rejects_non_contract_inputs(config: Any, expectation: Any) -> None:
    with pytest.raises(TypeError, match="invalid_guard_inputs"):
        DataQualityGuard(config, expectation)


@pytest.mark.parametrize("bad", ["", " ", " x", "x ", "a\nb", 5, None])
def test_expectation_rejects_blank_padded_control_and_non_string_identity(bad: object) -> None:
    with pytest.raises(ValueError, match="incomplete_expectation|invalid_expectation"):
        QualityExpectation(bad, "XAUUSD", "USD/oz")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="incomplete_expectation|invalid_expectation"):
        QualityExpectation("synthetic-test", "XAUUSD", bad)  # type: ignore[arg-type]


def test_snapshot_must_be_a_tuple_of_events() -> None:
    event = tick(1, "100")
    with pytest.raises(ValueError, match="invalid_snapshot"):
        MarketDataSnapshot([event])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid_snapshot"):
        MarketDataSnapshot((event, "not-an-event"))  # type: ignore[arg-type]


def test_a_non_snapshot_argument_fails_closed_without_leaking() -> None:
    verdict = _guard().evaluate("secret-token", evaluated_at=START)  # type: ignore[arg-type]
    assert (verdict.state, verdict.new_trade_permitted) == ("unknown", False)
    assert _codes(verdict) == {m.GUARD_INTERNAL_ERROR}
    assert "secret-token" not in repr(verdict)


def test_guard_is_final_so_evaluate_cannot_be_overridden() -> None:
    with pytest.raises(TypeError, match="DataQualityGuard_is_final"):

        class Permissive(DataQualityGuard):  # noqa: B903
            pass


# Verdict construction invariants: a forged verdict cannot exist -----------------------------


def _ok_verdict() -> QualityVerdict:
    verdict = _eval(_guard(), tick(1, "100"))
    assert verdict.state == "ok"
    return verdict


def _blocked_verdict() -> QualityVerdict:
    event = replace(tick(1, "100"), bid=D("101"), ask=D("100"))
    verdict = _eval(_guard(), event)
    assert verdict.state == "blocked"
    return verdict


@pytest.mark.parametrize(
    "forge",
    [
        lambda v: replace(v, new_trade_permitted=False),  # ok but not permitted
        lambda v: replace(v, findings=(QualityFinding("x", "unknown", None, ()),)),  # ok + findings
        lambda v: replace(v, snapshot_hash=None),  # ok without a hash
        lambda v: replace(v, snapshot_hash="not-a-sha256"),
        lambda v: replace(v, snapshot_hash="A" * 64),  # upper-case hex is not canonical
        lambda v: replace(v, event_keys=()),  # ok without evidence
        lambda v: replace(v, event_keys=("a", "a")),  # duplicate keys
        lambda v: replace(v, event_keys=("",)),
        lambda v: replace(v, evaluated_at=None),
        lambda v: replace(v, evaluated_at=datetime(2026, 1, 1)),  # naive
        lambda v: replace(v, config_version=""),
        lambda v: replace(v, schema_version=True),  # bool masquerading as the int 1
        lambda v: replace(v, schema_version=2),
        lambda v: replace(v, state="ok-ish"),
        lambda v: replace(v, new_trade_permitted=1),  # int masquerading as a bool
    ],
)
def test_inconsistent_ok_verdicts_cannot_be_constructed(forge: Any) -> None:
    with pytest.raises(ValueError, match="invalid_quality_verdict"):
        forge(_ok_verdict())


@pytest.mark.parametrize(
    "forge",
    [
        lambda v: replace(v, new_trade_permitted=True),  # blocked but permitted
        lambda v: replace(v, state="ok"),  # relabelled ok while findings remain
        lambda v: replace(v, state="unknown"),  # blocking finding under unknown
        lambda v: replace(v, findings=()),  # blocked with no finding
    ],
)
def test_inconsistent_blocked_verdicts_cannot_be_constructed(forge: Any) -> None:
    with pytest.raises(ValueError, match="invalid_quality_verdict"):
        forge(_blocked_verdict())


def test_unknown_verdict_requires_findings_and_forbids_permission() -> None:
    unknown = _guard().evaluate(MarketDataSnapshot(()), evaluated_at=START)
    assert unknown.state == "unknown" and not unknown.new_trade_permitted
    with pytest.raises(ValueError, match="invalid_quality_verdict"):
        replace(unknown, findings=())
    with pytest.raises(ValueError, match="invalid_quality_verdict"):
        replace(unknown, new_trade_permitted=True)


def test_every_guard_verdict_satisfies_the_invariants_it_enforces() -> None:
    cases = [
        _eval(_guard(), tick(1, "100")),
        _eval(_guard(), replace(tick(1, "100"), bid=D("NaN"))),  # unhashable snapshot
        _guard().evaluate(MarketDataSnapshot(()), evaluated_at=START),
        _guard().evaluate(MarketDataSnapshot((tick(1, "100"),)), evaluated_at=None),
        _guard().evaluate(MarketDataSnapshot((tick(1, "100"),)), evaluated_at=datetime(2026, 1, 1)),
    ]
    for verdict in cases:
        verdict.__post_init__()  # re-run: must not raise
        assert verdict.new_trade_permitted == (verdict.state == "ok")


# Price / OHLC trust boundary ------------------------------------------------------------------


def _bar(**overrides: Any) -> NormalizedPriceEvent:
    base: dict[str, Any] = {
        "kind": "bar",
        "price_source": "close",
        "open_price": D("100"),
        "high": D("103"),
        "low": D("99"),
        "close": D("102"),
        "price": D("102"),
        "bid": None,
        "ask": None,
    }
    base.update(overrides)
    return replace(tick(1, "102"), **base)


def _bar_guard() -> DataQualityGuard:
    return _guard(require_bid_ask=False, max_spread=None)


def test_consistent_bar_is_ok() -> None:
    assert _eval(_bar_guard(), _bar()).state == "ok"


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": D("98")},  # high below low
        {"open_price": D("104")},  # open above high
        {"open_price": D("98")},  # open below low
        {"close": D("104"), "price": D("104")},  # close above high
        {"close": D("98"), "price": D("98")},  # close below low
    ],
)
def test_inconsistent_ohlc_is_blocked(overrides: dict[str, Any]) -> None:
    verdict = _eval(_bar_guard(), _bar(**overrides))
    assert verdict.state == "blocked" and not verdict.new_trade_permitted
    assert m.INVALID_OHLC in _codes(verdict)


@pytest.mark.parametrize("missing", ["open_price", "high", "low", "close"])
def test_bar_with_missing_ohlc_component_is_unknown(missing: str) -> None:
    verdict = _eval(_bar_guard(), _bar(**{missing: None}))
    assert verdict.new_trade_permitted is False
    assert m.INCOMPLETE_OHLC in _codes(verdict)


@pytest.mark.parametrize("field", ["price", "last", "open_price", "high", "low", "close"])
@pytest.mark.parametrize("bad", [D("0"), D("-1")])
def test_non_positive_prices_are_blocked(field: str, bad: D) -> None:
    event = _bar(**{field: bad, "last": D("100") if field != "last" else bad})
    verdict = _eval(_bar_guard(), event)
    assert verdict.new_trade_permitted is False
    assert m.INVALID_PRICE in _codes(verdict)


def test_price_must_match_its_declared_source_within_one_ulp() -> None:
    guard = _guard()
    clean = tick(1, "100")  # bid-sourced, precision 1
    assert _eval(guard, clean).state == "ok"
    off_by_one_ulp = replace(clean, price=clean.price + D("0.1"))
    assert _eval(guard, off_by_one_ulp).state == "ok"
    forged = replace(clean, price=D("250"))  # claims to be the bid but is not
    verdict = _eval(guard, forged)
    assert verdict.state == "blocked" and m.PRICE_SOURCE_MISMATCH in _codes(verdict)


def test_mid_price_is_checked_against_bid_and_ask() -> None:
    event = replace(tick(1, "100"), price_source="mid", price=D("100.15"))
    assert _eval(_guard(), event).state == "ok"  # bid 100, ask 100.3 -> mid 100.15
    verdict = _eval(_guard(), replace(event, price=D("105")))
    assert verdict.state == "blocked" and m.PRICE_SOURCE_MISMATCH in _codes(verdict)


def test_price_with_an_unavailable_source_value_is_unknown_not_ok() -> None:
    event = replace(tick(1, "100"), price_source="last", last=None)
    verdict = _eval(_guard(), event)
    assert verdict.new_trade_permitted is False
    assert m.PRICE_SOURCE_MISMATCH in _codes(verdict)
    assert {f.severity for f in verdict.findings if f.code == m.PRICE_SOURCE_MISMATCH} == {
        "unknown"
    }


@pytest.mark.parametrize("precision", [-1, 11, True, 1.0, "1", None])
def test_invalid_precision_is_blocked(precision: Any) -> None:
    verdict = _eval(_guard(), replace(tick(1, "100"), precision=precision))
    assert verdict.new_trade_permitted is False
    assert m.INVALID_PRECISION in _codes(verdict)


def test_unknown_event_kind_is_blocked() -> None:
    verdict = _eval(_guard(), replace(tick(1, "100"), kind="quote"))  # type: ignore[arg-type]
    assert verdict.new_trade_permitted is False
    assert m.INVALID_EVENT_KIND in _codes(verdict)


def test_guard_keeps_verdicts_deterministic_with_the_new_checks() -> None:
    events = (tick(1, "100"), tick(2, "100.4"), tick(3, "100.2"))
    snapshot = MarketDataSnapshot(events)
    now = datetime(2026, 3, 2, 9, 1, tzinfo=UTC)
    first = _guard().evaluate(snapshot, evaluated_at=now)
    assert first == _guard().evaluate(snapshot, evaluated_at=now)
    assert START <= first.evaluated_at  # type: ignore[operator]
