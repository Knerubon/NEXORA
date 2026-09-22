"""Import verified recorded data and run explicitly configured local research."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from nexora.artifacts import canonical_hash, decode
from nexora.backtest import BacktestConfig, BacktestRunner, BacktestRunStore
from nexora.backtest.datasets import load_dataset
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora_api.environment import Environment
from nexora_api.research import configured_journal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--journal", type=Path, default=None)
    parser.add_argument("--backtest-config", type=Path)
    args = parser.parse_args()
    settings = Environment.resolve()
    for source in (args.runtime_config, args.backtest_config):
        if source and not (
            source.resolve().is_relative_to(settings.code)
            or source.resolve().is_relative_to(settings.root)
        ):
            raise ValueError("configuration_outside_environment")
    config = decode(RuntimeConfig, json.loads(args.runtime_config.read_text(encoding="utf-8")))
    dataset, events = load_dataset(args.dataset)
    if args.journal:
        os.environ["NEXORA_JOURNAL_PATH"] = str(args.journal.resolve())
    journal = configured_journal()
    try:
        runtime = ResearchRuntime(config, journal)
        for event in events:
            runtime.ingest(event, completeness=dataset.quality_status)
        print(f"PASS: {len(runtime.events())} verified recorded events; backend={journal.backend}")
        if args.backtest_config:
            cfg = decode(
                BacktestConfig, json.loads(args.backtest_config.read_text(encoding="utf-8"))
            )
            run = BacktestRunner().run(
                dataset=dataset,
                config=cfg,
                events=events,
                expected_dataset_hash=canonical_hash(dataset),
            )
            BacktestRunStore(journal).append(run)
            print(f"PASS: {run.run_id}; status={run.status}; trades={run.metrics.trade_count}")
    finally:
        journal.close()


if __name__ == "__main__":
    main()
