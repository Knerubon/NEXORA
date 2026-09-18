"""Local paper simulator consuming P11 risk decisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from nexora.paper.models import (
    PaperCheckpoint,
    PaperExecution,
    PaperFill,
    PaperLedgerEntry,
    PaperLedgerEntryType,
    PaperOrder,
    PaperPosition,
    PaperRuntimeStatus,
    PaperState,
)
from nexora.risk import RiskDecision
from nexora.risk.models import signal_fingerprint
from nexora.signals import ResearchSignal


class PaperInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PaperSimulator:
    def __init__(
        self,
        *,
        namespace: str,
        starting_cash: Decimal,
        fee_per_unit: Decimal,
        slippage: Decimal,
        account_id: str = "paper-account",
    ) -> None:
        if not namespace.startswith("paper-"):
            raise PaperInputError("invalid_namespace")
        if not account_id:
            raise PaperInputError("invalid_account_id")
        if not starting_cash.is_finite() or starting_cash <= 0:
            raise PaperInputError("invalid_starting_cash")
        if not fee_per_unit.is_finite() or fee_per_unit < 0:
            raise PaperInputError("invalid_fee_per_unit")
        if not slippage.is_finite() or slippage < 0:
            raise PaperInputError("invalid_slippage")
        self.namespace = namespace
        self.account_id = account_id
        self._cash = starting_cash
        self._realized_pnl = Decimal("0")
        self._status: PaperRuntimeStatus = "running"
        self._sequence = 0
        self._positions: dict[str, PaperPosition] = {}
        self._orders: dict[str, PaperOrder] = {}
        self._fills: dict[str, PaperFill] = {}
        self._ledger: list[PaperLedgerEntry] = []
        self._seen_proposals: tuple[str, ...] = ()
        self._fee_per_unit = fee_per_unit
        self._slippage = slippage

    def pause(self) -> None:
        self._status = "paused"

    def resume(self) -> None:
        self._status = "running"

    def set_kill_switch(self, active: bool) -> None:
        self._status = "kill_switch" if active else "running"

    def apply_decision(
        self,
        *,
        decision: RiskDecision,
        signal: ResearchSignal,
        market_price: Decimal,
        event_time: datetime,
    ) -> PaperExecution:
        if not market_price.is_finite() or market_price <= 0:
            raise PaperInputError("invalid_market_price")
        if decision.proposal_id in self._orders:
            existing = self._orders[decision.proposal_id]
            return PaperExecution(order=existing, fill=self._fills.get(decision.proposal_id))

        if event_time.tzinfo is None or event_time.utcoffset() is None:
            raise PaperInputError("timezone_required")
        binding_error = (
            decision.signal_id != signal.signal_id
            or decision.signal_hash != signal_fingerprint(signal)
            or decision.symbol != signal.symbol
            or decision.side != signal.side
            or decision.account_id != self.account_id
            or decision.expires_at is None
            or event_time < decision.effective_time
            or event_time > decision.expires_at
            or signal.status != "active"
        )
        if decision.action == "allow" and binding_error:
            raise PaperInputError("invalid_approval_binding_or_time")
        self._sequence += 1
        created_at = event_time.astimezone(UTC)
        blocked = self._status in {"paused", "kill_switch"} or decision.action == "reject"
        if blocked:
            reason = decision.reason if decision.action == "reject" else "paper_runtime_blocked"
            reason_codes = (
                decision.reason_codes
                if decision.action == "reject"
                else (self._status, "runtime_blocked")
            )
            order = PaperOrder(
                order_id=f"{self.namespace}:order:{decision.proposal_id}",
                proposal_id=decision.proposal_id,
                signal_id=decision.signal_id,
                symbol=signal.symbol,
                side=signal.side,
                requested_size=decision.approved_size,
                approved_size=Decimal("0"),
                status="rejected",
                reason=reason,
                reason_codes=reason_codes,
                created_at=created_at,
                policy_version=decision.policy_version,
                source_refs=decision.source_refs,
            )
            self._orders[decision.proposal_id] = order
            self._seen_proposals = self._seen_proposals + (decision.proposal_id,)
            self._append_ledger(
                entry_type="order_rejected",
                proposal_id=decision.proposal_id,
                order_id=order.order_id,
                amount=Decimal("0"),
                detail=order.reason,
                created_at=created_at,
            )
            return PaperExecution(order=order, fill=None)

        if decision.approved_size <= 0:
            raise PaperInputError("invalid_approved_size")
        signed_size = decision.approved_size if signal.side == "long" else -decision.approved_size
        executed_price = (
            market_price + self._slippage
            if signal.side == "long"
            else market_price - self._slippage
        )
        if executed_price <= 0:
            raise PaperInputError("invalid_executed_price")
        fee = decision.approved_size * self._fee_per_unit
        self._cash = self._cash - (signed_size * executed_price) - fee
        realized = self._apply_fill(
            symbol=signal.symbol,
            signed_size=signed_size,
            fill_price=executed_price,
        )
        self._realized_pnl += realized

        order = PaperOrder(
            order_id=f"{self.namespace}:order:{decision.proposal_id}",
            proposal_id=decision.proposal_id,
            signal_id=decision.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            requested_size=decision.approved_size,
            approved_size=decision.approved_size,
            status="filled",
            reason="filled",
            reason_codes=("paper_fill",),
            created_at=created_at,
            policy_version=decision.policy_version,
            source_refs=decision.source_refs,
        )
        fill = PaperFill(
            fill_id=f"{self.namespace}:fill:{decision.proposal_id}",
            order_id=order.order_id,
            symbol=signal.symbol,
            side=signal.side,
            size=decision.approved_size,
            price=executed_price,
            fee=fee,
            realized_pnl=realized,
            filled_at=created_at,
        )
        self._orders[decision.proposal_id] = order
        self._fills[decision.proposal_id] = fill
        self._seen_proposals = self._seen_proposals + (decision.proposal_id,)
        self._append_ledger(
            entry_type="fill",
            proposal_id=decision.proposal_id,
            order_id=order.order_id,
            amount=fill.realized_pnl - fill.fee,
            detail=order.reason,
            created_at=created_at,
        )
        return PaperExecution(order=order, fill=fill)

    def checkpoint(self, *, checkpoint_id: str, created_at: datetime) -> PaperCheckpoint:
        checkpoint = PaperCheckpoint(
            checkpoint_id=checkpoint_id,
            namespace=self.namespace,
            sequence=self._sequence,
            status=self._status,
            cash=self._cash,
            realized_pnl=self._realized_pnl,
            created_at=created_at.astimezone(UTC),
            seen_proposals=self._seen_proposals,
            positions=self._positions_tuple(),
            orders=self.orders(),
            fills=self.fills(),
            ledger=tuple(self._ledger),
            account_id=self.account_id,
        )
        self._append_ledger(
            entry_type="checkpoint",
            proposal_id=checkpoint_id,
            order_id=f"{self.namespace}:checkpoint",
            amount=Decimal("0"),
            detail="checkpoint",
            created_at=created_at.astimezone(UTC),
        )
        return checkpoint

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: PaperCheckpoint,
        *,
        fee_per_unit: Decimal,
        slippage: Decimal,
    ) -> PaperSimulator:
        simulator = cls(
            namespace=checkpoint.namespace,
            starting_cash=max(checkpoint.cash, Decimal("1")),
            fee_per_unit=fee_per_unit,
            slippage=slippage,
            account_id=checkpoint.account_id,
        )
        simulator._cash = checkpoint.cash
        simulator._realized_pnl = checkpoint.realized_pnl
        simulator._status = checkpoint.status
        simulator._sequence = checkpoint.sequence
        simulator._positions = {position.symbol: position for position in checkpoint.positions}
        simulator._orders = {order.proposal_id: order for order in checkpoint.orders}
        simulator._fills = {fill.fill_id.rsplit(":fill:", 1)[-1]: fill for fill in checkpoint.fills}
        simulator._ledger = list(checkpoint.ledger)
        simulator._seen_proposals = checkpoint.seen_proposals
        return simulator

    def state(self) -> PaperState:
        return PaperState(
            namespace=self.namespace,
            status=self._status,
            sequence=self._sequence,
            cash=self._cash,
            realized_pnl=self._realized_pnl,
            open_positions=self._positions_tuple(),
            seen_proposals=self._seen_proposals,
        )

    def orders(self) -> tuple[PaperOrder, ...]:
        return tuple(self._orders[item] for item in self._seen_proposals)

    def fills(self) -> tuple[PaperFill, ...]:
        filled = [proposal_id for proposal_id in self._seen_proposals if proposal_id in self._fills]
        return tuple(self._fills[item] for item in filled)

    def ledger(self) -> tuple[PaperLedgerEntry, ...]:
        return tuple(self._ledger)

    def _apply_fill(self, *, symbol: str, signed_size: Decimal, fill_price: Decimal) -> Decimal:
        current = self._positions.get(
            symbol,
            PaperPosition(
                symbol=symbol,
                quantity=Decimal("0"),
                average_price=Decimal("0"),
            ),
        )
        if (
            current.quantity == 0
            or (current.quantity > 0 and signed_size > 0)
            or (current.quantity < 0 and signed_size < 0)
        ):
            total_abs = abs(current.quantity) + abs(signed_size)
            weighted = (abs(current.quantity) * current.average_price) + (
                abs(signed_size) * fill_price
            )
            average_price = weighted / total_abs
            self._positions[symbol] = replace(
                current,
                quantity=current.quantity + signed_size,
                average_price=average_price,
            )
            return Decimal("0")

        closing = min(abs(current.quantity), abs(signed_size))
        realized = (
            (fill_price - current.average_price) * closing
            if current.quantity > 0
            else (current.average_price - fill_price) * closing
        )
        new_quantity = current.quantity + signed_size
        if new_quantity == 0:
            self._positions[symbol] = replace(
                current,
                quantity=Decimal("0"),
                average_price=Decimal("0"),
            )
            return realized
        if (current.quantity > 0 > new_quantity) or (current.quantity < 0 < new_quantity):
            self._positions[symbol] = replace(
                current,
                quantity=new_quantity,
                average_price=fill_price,
            )
            return realized
        self._positions[symbol] = replace(current, quantity=new_quantity)
        return realized

    def _append_ledger(
        self,
        *,
        entry_type: PaperLedgerEntryType,
        proposal_id: str,
        order_id: str,
        amount: Decimal,
        detail: str,
        created_at: datetime,
    ) -> None:
        entry = PaperLedgerEntry(
            entry_id=f"{self.namespace}:ledger:{len(self._ledger) + 1}",
            entry_type=entry_type,
            proposal_id=proposal_id,
            order_id=order_id,
            amount=amount,
            balance_after=self._cash,
            detail=detail,
            created_at=created_at,
        )
        self._ledger.append(entry)

    def _positions_tuple(self) -> tuple[PaperPosition, ...]:
        non_zero = [position for position in self._positions.values() if position.quantity != 0]
        return tuple(sorted(non_zero, key=lambda item: item.symbol))
