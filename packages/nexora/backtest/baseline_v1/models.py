"""Contracts for the P&F Baseline V1 historical replay harness.

This is a separate research contract from the ADR-014 timed comparison runner
(`nexora.backtest.runner`). Every policy value is supplied explicitly by the
caller; nothing here is an approved trading default. Policy enums name the
decision-record option they implement (see
docs/research/edge-validation/baseline-v1-replay/DECISIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.risk.models import RiskPolicy

Side = Literal["long", "short"]
ReadinessMode = Literal["ready_at_emission"]  # D2 option A (only mode implemented)
EntryMode = Literal["market_next_quote"]  # D2 option A
TargetPlan = Literal["tp1_full_exit"]  # D3 Owner decision D3-TP1-OWNER-20261030
StopPlan = Literal["static_signal_invalidation"]  # D3 option A, no movement
OppositeSignalPolicy = Literal["ignore"]  # D3 option A
EndOfDataPolicy = Literal["mark_open_censored"]  # D3 end-window option A
LedgerScope = Literal["single_position"]  # D4 option A
QuoteRequirement = Literal["tick_bid_ask"]  # D3 both-touch option A / D5 option A

ExitReason = Literal["stop", "tp1", "max_hold"]
Verdict = Literal["SUPPORTS_EDGE", "INSUFFICIENT_EVIDENCE", "NO_EDGE", "BLOCKED"]
OpportunityOutcome = Literal[
    "entered",
    "readiness_not_ready",
    "readiness_mismatch",
    "position_busy",
    "invalid_geometry",
    "risk_rejected",
    "risk_approval_expired",
    "entry_through_stop",
    "entry_through_target",
    "stale_entry_quote",
    "no_entry_quote",
]


class ReplayInputError(ValueError):
    """Fail-closed input/preflight error; the run produces no result."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InstrumentCapabilities:
    """Adapter-supplied instrument economics; core logic never hardcodes a broker/symbol."""

    instrument_id: str
    symbol: str
    account_currency: str
    contract_multiplier: Decimal  # account money per 1.0 price unit per 1.0 quantity
    quantity_step: Decimal
    price_precision: int
    source: str
    version: str

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.symbol or not self.account_currency:
            raise ReplayInputError("invalid_capabilities")
        for value in (self.contract_multiplier, self.quantity_step):
            if not value.is_finite() or value <= 0:
                raise ReplayInputError("invalid_capabilities")
        if not 0 <= self.price_precision <= 10 or not self.version or not self.source:
            raise ReplayInputError("invalid_capabilities")


@dataclass(frozen=True, slots=True)
class CostModel:
    """Spread is embedded in bid/ask fills and is never charged again.

    ``None`` means UNKNOWN and is never treated as zero: a run with an unknown
    component reports ``net_complete=False`` and cannot support a net-edge claim.
    """

    version: str
    currency: str
    commission_per_unit_per_side: Decimal | None
    slippage_price_units_per_market_fill: Decimal
    swap_long_per_unit_per_rollover: Decimal | None
    swap_short_per_unit_per_rollover: Decimal | None
    rollover_hour_utc: int = 21

    def __post_init__(self) -> None:
        if not self.version or not self.currency or not 0 <= self.rollover_hour_utc <= 23:
            raise ReplayInputError("invalid_cost_model")
        for value in (
            self.commission_per_unit_per_side,
            self.slippage_price_units_per_market_fill,
            self.swap_long_per_unit_per_rollover,
            self.swap_short_per_unit_per_rollover,
        ):
            if value is not None and not value.is_finite():
                raise ReplayInputError("invalid_cost_model")
        if self.slippage_price_units_per_market_fill < 0 or (
            self.commission_per_unit_per_side is not None and self.commission_per_unit_per_side < 0
        ):
            raise ReplayInputError("invalid_cost_model")


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    version: str
    entry_latency_seconds: Decimal
    max_entry_quote_age_seconds: Decimal
    max_hold_seconds: int | None  # None = no time stop (D3 open value)
    requested_size: Decimal
    readiness_mode: ReadinessMode = "ready_at_emission"
    entry_mode: EntryMode = "market_next_quote"
    target_plan: TargetPlan = "tp1_full_exit"
    stop_plan: StopPlan = "static_signal_invalidation"
    opposite_signal: OppositeSignalPolicy = "ignore"
    end_of_data: EndOfDataPolicy = "mark_open_censored"
    ledger_scope: LedgerScope = "single_position"
    quote_requirement: QuoteRequirement = "tick_bid_ask"

    def __post_init__(self) -> None:
        if not self.version:
            raise ReplayInputError("invalid_replay_policy")
        for value in (self.entry_latency_seconds, self.max_entry_quote_age_seconds):
            if not value.is_finite() or value < 0:
                raise ReplayInputError("invalid_replay_policy")
        if not self.requested_size.is_finite() or self.requested_size <= 0:
            raise ReplayInputError("invalid_replay_policy")
        if self.max_hold_seconds is not None and self.max_hold_seconds < 1:
            raise ReplayInputError("invalid_replay_policy")


