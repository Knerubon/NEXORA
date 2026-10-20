"""Operator-started, READ-ONLY historical tick extraction (WINDOWS-INTEGRATION).

Attaches to an already-running, already-logged-in terminal; accepts no credentials; sends no
orders. Requires an explicit time-conversion JSON (segments with evidence_ref) - there is no
default offset. Output is written to --out (keep it outside Git; raw ticks are not committed).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from nexora.market_history.convert import TimeConversion, TimeSegment
from nexora.market_history.extract import ExtractionRequest, extract, verify_dataset
from nexora.market_history.provider import Mt5ReadOnlyHistorySource
from nexora.market_history.quality import QualityConfig


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--from-raw-msc", type=int, required=True)
    ap.add_argument("--to-raw-msc", type=int, required=True)
    ap.add_argument("--chunk-ms", type=int, required=True)
    ap.add_argument("--max-gap-ms", type=int, required=True)
    ap.add_argument("--time-conversion", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    raw = json.loads(args.time_conversion.read_text())
    conversion = TimeConversion(tuple(TimeSegment(**s) for s in raw["segments"]))
    source = Mt5ReadOnlyHistorySource()
    try:
        manifest = extract(
            source,
            ExtractionRequest(args.symbol, args.from_raw_msc, args.to_raw_msc, args.chunk_ms),
            conversion,
            QualityConfig(args.max_gap_ms),
            args.out,
            retrieved_at=datetime.now(UTC),
        )
    finally:
        source.close()
    problems = verify_dataset(args.out)
    print(json.dumps({"dataset_id": manifest["dataset_id"], "verify_problems": problems}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
