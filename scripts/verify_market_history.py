"""Verify a market-history dataset and report TRUSTED / UNTRUSTED (offline; reads files only).

TRUSTED requires an anchor record stored independently of the dataset folder (see
`extract_market_history.py --anchor-out`). Without one, a dataset that verifies cleanly is still
UNTRUSTED: usable for development, never as evidence (G1-G5, baseline freeze).
Exit code: 0 = TRUSTED, or (without --require-trusted) cleanly verified; 1 = not; 2 = bad anchor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nexora.market_history.extract import dataset_status, load_anchor
from nexora.market_history.models import HistoryError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--anchor", type=Path, help="anchor JSON stored OUTSIDE the dataset folder")
    ap.add_argument("--require-trusted", action="store_true")
    args = ap.parse_args()
    anchor = None
    if args.anchor is not None:
        try:
            anchor = load_anchor(args.anchor, dataset_dir=args.dataset)
        except HistoryError as exc:
            print(json.dumps({"status": "UNTRUSTED", "status_reasons": [f"{exc.code}"]}))
            return 2
    status = dataset_status(args.dataset, anchor)
    print(json.dumps({"status": status.status, "status_reasons": list(status.reasons)}))
    if status.trusted:
        return 0
    only_unanchored = set(status.reasons) <= {"no_independent_anchor"}
    return 0 if only_unanchored and not args.require_trusted else 1


if __name__ == "__main__":
    raise SystemExit(main())
