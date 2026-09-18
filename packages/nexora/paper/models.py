"""Paper trading contracts for local simulation only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.signals import ResearchSignal

PaperOrderStatus = Literal["filled", "rejected"]
PaperRuntimeStatus = Literal["running", "paused", "kill_switch"]
PaperLedgerEntryType = Literal["order_rejected", "fill", "checkpoint"]


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    proposal_id: str
    signal_id: str
    symbol: str
    side: Literal["long", "short"]
    requested_size: Decimal
    approved_size: Decimal
    status: PaperOrderStatus
    reason: str
    reason_codes: tuple[str, ...]
    created_at: datetime
    policy_version: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaperFill:
    fill_id: str
    order_id: str
    symbol: str
    side: Literal["long", "short"]
    size: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class PaperPosition:
    symbol: str
    quantity: Decimal
    average_price: Decimal


@dataclass(frozen=True, slots=True)
class PaperLedgerEntry:
    entry_id: str
    entry_type: PaperLedgerEntryType
    proposal_id: str
    order_id: str
    amount: Decimal
    balance_after: Decimal
    detail: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaperCheckpoint:
    checkpoint_id: str
    namespace: str
    sequence: int
    status: PaperRuntimeStatus
    cash: Decimal
    realized_pnl: Decimal
    created_at: datetime
    seen_proposals: tuple[str, ...]
    positions: tuple[PaperPosition, ...]
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    ledger: tuple[PaperLedgerEntry, ...]


@dataclass(frozen=True, slots=True)
class PaperState:
    namespace: str
    status: PaperRuntimeStatus
    sequence: int
    cash: Decimal
    realized_pnl: Decimal
    open_positions: tuple[PaperPosition, ...]
    seen_proposals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaperExecution:
    order: PaperOrder
    fill: PaperFill | None


@dataclass(frozen=True, slots=True)
class PaperReplayResult:
    signals: tuple[ResearchSignal, ...]
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    ledger: tuple[PaperLedgerEntry, ...]
    checkpoints: tuple[PaperCheckpoint, ...]
    state: PaperState
