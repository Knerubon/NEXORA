"""Specification-based duplicate and ordering oracle (finding B-M4).

Written from ORACLE-SPEC.md, not from the extractor. It deliberately uses a different method from
the extractor's `quality_report` (as read at the pinned PR #96 candidate, unchanged across the
stack): the extractor compares each tick with the previous one only; this oracle canonicalizes every
observation and counts over the WHOLE sequence by sorting and grouping, so repeats are found
wherever they occur. Golden vectors in `test_duplicate_oracle.py` are derived by hand from the
specification, not by running this code.

Canonical tick identity (ORACLE-SPEC.md section 2):
    (time_msc, canonical(bid), canonical(ask), flags-or-ABSENT)

Pure Python, no I/O, no `nexora` imports.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

ABSENT = "ABSENT"
Identity = tuple[int, str, str, str]


class Malformed(ValueError):
    pass


def canonical_decimal(text: Any) -> str:
    """Decimal TEXT -> canonical text. Equivalent spellings are equal ("1.10", "1.1", "+1.1",
    "1.1E0", "01.1" -> "1.1"; "-0" -> "0"). Binary floats, bools and other types are refused:
    their decimal value is ambiguous. NaN/Infinity are kept as named values, never dropped."""
    if type(text) is not str or not text.strip():
        raise Malformed("quote_not_decimal_text")
    try:
        with localcontext() as ctx:
            ctx.prec = 1000
            d = Decimal(text.strip())
    except InvalidOperation:
        raise Malformed("quote_not_decimal_text") from None
    if d.is_nan():
        return "NaN"
    if d.is_infinite():
        return "-Infinity" if d < 0 else "Infinity"
    plain = format(d, "f")  # fixed point, never an exponent
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    if plain in ("-0", ""):
        plain = "0"
    return plain


def identity(tick: Mapping[str, Any]) -> Identity:
    t = tick.get("time_msc")
    if type(t) is not int:
        raise Malformed("time_msc_not_int")
    if "bid" not in tick or "ask" not in tick:
        raise Malformed("quote_missing")
    flags = tick.get("flags", ABSENT)
    if flags != ABSENT and type(flags) is not int:
        raise Malformed("flags_not_int")
    return (t, canonical_decimal(tick["bid"]), canonical_decimal(tick["ask"]), str(flags))


def classify(ticks: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Counts per ORACLE-SPEC.md section 3. Malformed observations are counted and excluded."""
    ids: list[Identity] = []
    malformed = 0
    out_of_order = 0
    running_max: int | None = None
    for tick in ticks:
        try:
            ident = identity(tick)
        except Malformed:
            malformed += 1
            continue
        if running_max is not None and ident[0] < running_max:
            out_of_order += 1
        running_max = ident[0] if running_max is None else max(running_max, ident[0])
        ids.append(ident)

    # sort-and-group over the whole sequence: position plays no part
    by_identity = Counter(sorted(ids))
    exact_duplicate = sum(n - 1 for n in by_identity.values())
    quotes_at: dict[int, set[tuple[str, str]]] = defaultdict(set)
    flags_at: dict[tuple[int, str, str], set[str]] = defaultdict(set)
    for t, bid, ask, flags in by_identity:
        quotes_at[t].add((bid, ask))
        flags_at[(t, bid, ask)].add(flags)
    return {
        "observations": len(ids) + malformed,
        "malformed": malformed,
        "exact_duplicate": exact_duplicate,
        "same_ts_diff_quote": sum(len(q) - 1 for q in quotes_at.values()),
        "same_quote_diff_flags": sum(len(f) - 1 for f in flags_at.values()),
        "out_of_order": out_of_order,
    }


def from_harness_ticks(ticks: Iterable[tuple[int, str, str, int]]) -> list[dict[str, Any]]:
    """The harness tick shape (time_msc, bid_text, ask_text, flags) as oracle records."""
    return [{"time_msc": t, "bid": b, "ask": a, "flags": f} for t, b, a, f in ticks]
