"""Deterministic SYNTHETIC inputs for harness mechanics proofs and the rerun script.

Nothing here is market data or an approved configuration. The pipeline values are
test vectors chosen only so the full P&F -> Signal -> Readiness chain emits decisions;
they must never be cited as Baseline V1 settings (D1 remains open).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.adaptive_box import AdaptiveBoxConfig
from nexora.backtest.baseline_v1.models import (
    AccountConfig,
    CostModel,
    DataAuthorization,
    InstrumentCapabilities,
    ReplayPolicy,
    UtcRange,
)
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_regime import RegimeConfig
from nexora.pnf import PnfConfig
from nexora.research import PipelineConfig, ResolutionConfig
from nexora.risk.models import RiskPolicy
from nexora.signals import SignalConfig

SYMBOL = "SYNTH"
SOURCE = "synthetic-zigzag"
START = datetime(2026, 1, 5, 9, tzinfo=UTC)
D = Decimal


def synthetic_events(count: int = 400) -> tuple[NormalizedPriceEvent, ...]:
    """Zig-zag bid path flipping every 40 ticks, fixed 0.3 spread, 20 s spacing."""
    pattern = (100, 104, 99, 106, 98, 108, 102, 110, 104, 112)
    events = []
    for i in range(1, count + 1):
        price = D(pattern[(i - 1) % 10] + 3 * ((i - 1) // 10)) + D(i % 7) / 10
        if (i // 40) % 2:
            price = D(300) - price
        at = START + timedelta(seconds=20 * i)
        events.append(
            NormalizedPriceEvent(
                1,
                f"syn:{i}",
                SOURCE,
                SYMBOL,
                "tick",
                at,
                at + timedelta(milliseconds=150),
                i,
                i,
                f"syn:{i}",
                "bid",
                "synthetic-units",
                1,
                price,
                bid=price,
                ask=price + D("0.3"),
            )
        )
    return tuple(events)


def synthetic_pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        version="synthetic-pipeline-v1",
        resolutions=tuple(
            ResolutionConfig(
                name,
                PnfConfig(SYMBOL, D(size), 2, 1, "bid", f"{name}-syn-v1"),
                AdaptiveBoxConfig(
                    "fixed", D(size), 1, f"{name}-syn-v1", atr_period=2, max_box_size=D("5")
                ),
            )
            for name, size in (("fast", "0.5"), ("medium", "1"), ("slow", "2"))
        ),
        structure_resolution="fast",
        regime=RegimeConfig(SYMBOL, 4, D("2"), D("20"), D("0.5"), "synthetic-regime-v1"),
        signals=SignalConfig(SYMBOL, 1, 20, "synthetic-signal-v1"),
        stale_after_events=100,
    )


def synthetic_capabilities() -> InstrumentCapabilities:
    return InstrumentCapabilities(
        instrument_id="synthetic:SYNTH",
        symbol=SYMBOL,
        account_currency="USD",
        contract_multiplier=D("1"),
        quantity_step=D("0.01"),
        price_precision=1,
        source="synthetic-capability-profile",
        version="synthetic-caps-v1",
    )


def synthetic_setup() -> tuple[ReplayPolicy, CostModel, AccountConfig]:
    policy = ReplayPolicy(
        version="synthetic-replay-v1",
        entry_latency_seconds=D("0"),
        max_entry_quote_age_seconds=D("60"),
        max_hold_seconds=None,
        requested_size=D("2"),
    )
    costs = CostModel(
        version="synthetic-cost-v1",
        currency="USD",
        commission_per_unit_per_side=D("0.05"),
        slippage_price_units_per_market_fill=D("0.05"),
        swap_long_per_unit_per_rollover=D("0"),
        swap_short_per_unit_per_rollover=D("0"),
    )
    account = AccountConfig(
        account_id="synthetic-account",
        currency="USD",
        initial_equity=D("10000"),
        risk_policy=RiskPolicy(
            version="synthetic-risk-v1",
            currency="USD",
            max_risk_per_trade=D("50"),
            max_total_exposure=D("500"),
            max_daily_loss=D("1000"),
            max_drawdown=D("2000"),
            min_stop_distance=D("0.1"),
            size_step=D("0.01"),
            max_input_age_seconds=10,
            approval_ttl_seconds=60,
        ),
    )
    return policy, costs, account


def synthetic_authorization(events: tuple[NormalizedPriceEvent, ...]) -> DataAuthorization:
    return DataAuthorization(
        data_use="synthetic",
        instrument_id="synthetic:SYNTH",
        source=SOURCE,
        development=UtcRange(
            events[0].event_time, events[-1].event_time + timedelta(seconds=1), "synthetic"
        ),
        warmup=None,
        protected=(),
        protected_registry_complete=True,
        registry_authority="synthetic-only (no market data)",
        registry_version="synthetic-v1",
    )
