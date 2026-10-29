"""Executed check of the PR #96 tick storage format against the MD-6 QI-R4 token grammar (Q2).

NOT a CI test: PR #96 code is not part of the base of PR #94, so this script loads a read-only
`git archive` export of PR #96 head 1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8 given on the command
line. It reads no market data, opens no network or broker, and every value is SYNTHETIC.

Usage (from any directory):
    python -I verify_pr96_mapping.py <export root containing packages/nexora/market_history> \
        <repo root containing tests/md6_quarantine_reference.py>

Prints one line per check: `PASS|FAIL <id> <detail>`; exit status 1 if any FAIL. The results the
documentation cites are those of the run recorded in QUARANTINE-IDENTITY.md section 11.
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path


def main(export_root: str, repo_root: str) -> int:
    sys.path.insert(0, str(Path(export_root) / "packages"))
    sys.path.insert(0, repo_root)
    from nexora.market_history.extract import _encode_tick
    from nexora.market_history.models import RawTick, decimal_str

    from tests import md6_quarantine_reference as c

    failures = 0

    def report(ok: bool, check: str, detail: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {check} {detail}")

    def accepted(token: str) -> bool:
        try:
            c._price_token(token, "price")
        except c.CanonicalizationError:
            return False
        return True

    # V1: every finite value normalizes to a token inside the QI-R4 grammar.
    finite = [
        "1.1",
        "1.10",
        "1.100000",
        "0.0",
        "-0.0",
        "-0",
        "1E+3",
        "1E-7",
        "123.4500",
        "0.000",
        "5",
    ]
    outputs = {text: decimal_str(Decimal(text)) for text in finite}
    report(
        all(accepted(t) for t in outputs.values()),
        "V1 finite-output-in-grammar",
        "; ".join(f"{k}->{v}" for k, v in outputs.items()),
    )
    # V2: #96 normalizes trailing zeros BEFORE hashing; QI-R4 does not. So 1.10 and 1.1 become
    # one stored token in #96 data, while the same two spellings fed directly to QI-R4 differ.
    report(
        decimal_str(Decimal("1.10")) == decimal_str(Decimal("1.1")) == "1.1",
        "V2 pr96-normalizes-trailing-zeros",
        "decimal_str(1.10)==decimal_str(1.1)=='1.1'",
    )
    a = c.TickRecord(1, "1.10", "1.20", None)
    b = c.TickRecord(1, "1.1", "1.2", None)
    report(
        c.record_hash(a) != c.record_hash(b),
        "V3 qi-r4-keeps-1.10-and-1.1-distinct",
        "record_hash differs",
    )
    # V4: non-finite spellings. #96 stores str(Decimal) verbatim; QI-R4 has a closed set.
    nonfinite = ["NaN", "sNaN", "-NaN", "-sNaN", "Infinity", "-Infinity", "NaN123"]
    table = {t: (str(Decimal(t)), accepted(str(Decimal(t)))) for t in nonfinite}
    report(
        table["NaN"][1] and table["Infinity"][1] and table["-Infinity"][1],
        "V4a closed-set-spellings-accepted",
        "NaN, Infinity, -Infinity",
    )
    report(
        not any(table[t][1] for t in ("sNaN", "-NaN", "-sNaN", "NaN123")),
        "V4b other-nonfinite-spellings-outside-closed-set",
        "; ".join(f"{k}->{v[0]} accepted={v[1]}" for k, v in table.items()),
    )
    # V5: row layout and flags mapping.
    line = _encode_tick(RawTick(1_700_000_000_123, Decimal("1.10"), Decimal("1.2"), 6))
    row = json.loads(line)
    report(
        line == '{"a":"1.2","b":"1.1","f":6,"t":1700000000123}',
        "V5a row-layout",
        line,
    )
    no_flags = json.loads(_encode_tick(RawTick(5, Decimal("1"), Decimal("2"), None)))
    report("f" not in no_flags, "V5b absent-flags-omit-key", json.dumps(no_flags, sort_keys=True))
    mapped = c.TickRecord(row["t"], row["b"], row["a"], str(row["f"]))
    report(c.record_hash(mapped) is not None, "V5c row-maps-to-tickrecord", repr(mapped))
    negative = False
    try:
        c.canonical_record_bytes(c.TickRecord(1, "1", "2", str(-1)))
    except c.CanonicalizationError:
        negative = True
    report(negative, "V5d negative-flags-outside-grammar", "flags '-1' refused by QI-R4")
    # V6: content_sha256 is a hash over concatenated row bytes in stored order.
    ticks = [
        RawTick(10 + i, Decimal("1.1") + Decimal(i) / 100, Decimal("1.2"), None) for i in range(6)
    ]

    def content(chunks: list[list[RawTick]]) -> str:
        h = hashlib.sha256()
        for chunk in chunks:
            h.update("".join(_encode_tick(t) + "\n" for t in chunk).encode())
        return h.hexdigest()

    report(
        content([ticks])
        == content([ticks[:2], ticks[2:]])
        == content([ticks[:1], ticks[1:5], ticks[5:]]),
        "V6a content_sha256-independent-of-chunk-layout",
        "three chunkings equal",
    )
    report(
        content([ticks]) != content([list(reversed(ticks))]),
        "V6b content_sha256-sensitive-to-order",
        "reordered ticks give another hash",
    )
    recs = [c.TickRecord(t.time_msc, decimal_str(t.bid), decimal_str(t.ask), None) for t in ticks]
    same_multiset = (
        c.content_identity(recs).multiset_fingerprint
        == c.content_identity(list(reversed(recs))).multiset_fingerprint
    )
    report(same_multiset, "V6c qi-multiset-equal-for-reordered", "multiset_fingerprint equal")
    report(
        content([ticks]) != c.content_identity(recs).ordered_content_hash,
        "V6d hashes-are-different-functions",
        "content_sha256 != ordered_content_hash (no derivable relation)",
    )
    # V7: no linkage between the two registers in PR #96 sources.
    root = Path(export_root) / "packages" / "nexora" / "market_history"
    needles = ("quarantine", "multiset_fingerprint", "ordered_content_hash", "shadow_record")
    hits = {
        n: sorted(p.name for p in root.glob("*.py") if n in p.read_text(encoding="utf-8").lower())
        for n in needles
    }
    report(
        not any(hits.values()),
        "V7 pr96-has-no-md6-quarantine-reference",
        json.dumps(hits, sort_keys=True),
    )
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
