from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from nexora.risk import RiskDecisionStore, RiskEngine, proposal_fixture, risk_policy_fixture


def test_risk_engine_allows_and_rejects_on_thresholds() -> None:
    engine = RiskEngine(risk_policy_fixture())

    allowed = engine.evaluate(
        proposal_fixture(
            proposal_id="allow-1",
            requested_size="2.0",
            stop_distance="1.0",
            exposure_in_use="0",
        )
    )
    assert allowed.action == "allow"
    assert allowed.approved_size == Decimal("2.0")

    rejected = engine.evaluate(
        proposal_fixture(
            proposal_id="reject-exposure",
            requested_size="5.0",
            stop_distance="100.0",
            exposure_in_use="350",
        )
    )
    assert rejected.action == "reject"
    assert "max_total_exposure" in rejected.reason_codes


def test_risk_engine_rejects_stale_missing_or_unknown_inputs() -> None:
    engine = RiskEngine(risk_policy_fixture())
    stale = engine.evaluate(
        proposal_fixture(
            proposal_id="stale-price",
            quality_status="complete",
            price_status="stale",
        )
    )
    unknown = engine.evaluate(
        proposal_fixture(
            proposal_id="unknown-quality",
            quality_status="unknown",
        )
    )
    currency = engine.evaluate(
        proposal_fixture(
            proposal_id="bad-currency",
            currency="THB",
        )
    )
    assert stale.action == "reject"
    assert unknown.action == "reject"
    assert currency.action == "reject"


def test_risk_engine_kill_switch_daily_reset_and_idempotency() -> None:
    engine = RiskEngine(risk_policy_fixture())
    proposal = proposal_fixture(proposal_id="idempotent")

    first = engine.evaluate(proposal)
    second = engine.evaluate(proposal)
    assert first == second

    engine.activate_kill_switch()
    killed = engine.evaluate(proposal_fixture(proposal_id="killed"))
    assert killed.action == "reject"
    assert engine.state().status == "kill_switch"
    engine.deactivate_kill_switch()

    engine.release("idempotent", realized_pnl=Decimal("-40"))
    next_day_account = replace(
        proposal.account,
        observed_at=proposal.account.observed_at + timedelta(days=1),
    )
    next_day = engine.evaluate(
        replace(
            proposal_fixture(proposal_id="next-day"),
            account=next_day_account,
            price=replace(proposal.price, observed_at=next_day_account.observed_at),
            signal=replace(proposal.signal, decision_time=next_day_account.observed_at),
        )
    )
    assert next_day.action in {"allow", "reject"}
    expected_day = (proposal.account.observed_at + timedelta(days=1)).date().isoformat()
    assert engine.state().trading_day == expected_day


def test_risk_store_replay_is_deterministic() -> None:
    store = RiskDecisionStore()
    engine = RiskEngine(risk_policy_fixture())
    allowed = engine.evaluate(proposal_fixture(proposal_id="store-allow"))
    rejected = engine.evaluate(
        proposal_fixture(
            proposal_id="store-reject",
            quality_status="unknown",
        )
    )
    store.append_decision(allowed)
    store.append_decision(rejected)
    store.append_state(engine.state())

    decisions = store.replay_decisions()
    assert decisions[0]["proposal_id"] == "store-allow"
    assert decisions[1]["proposal_id"] == "store-reject"
    assert store.replay_states()[0]["policy_version"] == "p11-risk-v1"