@dataclass(frozen=True, slots=True)
class AccountConfig:
    account_id: str
    currency: str
    initial_equity: Decimal
    risk_policy: RiskPolicy

    def __post_init__(self) -> None:
        if not self.account_id or self.currency != self.risk_policy.currency:
            raise ReplayInputError("invalid_account_config")
        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ReplayInputError("invalid_account_config")


@dataclass(frozen=True, slots=True)
class UtcRange:
    """Half-open ``[start, end)`` UTC interval."""

    start: datetime
    end: datetime
    label: str

    def __post_init__(self) -> None:
        for value in (self.start, self.end):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ReplayInputError("timezone_required")
        if self.end <= self.start or not self.label:
            raise ReplayInputError("invalid_range")

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def overlaps(self, other: UtcRange) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class DataAuthorization:
    """Development-only data scope. An absent or incomplete registry blocks the run.

    ``protected_registry_complete`` must be attested by the data custodian; an
    empty guessed protected list is not a valid registry (E-DEVELOPMENT-DATA).
    """

    data_use: Literal["development", "synthetic"]
    instrument_id: str
    source: str
    development: UtcRange
    warmup: UtcRange | None
    protected: tuple[UtcRange, ...]
    protected_registry_complete: bool
    registry_authority: str
    registry_version: str


@dataclass(frozen=True, slots=True)
class EvidenceCriteria:
    """Pre-registered decision thresholds; must be fixed before a development run."""

    version: str
    registered_at: datetime
    min_closed_trades: int
    confidence_level: Decimal
    min_profit_factor: Decimal
    bootstrap_resamples: int
    bootstrap_seed: int

    def __post_init__(self) -> None:
        if self.registered_at.tzinfo is None or self.registered_at.utcoffset() is None:
            raise ReplayInputError("timezone_required")
        if (
            not self.version
            or self.min_closed_trades < 1
            or not Decimal("0") < self.confidence_level < Decimal("1")
            or self.min_profit_factor <= 0
            or self.bootstrap_resamples < 100
        ):
            raise ReplayInputError("invalid_evidence_criteria")


@dataclass(frozen=True, slots=True)
class OpportunityRecord:
    signal_id: str
    side: Side
    decision_event: str
    decision_index: int
    decision_time: datetime
    readiness_state: str
    regime: str
    outcome: OpportunityOutcome
    risk_reason: str | None
    stop_price: Decimal | None
    target_price: Decimal | None


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    commission: Decimal
    slippage: Decimal
    spread: Decimal  # informational: already inside the bid/ask fills, never re-charged
    swap: Decimal
    swap_unknown_rollovers: int
    commission_unknown: bool


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: str
    signal_id: str
    side: Side
    regime: str
    quantity: Decimal
    decision_index: int
    entry_index: int
    entry_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    exit_index: int | None
    exit_time: datetime | None
    exit_price: Decimal | None
    exit_reason: ExitReason | None
    status: Literal["closed", "open_censored"]
    gross_pnl: Decimal  # price-path PnL at bid/ask fills (spread included), before fees
    costs: CostBreakdown
    net_pnl: Decimal
    risk_proposal_id: str
    reserved_risk: Decimal


@dataclass(frozen=True, slots=True)
class RegimeMetrics:
    regime: str
    trade_count: int
    net_pnl: Decimal
    expectancy: Decimal
    win_rate: Decimal


@dataclass(frozen=True, slots=True)
class ReplayMetrics:
    closed_trades: int
    open_censored: int
    wins: int
    losses: int
    win_rate: Decimal
    gross_pnl: Decimal
    total_costs: Decimal
    net_pnl: Decimal
    expectancy: Decimal
    profit_factor: Decimal | None
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    final_equity: Decimal
    expectancy_ci_low: Decimal | None
    expectancy_ci_high: Decimal | None
    net_complete: bool
    by_regime: tuple[RegimeMetrics, ...]
    opportunity_funnel: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RunManifest:
    harness_version: str
    decision_source: str
    code_sha: str
    code_dirty: bool
    python: str
    decimal_precision: int
    instrument: InstrumentCapabilities
    data_use: str
    dataset_hash: str
    events_hash: str
    event_count: int
    first_event_time: datetime
    last_event_time: datetime
    development_range: UtcRange
    protected_registry_hash: str
    config_hash: str
    pipeline_config_hash: str
    policy_hash: str
    cost_hash: str
    account_hash: str
    criteria_hash: str | None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    manifest: RunManifest
    opportunities: tuple[OpportunityRecord, ...]
    trades: tuple[TradeRecord, ...]
    equity_curve_hash: str
    metrics: ReplayMetrics
    verdict: Verdict
    verdict_reasons: tuple[str, ...]
    result_hash: str
