"""Backtest lab service for API consumption."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, cast

from nexora.backtest.fixtures import fixture_config, fixture_dataset_manifest, fixture_signals
from nexora.backtest.hashing import canonical_hash
from nexora.backtest.repository import BacktestRunStore
from nexora.backtest.runner import BacktestRunner
from nexora.paper import PaperLedgerStore, PaperSimulator
from nexora.risk import (
    AccountSnapshot,
    PriceSnapshot,
    RiskEngine,
    RiskProposal,
    replay_signals_with_risk,
    risk_policy_fixture,
)


@dataclass(slots=True)
class BacktestLabService:
    runner: BacktestRunner
    store: BacktestRunStore

    @classmethod
    def bootstrap(cls) -> BacktestLabService:
        service = cls(runner=BacktestRunner(), store=BacktestRunStore())
        service.seed_default_runs()
        return service

    def seed_default_runs(self) -> None:
        if self.store.list_runs():
            return
        dataset = fixture_dataset_manifest()
        dataset_hash = canonical_hash(dataset)
        signals = fixture_signals()
        for mode in ("baseline", "fixed_pnf", "adaptive_pnf"):
            config = fixture_config(mode)
            run = self.runner.run(
                dataset=dataset,
                config=config,
                expected_dataset_hash=dataset_hash,
                signals=signals,
            )
            self.store.append(run)

    def list_runs(self) -> tuple[dict[str, object], ...]:
        return self.store.list_runs()

    def compare(self, run_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        return self.store.compare(run_ids)

    def risk_replay(self) -> dict[str, object]:
        engine = RiskEngine(risk_policy_fixture())
        result = replay_signals_with_risk(
            engine=engine,
            signals=fixture_signals(),
            quality_status="complete",
            starting_equity=Decimal("10000"),
        )
        return {
            "accepted": result.accepted,
            "rejected": result.rejected,
            "decisions": [asdict(decision) for decision in result.decisions],
            "state": asdict(engine.state()),
        }

    def paper_replay(self) -> dict[str, object]:
        policy = risk_policy_fixture()
        risk_engine = RiskEngine(policy)
        simulator = PaperSimulator(
            namespace="paper-main",
            starting_cash=Decimal("10000"),
            fee_per_unit=Decimal("0.05"),
            slippage=Decimal("0.10"),
        )
        store = PaperLedgerStore()
        signals = fixture_signals()
        for index, signal in enumerate(signals, 1):
            observed_at = signal.decision_time
            proposal = RiskProposal(
                proposal_id=f"paper-{index}:{signal.signal_id}",
                signal=signal,
                stop_distance=Decimal("1.0"),
                requested_size=Decimal("1.0"),
                quality_status="unknown" if index == len(signals) else "complete",
                account=AccountSnapshot(
                    account_id="paper-main",
                    currency=policy.currency,
                    equity=Decimal("10000"),
                    balance=Decimal("10000"),
                    exposure_in_use=Decimal("0"),
                    observed_at=observed_at,
                ),
                price=PriceSnapshot(
                    symbol=signal.symbol,
                    price=Decimal("100.0") + Decimal(index),
                    status="live",
                    observed_at=observed_at,
                ),
            )
            decision = risk_engine.evaluate(proposal)
            execution = simulator.apply_decision(
                decision=decision,
                signal=signal,
                market_price=Decimal("100.0") + Decimal(index),
                event_time=observed_at,
            )
            store.append_order(execution.order)
            if execution.fill is not None:
                store.append_fill(execution.fill)
                risk_engine.release(
                    proposal.proposal_id,
                    realized_pnl=execution.fill.realized_pnl - execution.fill.fee,
                )
        checkpoint = simulator.checkpoint(
            checkpoint_id="paper-main:checkpoint:1",
            created_at=signals[-1].decision_time,
        )
        store.append_checkpoint(checkpoint)
        store.append_state(simulator.state())
        for entry in simulator.ledger():
            store.append_ledger_entry(entry)
        return {
            "accepted": sum(1 for order in simulator.orders() if order.status == "filled"),
            "rejected": sum(1 for order in simulator.orders() if order.status == "rejected"),
            "orders": [asdict(order) for order in simulator.orders()],
            "fills": [asdict(fill) for fill in simulator.fills()],
            "ledger": [asdict(entry) for entry in simulator.ledger()],
            "state": asdict(simulator.state()),
            "risk_state": asdict(risk_engine.state()),
            "checkpoints": store.replay_checkpoints(),
        }

    def paper_snapshot(self) -> dict[str, object]:
        replay = self.paper_replay()
        state = cast(dict[str, Any], replay["state"])
        fills = cast(list[dict[str, Any]], replay["fills"])
        return {
            "status": state["status"],
            "accepted": cast(int, replay["accepted"]),
            "rejected": cast(int, replay["rejected"]),
            "fills": len(fills),
        }
