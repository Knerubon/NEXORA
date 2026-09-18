"""Fixtures for risk policy and proposals."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nexora.risk.models import AccountSnapshot, PriceSnapshot, RiskPolicy, RiskProposal
from nexora.signals import ResearchSignal


def risk_policy_fixture() -> RiskPolicy:
    return RiskPolicy(
        version="p11-risk-v1",
        currency="USD",
        max_risk_per_trade=Decimal("150"),
        max_total_exposure=Decimal("400"),
        max_daily_loss=Decimal("300"),
        max_drawdown=Decimal("250"),
        min_stop_distance=Decimal("0.5"),
        size_step=Decimal("0.1"),
    )


def signal_fixture(signal_id: str = "xau:1:long", side: str = "long") -> ResearchSignal:
    decision_time = datetime(2026, 3, 5, 9, 0, tzinfo=UTC)
    return ResearchSignal(
        signal_id=signal_id,
        symbol="XAUUSD",
        side=side,  # type: ignore[arg-type]
        sequence=1,
        occurrence_time=decision_time,
        confirmation_time=decision_time,
        decision_time=decision_time,
        reasons=("fixture",),
        reason_codes=("fixture",),
        source_refs=("pivot-fixture", "regime-fixture"),
        config_version="p8-signal-v1",
        engine_versions=("p5-v1", "p7-v1"),
        status="active",
    )


def proposal_fixture(
    *,
    proposal_id: str = "proposal-1",
    requested_size: str = "2.0",
    stop_distance: str = "1.0",
    quality_status: str = "complete",
    price_status: str = "live",
    currency: str = "USD",
    equity: str = "10000",
    exposure_in_use: str = "0",
) -> RiskProposal:
    observed = datetime(2026, 3, 5, 9, 0, tzinfo=UTC)
    return RiskProposal(
        proposal_id=proposal_id,
        signal=signal_fixture(),
        stop_distance=Decimal(stop_distance),
        requested_size=Decimal(requested_size),
        quality_status=quality_status,
        account=AccountSnapshot(
            account_id="paper-account",
            currency=currency,
            equity=Decimal(equity),
            balance=Decimal(equity),
            exposure_in_use=Decimal(exposure_in_use),
            observed_at=observed,
        ),
        price=PriceSnapshot(
            symbol="XAUUSD",
            price=Decimal("100.0") if price_status == "live" else None,
            status=price_status,  # type: ignore[arg-type]
            observed_at=observed,
        ),
    )
