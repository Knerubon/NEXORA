"""EXPLORATORY run of the P&F Baseline V1 replay harness on public XAUUSD tick data.

NOT governance evidence. NOT a D6 development run. Every value below is an assumption
written down BEFORE the run (2026-11-20) and was not tuned on results; nothing here is an
approved Baseline V1 setting. The protected range is a self-declared exclusion (all data
after the window, never loaded), NOT a custodian-attested holdout registry.

Usage (repo root, data from https://github.com/Touhid-HZ/xauusd-tick-data-2024):
    uv run --no-sync python scripts/baseline_v1_exploratory_xauusd.py \
        --data-dir <dir with XAUUSD_2024_wNN.csv.gz> --out <file.json outside checkout>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from nexora.adaptive_box import AdaptiveBoxConfig
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.backtest.baseline_v1 import BaselineV1Replay
from nexora.backtest.baseline_v1.dukascopy_csv import SOURCE, load_ticks
from nexora.backtest.baseline_v1.models import (
    AccountConfig,
    CostModel,
    DataAuthorization,
    InstrumentCapabilities,
    ReplayPolicy,
    UtcRange,
)
from nexora.backtest.datasets import manifest_for
from nexora.market_regime import RegimeConfig
from nexora.pnf import PnfConfig
from nexora.research import PipelineConfig, ResolutionConfig
from nexora.risk.models import RiskPolicy
from nexora.signals import SignalConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
SYMBOL = "XAUUSD"
D = Decimal

# ---- window (EXPLORATORY; earliest data so later 2024-2026 stays unseen for a future holdout)
WARMUP = UtcRange(datetime(2024, 1, 2, tzinfo=UTC), datetime(2024, 1, 8, tzinfo=UTC), "warmup")
DEVELOPMENT = UtcRange(
    datetime(2024, 1, 8, tzinfo=UTC), datetime(2024, 2, 5, tzinfo=UTC), "exploratory"
)
EXCLUDED_AFTER = UtcRange(
    datetime(2024, 2, 5, tzinfo=UTC),
    datetime(2100, 1, 1, tzinfo=UTC),
    "self-declared-exclusion-not-a-registry",
)
FILES = ("w01", "w02", "w03", "w04", "w05")


def pipeline_config() -> PipelineConfig:
    """The repo's XAUUSD test fixture (tests/validation_fixtures.py); only precision 1 -> 3."""
    return PipelineConfig(
        version="exploratory-xauusd-fixture-copy-v1",
        resolutions=tuple(
            ResolutionConfig(
                name,
                PnfConfig(SYMBOL, D(size), 2, 3, "bid", f"{name}-expl-v1"),
                AdaptiveBoxConfig(
                    "fixed",
                    D(size),
                    3,  # precision must equal the feed's 3 decimals (fixture used 1)
                    f"{name}-expl-v1",
                    atr_period=2,
                    min_box_size=D("0.1"),
                    max_box_size=D("5"),
                ),
            )
            for name, size in (("fast", "0.5"), ("medium", "1"), ("slow", "2"))
        ),
        structure_resolution="fast",
        regime=RegimeConfig(SYMBOL, 4, D("2"), D("20"), D("0.5"), "expl-regime-v1"),
        signals=SignalConfig(SYMBOL, 1, 20, "expl-signal-v1"),
        stale_after_events=100,
    )


def capabilities() -> InstrumentCapabilities:
    return InstrumentCapabilities(
        instrument_id="exploratory:XAUUSD",
        symbol=SYMBOL,
        account_currency="USD",
        contract_multiplier=D("100"),  # assumption: 1 lot = 100 oz
        quantity_step=D("0.01"),
        price_precision=3,
        source="assumed-standard-xauusd-cfd-profile",
        version="expl-caps-v1",
    )


