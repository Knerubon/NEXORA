"""Ingest operator-supplied hourly .bi5 files into an immutable, verified dataset (offline).

No network access. Requires the data owner's declared time semantics, price divisor and a
retrieval log (URL + tz-aware retrieval time per file). Raw files are never committed to Git.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from nexora.market_history.dukascopy import DeclaredSemantics, ingest, verify
from nexora.market_history.quality import QualityConfig


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start-day", type=date.fromisoformat, required=True)
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--semantics", type=Path, required=True, help="DeclaredSemantics JSON")
    ap.add_argument("--retrieval-log", type=Path, required=True)
    ap.add_argument("--selection-ref", required=True, help="where the window was documented")
    ap.add_argument("--max-gap-ms", type=int, required=True)
    a = ap.parse_args()
    manifest = ingest(
        a.source_dir, a.out, symbol=a.symbol, start_day=a.start_day, days=a.days,
        semantics=DeclaredSemantics(**json.loads(a.semantics.read_text())),
        retrieval_log=a.retrieval_log, selection_ref=a.selection_ref,
        quality=QualityConfig(a.max_gap_ms), ingested_at=datetime.now(UTC),
    )  # fmt: skip
    problems = verify(a.out)
    print(json.dumps({"dataset_id": manifest["dataset_id"], "verify_problems": problems}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
