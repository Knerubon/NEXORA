"""Durable single-account paper session, rebuilt from committed proposals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from threading import RLock
from typing import Any, cast

from nexora.artifacts import canonical_hash, canonical_serialize, decode
from nexora.paper.simulator import PaperSimulator
from nexora.risk import AccountSnapshot, PriceSnapshot, RiskEngine, RiskPolicy, RiskProposal
from nexora.signals import ResearchSignal
from nexora.storage import Journal


@dataclass(frozen=True)
class PaperSessionConfig:
    namespace: str
    account_id: str
    starting_cash: Decimal
    fee_per_unit: Decimal
    slippage: Decimal
    policy: RiskPolicy
    requested_size: Decimal
    stop_distance: Decimal
    implementation_version: str = "paper-session-v2"

    def __post_init__(self) -> None:
        if any(not x.is_finite() or x <= 0 for x in (self.requested_size, self.stop_distance)):
            raise ValueError("invalid_paper_sizing")


class PaperSession:
    def __init__(self, config: PaperSessionConfig, journal: Journal) -> None:
        self.config, self.journal = config, journal
        self._lock = RLock()
        self.stream = f"paper:{config.namespace}"
        journal.append(self.stream + ":config", "config", config)
        self._rebuild()

    def _rebuild(self) -> None:
        cfg = self.config
        self.risk = RiskEngine(cfg.policy)
        self.simulator = PaperSimulator(
            namespace=cfg.namespace,
            account_id=cfg.account_id,
            starting_cash=cfg.starting_cash,
            fee_per_unit=cfg.fee_per_unit,
            slippage=cfg.slippage,
        )
        self._active: list[str] = []
        self._settled_pnl = Decimal("0")
        self._count = 0
        self._symbol: str | None = None
        for row in self.journal.read(self.stream):
            if "control" in row:
                self._control(str(row["control"]))
            else:
                self._apply(decode(RiskProposal, row["proposal"]))
            self._count += 1

    def _apply(self, proposal: RiskProposal) -> None:
        if self._symbol not in (None, proposal.signal.symbol):
            raise ValueError("paper_session_symbol_mismatch")
        self._symbol = proposal.signal.symbol
        decision = self.risk.evaluate(proposal)
        price = proposal.price.price
        if price is None:
            raise ValueError("missing_paper_price")
        execution = self.simulator.apply_decision(
            decision=decision,
            signal=proposal.signal,
            market_price=price,
            event_time=proposal.signal.decision_time,
        )
        if execution.fill is None and decision.action == "allow":
            # A paused simulator did not open a position; do not strand a reservation.
            self.risk.release(proposal.proposal_id, realized_pnl=Decimal("0"))
        if execution.fill is not None:
            self._active.append(proposal.proposal_id)
            if not self.simulator.state().open_positions:
                total = sum((f.realized_pnl - f.fee for f in self.simulator.fills()), Decimal("0"))
                for index, proposal_id in enumerate(self._active):
                    self.risk.release(
                        proposal_id,
                        realized_pnl=total - self._settled_pnl if index == 0 else Decimal("0"),
                    )
                self._settled_pnl = total
                self._active = []

    def submit(self, signal: ResearchSignal, *, price: Decimal, quality: str) -> None:
        with self._lock:
            if self._symbol not in (None, signal.symbol):
                raise ValueError("paper_session_symbol_mismatch")
            # A repeated signal returns the original committed outcome.
            key = f"signal:{signal.signal_id}"
            rows = self.journal.read(self.stream)
            for row in rows:
                if row.get("signal_id") == signal.signal_id:
                    if row["signal_hash"] != canonical_hash(signal):
                        raise ValueError("signal_identity_conflict")
                    return
            state = self.simulator.state()
            equity = state.cash + sum(
                (p.quantity * price for p in state.open_positions), Decimal("0")
            )
            proposal = RiskProposal(
                proposal_id=key,
                signal=signal,
                stop_distance=self.config.stop_distance,
                requested_size=self.config.requested_size,
                quality_status=quality,
                account=AccountSnapshot(
                    account_id=self.config.account_id,
                    currency=self.config.policy.currency,
                    equity=equity,
                    balance=state.cash,
                    exposure_in_use=Decimal("0"),
                    observed_at=signal.decision_time,
                ),
                price=PriceSnapshot(signal.symbol, price, "live", signal.decision_time),
            )
            try:
                self._apply(proposal)
                self.journal.append(
                    self.stream,
                    key,
                    {
                        "proposal": canonical_serialize(proposal),
                        "signal_id": signal.signal_id,
                        "signal_hash": canonical_hash(signal),
                    },
                    expected_count=self._count,
                )
                self._count += 1
            except Exception:
                self._rebuild()
                raise

    def _control(self, action: str) -> None:
        if self.simulator.state().status == "kill_switch" and action != "kill":
            raise ValueError("paper_kill_switch_latched")
        if action == "pause":
            self.simulator.pause()
        elif action == "resume":
            self.simulator.resume()
        elif action == "kill":
            self.simulator.set_kill_switch(True)
            self.risk.activate_kill_switch()
        else:
            raise ValueError("invalid_paper_control")

    def control(self, action: str) -> None:
        with self._lock:
            try:
                self._control(action)
                self.journal.append(
                    self.stream,
                    f"control:{self._count}",
                    {"control": action},
                    expected_count=self._count,
                )
                self._count += 1
            except Exception:
                self._rebuild()
                raise

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return cast(
                dict[str, Any],
                canonical_serialize(
                    {
                        "status": self.simulator.state().status,
                        "state": asdict(self.simulator.state()),
                        "orders": self.simulator.orders(),
                        "fills": self.simulator.fills(),
                        "ledger": self.simulator.ledger(),
                        "risk_state": self.risk.state(),
                        "decisions": self.risk.decisions(),
                        "backend": self.journal.backend,
                        "accepted": len(self.simulator.fills()),
                        "rejected": sum(o.status == "rejected" for o in self.simulator.orders()),
                    }
                ),
            )