def setup() -> tuple[ReplayPolicy, CostModel, AccountConfig]:
    policy = ReplayPolicy(
        version="expl-replay-v1",
        entry_latency_seconds=D("0.5"),
        max_entry_quote_age_seconds=D("5"),
        max_hold_seconds=28800,  # 8 h time stop (D3-A pairs ignore-opposite with a finite hold)
        requested_size=D("1"),
    )
    costs = CostModel(
        version="expl-cost-v1",
        currency="USD",
        commission_per_unit_per_side=D("3.5"),  # USD per lot per side (~7 round trip)
        slippage_price_units_per_market_fill=D("0.05"),
        swap_long_per_unit_per_rollover=D("30"),  # positive = charge, USD per lot per night
        swap_short_per_unit_per_rollover=D("-10"),  # credit
        rollover_hour_utc=22,  # 17:00 New York in January
    )
    account = AccountConfig(
        account_id="exploratory-account",
        currency="USD",
        initial_equity=D("10000"),
        risk_policy=RiskPolicy(
            version="expl-risk-v1",
            currency="USD",
            max_risk_per_trade=D("100"),  # 1% of equity
            max_total_exposure=D("200"),
            max_daily_loss=D("300"),
            max_drawdown=D("1000"),
            min_stop_distance=D("50"),  # 0.50 USD/oz x 100 oz
            size_step=D("0.01"),
            max_input_age_seconds=10,
            approval_ttl_seconds=60,
        ),
    )
    return policy, costs, account


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dev-end", help="ISO UTC date; shortens the window (benchmarking)")
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(REPO_ROOT):
        parser.error("--out must be outside the repository checkout")
    development = DEVELOPMENT
    if args.dev_end:
        end = datetime.fromisoformat(args.dev_end).replace(tzinfo=UTC)
        development = UtcRange(DEVELOPMENT.start, end, "exploratory-short")
    paths = [args.data_dir / f"XAUUSD_2024_{w}.csv.gz" for w in FILES]
    t0 = time.monotonic()
    events = load_ticks(paths, symbol=SYMBOL, start=WARMUP.start, end=development.end)
    # received_at = event_time + 150 ms may cross the end; drop such edge ticks
    events = tuple(
        e for e in events if development.contains(e.received_at) or WARMUP.contains(e.received_at)
    )
    t1 = time.monotonic()
    dataset = manifest_for(events, quality="complete")
    policy, costs, account = setup()
    auth = DataAuthorization(
        data_use="development",
        instrument_id="exploratory:XAUUSD",
        source=SOURCE,
        development=development,
        warmup=WARMUP,
        protected=(EXCLUDED_AFTER,),
        protected_registry_complete=True,
        registry_authority="EXPLORATORY-SELF-DECLARED (no custodian attestation; not D6)",
        registry_version="exploratory-v1",
    )
    result = BaselineV1Replay(
        pipeline_config=pipeline_config(),
        policy=policy,
        costs=costs,
        account=account,
        capabilities=capabilities(),
    ).run(
        events=events,
        dataset=dataset,
        expected_dataset_hash=canonical_hash(dataset),
        authorization=auth,
        criteria=None,
        code_sha=_git("rev-parse", "HEAD"),
        code_dirty=bool(_git("status", "--porcelain")),
    )
    t2 = time.monotonic()
    bundle = {
        "label": "EXPLORATORY - not governance evidence, not D6",
        "event_count": len(events),
        "first_event": events[0].event_time.isoformat(),
        "last_event": events[-1].event_time.isoformat(),
        "seconds_load": round(t1 - t0, 1),
        "seconds_replay": round(t2 - t1, 1),
        "manifest": canonical_serialize(result.manifest),
        "metrics": canonical_serialize(result.metrics),
        "verdict": result.verdict,
        "verdict_reasons": list(result.verdict_reasons),
        "equity_curve_hash": result.equity_curve_hash,
        "result_hash": result.result_hash,
        "trades": canonical_serialize(result.trades),
        "opportunities": canonical_serialize(result.opportunities),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: bundle[k]
                for k in ("event_count", "seconds_load", "seconds_replay", "verdict", "result_hash")
            }
        )
    )
    print(json.dumps(bundle["metrics"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
