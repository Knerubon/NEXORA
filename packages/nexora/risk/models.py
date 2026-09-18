"""Risk decision contracts for research and paper simulation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from nexora.signals import ResearchSignal

DecisionAction = Literal["allow", "reject"]
RiskStatus = Literal["ready", "blocked", "kill_switch"]


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: str
    currency: str
    equity: Decimal
    balance: Decimal
    exposure_in_use: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    symbol: str
    price: Decimal | None
    status: Literal["live", "stale", "unknown"]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    version: str
    currency: str
    max_risk_per_trade: Decimal
    max_total_exposure: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    min_stop_distance: Decimal
    size_step: Decimal
    timezone: str = "UTC"
    max_input_age_seconds: int = 10
    approval_ttl_seconds: int = 10

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone)
        if self.max_input_age_seconds < 1 or self.approval_ttl_seconds < 1:
            raise ValueError("invalid_freshness_policy")
        if not self.version:
            raise ValueError("missing_policy_version")
        if not self.currency:
            raise ValueError("missing_currency")
        for field, value in (
            ("max_risk_per_trade", self.max_risk_per_trade),
            ("max_total_exposure", self.max_total_exposure),
            ("max_daily_loss", self.max_daily_loss),
            ("max_drawdown", self.max_drawdown),
            ("min_stop_distance", self.min_stop_distance),
            ("size_step", self.size_step),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"invalid_{field}")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: str
    proposal_id: str
    signal_id: str
    action: DecisionAction
    reason: str
    reason_codes: tuple[str, ...]
    approved_size: Decimal
    reserved_risk: Decimal
    effective_time: datetime
    policy_version: str
    source_refs: tuple[str, ...]
    account_id: str = ""
    symbol: str = ""
    side: str = ""
    signal_hash: str = ""
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RiskProposal:
    proposal_id: str
    signal: ResearchSignal
    stop_distance: Decimal
    requested_size: Decimal
    quality_status: str
    account: AccountSnapshot
    price: PriceSnapshot


@dataclass(frozen=True, slots=True)
class RiskState:
    status: RiskStatus
    policy_version: str
    trading_day: str
    daily_pnl: Decimal
    equity_peak: Decimal
    reserved_exposure: Decimal
    kill_switch: bool
    seen_proposals: tuple[str, ...]


def signal_fingerprint(signal: ResearchSignal) -> str:
    return hashlib.sha256(
        json.dumps(asdict(signal), sort_keys=True, default=str).encode()
    ).hexdigest()
