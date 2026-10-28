"""Finish a dataset publication interrupted by a crash (offline; never touches a terminal).

A crash between the no-overwrite link of a file and the unlink of its temp name leaves the file
with two names (link count 2): verification then reports `*_multiple_links` and `stale_temp_file`
and the dataset stays blocked. This tool takes the writer lock and removes ONLY temp names whose
inode is the inode of their final name (the second name of an already published file); no data is
deleted and nothing else is touched. It prints the removed names as an audit record. Anything it
cannot prove safe stays as it is: re-extract into a new folder.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nexora.market_history.extract import recover_interrupted_publish, verify_dataset
from nexora.market_history.models import HistoryError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    args = ap.parse_args()
    try:
        removed = recover_interrupted_publish(args.dataset)
    except HistoryError as exc:
        print(json.dumps({"refused": exc.code, "detail": str(exc)[:300]}))
        return 2
    problems = verify_dataset(args.dataset)
    print(json.dumps({"removed_temp_names": removed, "verify_problems": problems}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
