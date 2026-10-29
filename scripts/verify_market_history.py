"""Verify a market-history dataset and report TRUSTED / UNTRUSTED (offline; reads files only).

TRUSTED needs independent custody evidence supplied by the VERIFIER, outside the dataset folder:
`--store` (an append-only evidence store directory) and `--registry` (custodian allowlist JSON).
A dataset that verifies cleanly without them is still UNTRUSTED: usable for development, never as
evidence (G1-G5, baseline freeze). A V3-style `--anchor` (hashes only) is cross-checked but never
grants trust. TRUSTED also needs `--pin` (a head pin SIGNED by a registered pin authority, obtained
through the pin authority's own channel, outside the dataset) and `--max-pin-age-seconds` (how old
that pin may be: a governance parameter, no default). A head read from the store being verified is
not independent and never qualifies. `--store-head SEQ:SHA256` is an extra, unsigned truncation
check only.

EXIT-CODE CONTRACT (stable; a calling script may rely on it, and ONLY 0 means "trusted"):

    0  TRUSTED. Every required condition held: dataset integrity, no hard defects, proven
       capabilities, a valid un-revoked custodian approval, a fresh signed head pin.
    1  UNTRUSTED. The check ran to a verdict and the dataset is NOT trusted. This includes a
       dataset that verifies cleanly but has no custody evidence (development use only), and one
       that fails integrity or custody. Read `status_reasons` for why. Never approval.
    2  INVALID INPUT. The command line or an input file (anchor, registry, pin, store head,
       max pin age) is missing, unreadable, malformed or out of range. No verdict was reached.
    3  OPERATIONAL ERROR. An internal error prevented a verdict. Fail closed: treat as not trusted.

Any code other than 0 must be treated as "do not use as evidence". The JSON line on stdout repeats
the verdict: {"status", "trusted", "exit_code", "exit_meaning", "status_reasons"}. The historical
`--require-trusted` flag is accepted and is a NO-OP (the behaviour it used to request is now the
only behaviour); it can never turn a non-zero exit into 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from nexora.market_history import jsonio
from nexora.market_history.custody import ChainHead, LocalAppendOnlyStore, _inside, load_registry
from nexora.market_history.extract import _open_regular, dataset_status, load_anchor
from nexora.market_history.models import HistoryError

EXIT_TRUSTED = 0
EXIT_UNTRUSTED = 1
EXIT_INVALID_INPUT = 2
EXIT_OPERATIONAL = 3
EXIT_MEANING = {
    EXIT_TRUSTED: "trusted",
    EXIT_UNTRUSTED: "untrusted",
    EXIT_INVALID_INPUT: "invalid_input",
    EXIT_OPERATIONAL: "operational_error",
}
_INTERNAL_MARKERS = ("status_internal_error", "verify_internal_error")
_STORE_HEAD = re.compile(r"([0-9]{1,18}):([0-9a-f]{64})", re.ASCII)


def _head(text: str) -> ChainHead:
    m = _STORE_HEAD.fullmatch(text)
    if not m or int(m.group(1)) < 1:
        raise HistoryError("invalid_store_head", "expected SEQ:SHA256")
    return ChainHead(int(m.group(1)), m.group(2))


def _max_age(seconds: int) -> timedelta:
    if seconds <= 0 or seconds > jsonio.MAX_AGE_SECONDS_SANITY:
        raise HistoryError("invalid_max_pin_age", f"must be 1..{jsonio.MAX_AGE_SECONDS_SANITY}")
    return timedelta(seconds=seconds)


def _seconds(text: str) -> int:
    """ASCII digits only (int() would also take Arabic-Indic or fullwidth digits)."""
    if not re.fullmatch(r"[0-9]{1,18}", text, re.ASCII):
        raise argparse.ArgumentTypeError("expected a positive integer number of seconds")
    return int(text)


def _emit(status: str, reasons: list[str], code: int) -> int:
    print(
        json.dumps(
            {
                "status": status,
                "trusted": code == EXIT_TRUSTED,
                "exit_code": code,
                "exit_meaning": EXIT_MEANING[code],
                "status_reasons": reasons,
            }
        )
    )
    return code


def main() -> int:
    try:
        return _main()
    except Exception as exc:  # defensive: never an uncontrolled traceback, never exit 0
        return _emit("UNTRUSTED", [f"cli_internal_error:{type(exc).__name__}"], EXIT_OPERATIONAL)


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--anchor", type=Path, help="V3 hash-only anchor, OUTSIDE the dataset folder")
    ap.add_argument("--store", type=Path, help="append-only evidence store directory (outside)")
    ap.add_argument("--registry", type=Path, help="custodian registry JSON (outside the dataset)")
    ap.add_argument("--store-head", help="unsigned store head SEQ:SHA256 (truncation check only)")
    ap.add_argument("--pin", type=Path, help="signed head pin JSON from the pin authority")
    ap.add_argument("--max-pin-age-seconds", type=_seconds, help="maximum pin age (no default)")
    ap.add_argument(
        "--require-trusted", action="store_true", help="deprecated no-op (always in force)"
    )
    try:
        args = ap.parse_args()
    except SystemExit as exc:  # argparse exits 2 on bad usage: keep it, but as a JSON verdict too
        if exc.code in (0, None):
            raise
        return _emit("UNTRUSTED", ["invalid_arguments"], EXIT_INVALID_INPUT)
    if bool(args.store) != bool(args.registry):
        return _emit("UNTRUSTED", ["store_and_registry_go_together"], EXIT_INVALID_INPUT)
    anchor = None
    store = registry = head = pin = None
    max_age = None
    try:
        if args.anchor is not None:
            anchor = load_anchor(args.anchor, dataset_dir=args.dataset)
        if args.store is not None:
            store = LocalAppendOnlyStore(args.store)
            registry = load_registry(args.registry, dataset_dir=args.dataset)
        if args.store_head is not None:
            head = _head(args.store_head)
        if args.pin is not None:
            if _inside(args.pin, args.dataset):
                raise HistoryError("pin_inside_dataset")
            with _open_regular(args.pin) as fh:
                pin = jsonio.loads(fh.read(1 << 16), "pin")
        if args.max_pin_age_seconds is not None:
            max_age = _max_age(args.max_pin_age_seconds)
    except (HistoryError, ValueError, OSError) as exc:
        code = exc.code if isinstance(exc, HistoryError) else "input_unreadable"
        if code in ("json_invalid", "json_too_deep"):
            code = "pin_unreadable"
        return _emit("UNTRUSTED", [code], EXIT_INVALID_INPUT)
    status = dataset_status(
        args.dataset, anchor, store=store, registry=registry, expected_head=head, pin=pin,
        max_pin_age=max_age,
    )  # fmt: skip
    reasons = list(status.reasons)
    if any(r.startswith(_INTERNAL_MARKERS) or ":verify_internal_error" in r for r in reasons):
        return _emit(status.status, reasons, EXIT_OPERATIONAL)
    # exit 0 only for a TRUSTED verdict that also carries every input TRUSTED requires
    complete = all(x is not None for x in (store, registry, pin, max_age))
    if status.trusted and status.status == "TRUSTED" and not reasons and complete:
        return _emit(status.status, reasons, EXIT_TRUSTED)
    return _emit(status.status, reasons, EXIT_UNTRUSTED)


if __name__ == "__main__":
    sys.exit(main())
