"""Rerun the P&F Baseline V1 replay harness on its deterministic SYNTHETIC proof input.

Usage (repo root):
    uv run --no-sync python scripts/baseline_v1_replay.py --synthetic --out <file.json>

Writes the run manifest, metrics, verdict and result hash. Real historical development
data is intentionally not accepted yet: the development source/window and the protected
holdout registry are not approved (decision record D6), so that mode stays BLOCKED.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.backtest.baseline_v1 import BaselineV1Replay
from nexora.backtest.baseline_v1.synthetic import (
    synthetic_authorization,
    synthetic_capabilities,
    synthetic_events,
    synthetic_pipeline_config,
    synthetic_setup,
)
from nexora.backtest.datasets import manifest_for

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", action="store_true", required=True)
    parser.add_argument("--events", type=int, default=400)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.out is not None and args.out.resolve().is_relative_to(REPO_ROOT):
        # an output file inside the checkout would make the next run's code_dirty True
        parser.error("--out must be outside the repository checkout")
    events = synthetic_events(args.events)
    dataset = manifest_for(events, quality="complete")
    policy, costs, account = synthetic_setup()
    result = BaselineV1Replay(
        pipeline_config=synthetic_pipeline_config(),
        policy=policy,
        costs=costs,
        account=account,
        capabilities=synthetic_capabilities(),
    ).run(
        events=events,
        dataset=dataset,
        expected_dataset_hash=canonical_hash(dataset),
        authorization=synthetic_authorization(events),
        criteria=None,
        code_sha=_git("rev-parse", "HEAD"),
        code_dirty=bool(_git("status", "--porcelain")),
    )
    bundle = {
        "manifest": canonical_serialize(result.manifest),
        "metrics": canonical_serialize(result.metrics),
        "verdict": result.verdict,
        "verdict_reasons": list(result.verdict_reasons),
        "equity_curve_hash": result.equity_curve_hash,
        "result_hash": result.result_hash,
        "trades": canonical_serialize(result.trades),
        "opportunities": canonical_serialize(result.opportunities),
    }
    text = json.dumps(bundle, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    summary = {k: bundle[k] for k in ("verdict", "verdict_reasons", "result_hash")}
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
