"""Decision Audit / Data Quality Guard hardening round 2 (ADR-036): regression tests.

Reproduces the findings of the second independent review of PR #80:

* F-A1 (MAJOR) - ``float`` prices bypassed the price / OHLC / price-source trust boundary
* F-A2 - sequence gaps inside a snapshot were detected only through the upstream ``is_gap`` flag
* F-A3 - token-shaped values were persisted through ``reference`` / ``token`` fields
* F-A4 - ``received_at`` was not bounded by ``evaluated_at``

Secret-shaped strings below are synthetic; none is a real credential.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from nexora.data_quality import (
    DataQualityGuard,
    MarketDataSnapshot,
    QualityExpectation,
    QualityGuardConfig,
)
from nexora.data_quality import models as m
from nexora.decision_audit import AuditGateOutcome, DecisionAuditGate, DecisionAuditStore
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.storage import SQLiteJournal

from tests.validation_fixtures import pipeline_config, tick, tick_events

CONFIG = QualityGuardConfig(
    version="dq-guard-round2-v1",
    max_quote_age_seconds=60,
    max_future_skew_seconds=5,
    max_latency_ms=3000,
    max_gap_seconds=120,
    min_events=1,
    require_bid_ask=True,
    allow_zero_spread=False,
    max_spread=D("1"),
    max_spread_to_price=None,
)
EXPECTED = QualityExpectation("synthetic-test", "XAUUSD", "USD/oz")
BUY_INDEX = 7  # first BUY in the fixture
ENV = "development"
# Synthetic, token-shaped (GitHub-PAT-like) value: 4-char prefix + 40 mixed-case alphanumerics.
FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


def _guard() -> DataQualityGuard:
    return DataQualityGuard(CONFIG, EXPECTED)


def _evaluate(*events: NormalizedPriceEvent, at: Any = None) -> Any:
    return _guard().evaluate(
        MarketDataSnapshot(tuple(events)),
        evaluated_at=events[-1].received_at if at is None else at,
    )


def _codes(verdict: Any) -> set[str]:
    return {f.code for f in verdict.findings}


def _bar(**overrides: Any) -> NormalizedPriceEvent:
    base = replace(
        tick(1, "100"),
        kind="bar",
        price_source="close",
        open_price=D("100"),
        high=D("101"),
        low=D("99"),
        close=D("100"),
    )
    return replace(base, **overrides)


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[SQLiteJournal]:
    store = SQLiteJournal(tmp_path / "round2.sqlite")
    yield store
    store.close()


def _gate(journal: SQLiteJournal) -> DecisionAuditGate:
    return DecisionAuditGate(DecisionAuditStore(journal, environment=ENV), _guard())


def _step(index: int = BUY_INDEX) -> tuple[NormalizedPriceEvent, dict[str, Any]]:
    pipeline = ResearchPipeline(pipeline_config())
    output: dict[str, Any] = {}
    events = tick_events(index + 1)
    for event in events:
        output = pipeline.process(event)
    return events[-1], output


def _decide(journal: SQLiteJournal, event: NormalizedPriceEvent, output: dict[str, Any]) -> Any:
    return _gate(journal).record_decision(
        output=output,
        event=event,
        snapshot=MarketDataSnapshot((event,)),
        evaluated_at=event.received_at,
    )


# =============================================================================================
# F-A1 - float prices must never pass the Decimal-only trust boundary
# =============================================================================================


def test_baseline_fixtures_are_clean() -> None:
    assert _evaluate(tick(1, "100")).state == "ok"
    assert _evaluate(_bar()).state == "ok"


@pytest.mark.parametrize(
    "field", ["price", "bid", "ask", "last", "open_price", "high", "low", "close"]
)
@pytest.mark.parametrize("value", [100.0, -5.0, 0.0, float("nan"), float("inf"), True, 100])
def test_non_decimal_price_fields_are_blocked(field: str, value: object) -> None:
    changes: dict[str, Any] = {field: value}
    event = replace(_bar(), **changes)
    verdict = _evaluate(event)
    assert verdict.state == "blocked" and verdict.new_trade_permitted is False
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)


def test_negative_float_price_is_blocked() -> None:
    event = replace(tick(1, "100"), price=-5.0, last=-5.0, price_source="last")  # type: ignore[arg-type]
    verdict = _evaluate(event)
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)


def test_float_price_disagreeing_with_its_source_is_blocked() -> None:
    event = replace(tick(1, "100"), price=12345.0)  # type: ignore[arg-type]
    assert _evaluate(event).state == "blocked"
    # Same value as a Decimal is caught by the price-source check, as before.
    assert m.PRICE_SOURCE_MISMATCH in _codes(_evaluate(replace(tick(1, "100"), price=D("12345"))))


def test_float_bar_with_inverted_range_is_blocked() -> None:
    event = _bar(open_price=50.0, high=10.0, low=90.0, close=50.0, price=50.0)
    assert _evaluate(event).state == "blocked"


def test_float_bid_ask_is_reported_not_turned_into_a_guard_error() -> None:
    verdict = _evaluate(replace(tick(1, "100"), bid=100.0, ask=100.3))  # type: ignore[arg-type]
    assert verdict.state == "blocked"
    assert m.INVALID_NUMERIC_TYPE in _codes(verdict)
    assert m.GUARD_INTERNAL_ERROR not in _codes(verdict)


def test_float_price_event_never_reaches_recorded_eligible(tmp_path: Path) -> None:
    event, output = _step()
    forged = replace(event, price=-5.0, last=-5.0, price_source="last")  # type: ignore[arg-type]
    for name, candidate, expected in (
        ("clean", event, AuditGateOutcome.RECORDED_ELIGIBLE),
        ("forged", forged, AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY),
    ):
        store = SQLiteJournal(tmp_path / f"{name}.sqlite")  # fresh journal: no replay/conflict
        try:
            result = _decide(store, candidate, output)
        finally:
            store.close()
        assert result.outcome is expected
        assert result.new_trade_eligible is (expected is AuditGateOutcome.RECORDED_ELIGIBLE)


# =============================================================================================
# F-A2 - sequence contiguity is re-checked inside the snapshot
# =============================================================================================


def test_sequence_gap_is_detected_without_the_upstream_flag() -> None:
    first = tick(1, "100")
    jump = replace(tick(2, "100.1"), source_sequence=500)
    assert jump.is_gap is False
    verdict = _evaluate(first, jump)
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)
    gaps = [f for f in verdict.findings if f.code == m.SEQUENCE_GAP]
    assert gaps and dict(gaps[0].evidence) == {"prev": "1", "cur": "500"}


def test_contiguous_sequences_stay_ok() -> None:
    assert _evaluate(*tick_events(5)).state == "ok"


# =============================================================================================
# F-A3 - token-shaped values never reach the journal through reference / token fields
# =============================================================================================


def _with_source_ref(value: str) -> tuple[NormalizedPriceEvent, dict[str, Any]]:
    event, output = _step()
    broken = copy.deepcopy(output)
    positives = broken["signals"]["decision"]["positive_evidence"]
    positives[0]["source_refs"] = [*positives[0].get("source_refs", ()), value]
    return event, broken


@pytest.mark.parametrize(
    "value",
    [
        FAKE_TOKEN,
        "ref:" + FAKE_TOKEN,
        "A" * 40,
        "0123456789ABCDEF" * 4,  # uppercase hex is not one of our digests
        "f" * 65,  # longer than a digest
    ],
)
def test_token_shaped_evidence_reference_is_rejected_and_not_persisted(
    journal: SQLiteJournal, value: str
) -> None:
    event, output = _with_source_ref(value)
    result = _decide(journal, event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"
    assert result.new_trade_eligible is False
    assert value not in repr(result)


def test_digest_shaped_evidence_reference_is_still_accepted(journal: SQLiteJournal) -> None:
    event, output = _with_source_ref("sha:" + "0f" * 32)
    assert _decide(journal, event, output).outcome is AuditGateOutcome.RECORDED_ELIGIBLE


def test_token_shaped_version_field_is_rejected(journal: SQLiteJournal) -> None:
    event, output = _step()
    broken = copy.deepcopy(output)
    broken["config_version"] = FAKE_TOKEN
    result = _decide(journal, event, broken)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"


# =============================================================================================
# F-A4 - received_at cannot be later than the evaluation time
# =============================================================================================


def test_received_after_evaluation_is_a_clock_anomaly() -> None:
    event = tick(1, "100")
    late = event.received_at - timedelta(seconds=CONFIG.max_future_skew_seconds + 1)
    verdict = _evaluate(event, at=late)
    assert (verdict.state, verdict.new_trade_permitted) == ("blocked", False)
    assert m.CLOCK_ANOMALY_RECEIVED_AFTER_EVALUATION in _codes(verdict)


def test_received_within_skew_of_evaluation_is_ok() -> None:
    event = tick(1, "100")
    at = event.received_at - timedelta(seconds=CONFIG.max_future_skew_seconds)
    assert _evaluate(event, at=at).state == "ok"
