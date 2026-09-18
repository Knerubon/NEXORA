from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nexora.backtest.service import BacktestLabService
from nexora.paper import PaperSimulator
from nexora.risk import RiskEngine, proposal_fixture, risk_policy_fixture, signal_fixture


def test_paper_service_does_not_create_fixture_session() -> None:
    service = BacktestLabService.bootstrap()
    replay = service.paper_replay()
    assert replay["status"] == "unavailable"
    assert replay["fills"] == []
    assert service.list_runs() == ()


def test_paper_simulator_avoids_duplicate_fill_after_restart() -> None:
    policy = risk_policy_fixture()
    engine = RiskEngine(policy)
    proposal = proposal_fixture(proposal_id="paper-1")
    decision = engine.evaluate(proposal)
    signal = proposal.signal

    simulator = PaperSimulator(
        namespace="paper-main",
        starting_cash=Decimal("10000"),
        fee_per_unit=Decimal("0.05"),
        slippage=Decimal("0.10"),
    )
    first = simulator.apply_decision(
        decision=decision,
        signal=signal,
        market_price=Decimal("101.0"),
        event_time=proposal.signal.decision_time,
    )
    duplicate = simulator.apply_decision(
        decision=decision,
        signal=signal,
        market_price=Decimal("101.0"),
        event_time=proposal.signal.decision_time,
    )
    checkpoint = simulator.checkpoint(
        checkpoint_id="paper-main:checkpoint:1",
        created_at=datetime(2026, 3, 6, 9, 1, tzinfo=UTC),
    )
    recovered = PaperSimulator.from_checkpoint(
        checkpoint,
        fee_per_unit=Decimal("0.05"),
        slippage=Decimal("0.10"),
    )
    recovered_execution = recovered.apply_decision(
        decision=decision,
        signal=signal,
        market_price=Decimal("101.0"),
        event_time=datetime(2026, 3, 6, 9, 2, tzinfo=UTC),
    )

    assert first.fill is not None
    assert duplicate.fill is not None
    assert recovered_execution.fill is not None
    assert len(simulator.fills()) == 1
    assert len(recovered.fills()) == 1


def test_paper_simulator_blocks_fill_for_reject_and_kill_switch() -> None:
    policy = risk_policy_fixture()
    engine = RiskEngine(policy)
    reject_decision = engine.evaluate(
        proposal_fixture(
            proposal_id="paper-reject",
            quality_status="unknown",
        )
    )

    simulator = PaperSimulator(
        namespace="paper-main",
        starting_cash=Decimal("10000"),
        fee_per_unit=Decimal("0.05"),
        slippage=Decimal("0.10"),
    )
    rejected_execution = simulator.apply_decision(
        decision=reject_decision,
        signal=signal_fixture(signal_id="reject-signal"),
        market_price=Decimal("100.0"),
        event_time=datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
    )
    simulator.set_kill_switch(True)
    allow_decision = engine.evaluate(proposal_fixture(proposal_id="paper-allow"))
    blocked_execution = simulator.apply_decision(
        decision=allow_decision,
        signal=signal_fixture(),
        market_price=Decimal("100.0"),
        event_time=signal_fixture().decision_time,
    )

    assert rejected_execution.fill is None
    assert blocked_execution.fill is None
    assert all(order.status == "rejected" for order in simulator.orders())
