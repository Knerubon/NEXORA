from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexora.backtest import (
    BacktestInputError,
    BacktestRunner,
    BacktestRunStore,
    canonical_hash,
    fixture_config,
    fixture_dataset_manifest,
    fixture_signals,
)


def test_backtest_rerun_with_same_inputs_is_deterministic() -> None:
    runner = BacktestRunner()
    dataset = fixture_dataset_manifest()
    config = fixture_config("fixed_pnf")
    signals = fixture_signals()
    expected_hash = canonical_hash(dataset)
    started = datetime(2026, 3, 4, 10, 0, tzinfo=UTC)

    first = runner.run(
        dataset=dataset,
        config=config,
        expected_dataset_hash=expected_hash,
        signals=signals,
        started_at=started,
    )
    second = runner.run(
        dataset=dataset,
        config=config,
        expected_dataset_hash=expected_hash,
        signals=signals,
        started_at=started,
    )

    assert first == second


def test_backtest_rejects_hash_mismatch_and_unknown_quality() -> None:
    runner = BacktestRunner()
    dataset = fixture_dataset_manifest()
    config = fixture_config("baseline")
    signals = fixture_signals()

    with pytest.raises(BacktestInputError) as mismatch:
        runner.run(
            dataset=dataset,
            config=config,
            expected_dataset_hash="bad-hash",
            signals=signals,
        )
    assert mismatch.value.code == "dataset_hash_mismatch"

    unknown_dataset = replace(dataset, quality_status="unknown")
    with pytest.raises(BacktestInputError) as quality:
        runner.run(
            dataset=unknown_dataset,
            config=config,
            expected_dataset_hash=canonical_hash(unknown_dataset),
            signals=signals,
        )
    assert quality.value.code == "dataset_quality_unknown"


def test_backtest_metrics_handle_zero_loss_without_division_error() -> None:
    runner = BacktestRunner()
    dataset = fixture_dataset_manifest()
    adaptive_config = fixture_config("adaptive_pnf")
    config = replace(
        adaptive_config,
        cost_policy=replace(
            adaptive_config.cost_policy,
            spread=Decimal("0"),
            commission=Decimal("0"),
            slippage=Decimal("0"),
        ),
    )
    run = runner.run(
        dataset=dataset,
        config=config,
        expected_dataset_hash=canonical_hash(dataset),
        signals=(fixture_signals()[0],),
    )
    assert run.metrics.trade_count == 1
    assert run.metrics.profit_factor is None


def test_backtest_store_compare_is_stable() -> None:
    runner = BacktestRunner()
    store = BacktestRunStore()
    dataset = fixture_dataset_manifest()
    expected_hash = canonical_hash(dataset)
    baseline = runner.run(
        dataset=dataset,
        config=fixture_config("baseline"),
        expected_dataset_hash=expected_hash,
        signals=fixture_signals(),
    )
    fixed = runner.run(
        dataset=dataset,
        config=fixture_config("fixed_pnf"),
        expected_dataset_hash=expected_hash,
        signals=fixture_signals(),
    )
    store.append(baseline)
    store.append(fixed)

    compared = store.compare((baseline.run_id,))
    assert len(compared) == 1
    assert compared[0]["run_id"] == baseline.run_id
