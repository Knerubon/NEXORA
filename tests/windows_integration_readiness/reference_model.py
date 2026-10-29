"""Reference model of the contract the real extractor must meet. Passing against it proves the
harness expectations are self-consistent, nothing about `nexora.market_history`.

Independence is NOT uniform (finding B-M4); ORACLE-SPEC.md section 5 classifies every function:
  * RESTATED from the extractor (shares its assumptions; a shared error passes both):
    `decimal_text`, `classify_quote`, `to_utc_msc`, `derived_fold_ranges`, `order_report`.
    Each has an independent cross-check elsewhere: hand-written vectors (`vectors.py`), the
    brute-force fold simulation (`test_oracle_independence.py`), and the specification-based
    duplicate oracle (`duplicate_oracle.py`), which exposes `order_report`'s adjacency blind spot.
  * SPECIFIED here (behaviour the extractor does not define, or defines differently, so the
    package states its own rule): `check_window`, `split_around_folds`, `check_time_fields`,
    the offset bound in `validate_segments`.
Refusal codes use the extractor's names as read at the pinned PR #96 candidate (minor F5) so
a real-half result can be compared code for code; `basis` (`unsupported_offset_basis`) is not
modelled.

Pure Python, no I/O, no MT5, no `nexora` imports.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal
from typing import Any


class ReadinessError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code


# ---- case 1 ---------------------------------------------------------------------------------
def check_tick_layout(descr: Iterable[tuple[str, str]], required: Sequence[str]) -> list[str]:
    """Return the problems found in a structured-array `dtype.descr`. Empty list = acceptable.

    Requires every field in `required`, time_msc integer, bid/ask 8-byte float. Extra fields are
    fine; missing `flags` is tolerated by the extractor (flags -> None) and is reported separately.
    """
    fields = dict(descr)
    problems = [f"missing_field:{n}" for n in required if n not in fields]
    if "time_msc" in fields and not fields["time_msc"].endswith(("i8", "u8")):
        problems.append(f"time_msc_not_64bit_int:{fields['time_msc']}")
    for n in ("bid", "ask"):
        if n in fields and not fields[n].endswith("f8"):
            problems.append(f"{n}_not_float64:{fields[n]}")
    if "flags" not in fields:
        problems.append("note_flags_absent")
    return problems


# ---- case 2 ---------------------------------------------------------------------------------
def decimal_text(value: float) -> str:
    """float64 -> shortest round-trip decimal text, no exponent, no trailing zeros.

    Built from Decimal(repr(value)).as_tuple() with integer/string work only, so it cannot depend
    on the ambient decimal context. Non-finite values are PRESERVED as text, never dropped."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    sign, digits, exp = Decimal(repr(float(value))).as_tuple()
    assert isinstance(exp, int)
    n = int("".join(map(str, digits)))
    if n == 0:
        return "0"
    while n % 10 == 0:
        n //= 10
        exp += 1
    text = str(n)
    if exp >= 0:
        text += "0" * exp
    elif len(text) <= -exp:
        text = "0." + "0" * (-exp - len(text)) + text
    else:
        text = text[:exp] + "." + text[exp:]
    return ("-" if sign else "") + text


def classify_quote(bid: float, ask: float) -> str:
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return "nonfinite"
    if bid <= 0 or ask <= 0:
        return "nonpositive"
    if ask < bid:
        return "crossed"
    if ask == bid:
        return "zero_spread"
    return "ok"


# ---- cases 3 and 4 --------------------------------------------------------------------------
Segment = tuple[int, int | None, int, str]  # from_raw, to_raw (excl.)|None, offset_s, evidence_ref

# Python's own bound for a UTC offset (datetime.timezone requires |offset| < 24 h). Not a broker
# claim and not a quality threshold; the extractor's TimeSegment has no bound (EXT-DEF-2).
MAX_ABS_OFFSET_S = 86_399


def validate_segments(segments: Sequence[Segment]) -> None:
    if not segments:
        raise ReadinessError("time_conversion_required")
    for f, t, off, ev in segments:
        if type(f) is not int or type(off) is not int or not (t is None or type(t) is int):
            raise ReadinessError("time_segment_malformed")
        if not isinstance(ev, str) or not ev.strip():
            raise ReadinessError("offset_evidence_required")
        if t is not None and t <= f:
            raise ReadinessError("empty_segment")
        if abs(off) > MAX_ABS_OFFSET_S:
            raise ReadinessError("offset_out_of_range", str(off))
    for prev, nxt in zip(segments, segments[1:], strict=False):
        if nxt[0] < prev[0]:
            raise ReadinessError("segments_not_sorted")
        if prev[1] is None or prev[1] > nxt[0]:
            raise ReadinessError("segments_overlap")


def check_time_fields(time_s: int, time_msc: int) -> None:
    """MT5 rows carry both `time` (s) and `time_msc` (ms); they must agree (floor division)."""
    if type(time_s) is not int or type(time_msc) is not int or time_s != time_msc // 1000:
        raise ReadinessError("time_fields_conflict", f"{time_s}/{time_msc}")


def to_utc_msc(raw: int, segments: Sequence[Segment]) -> int:
    validate_segments(segments)
    for f, t, off, _ in segments:
        if raw >= f and (t is None or raw < t):
            return raw - off * 1000
    raise ReadinessError("no_declared_offset_for_time", str(raw))


def derived_fold_ranges(segments: Sequence[Segment]) -> tuple[tuple[int, int], ...]:
    """Falling offset between segments -> raw time repeats; both sides of the raw boundary are
    ambiguous because a raw-keyed boundary cannot say which side repeats."""
    validate_segments(segments)
    out = []
    for prev, nxt in zip(segments, segments[1:], strict=False):
        drop = (prev[2] - nxt[2]) * 1000
        if drop > 0 and prev[1] is not None:
            out.append((prev[1] - drop, nxt[0] + drop))
    return tuple(out)


def _covered(segments: Sequence[Segment], lo: int, hi: int) -> bool:
    """[lo, hi) lies inside the union of declared segments (gaps are not covered)."""
    cursor = lo
    for f, t, _, _ in segments:
        if f > cursor:
            return False
        if t is None or t > cursor:
            cursor = hi if t is None else t
        if cursor >= hi:
            return True
    return cursor >= hi


def check_window(
    segments: Sequence[Segment],
    lo: int,
    hi: int,
    *,
    attested_ambiguous: Sequence[tuple[int, int]] = (),
) -> None:
    validate_segments(segments)
    if type(lo) is not int or type(hi) is not int or hi <= lo:
        raise ReadinessError("invalid_extraction_request", f"{lo}-{hi}")
    if not _covered(segments, lo, hi):
        raise ReadinessError("time_conversion_does_not_cover_window", f"{lo}-{hi}")
    for a, b in derived_fold_ranges(segments):
        if a < hi and lo < b:
            raise ReadinessError("window_touches_derived_fold_range", f"{a}-{b}")
    for a, b in attested_ambiguous:
        if a < hi and lo < b:
            raise ReadinessError("window_touches_ambiguous_raw_range", f"{a}-{b}")


def split_around_folds(
    segments: Sequence[Segment],
    lo: int,
    hi: int,
    *,
    attested_ambiguous: Sequence[tuple[int, int]] = (),
) -> tuple[tuple[int, int], ...]:
    """Windows covering [lo, hi) minus every derived fold range and every attested ambiguous
    range, so each returned window passes `check_window` with the same attestation."""
    cursor, out = lo, []
    for a, b in sorted((*derived_fold_ranges(segments), *attested_ambiguous)):
        if b <= cursor or a >= hi:
            continue
        if a > cursor:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < hi:
        out.append((cursor, hi))
    return tuple(out)


# ---- case 5 ---------------------------------------------------------------------------------
Tick = tuple[int, str, str, int]


def chunk_windows(lo: int, hi: int, chunk_ms: int) -> list[tuple[int, int]]:
    if chunk_ms <= 0 or hi <= lo:
        raise ReadinessError("invalid_chunking")
    out, a = [], lo
    while a < hi:
        b = min(a + chunk_ms, hi)
        out.append((a, b))
        a = b
    return out


def encode_tick(t: Tick) -> bytes:
    row = {"t": t[0], "b": t[1], "a": t[2], "f": t[3]}
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()


def build_chunks(
    ticks: Sequence[Tick], lo: int, hi: int, chunk_ms: int, *, fetch_extra: int = 0
) -> list[bytes]:
    """Half-open window filter per chunk; `fetch_extra` simulates a source that returns ticks just
    outside the requested window (rounded-to-second API bounds), which must be dropped."""
    bodies = []
    for a, b in chunk_windows(lo, hi, chunk_ms):
        fetched = [t for t in ticks if a - fetch_extra <= t[0] < b + fetch_extra]
        bodies.append(b"".join(encode_tick(t) for t in fetched if a <= t[0] < b))
    return bodies


def content_sha256(bodies: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for b in bodies:
        h.update(b)
    return h.hexdigest()


def order_report(ticks: Sequence[Tick]) -> dict[str, int]:
    """RESTATED from the extractor's adjacency-only quality counting (quality.py at earlier #96
    heads, see dependency-manifest.json stale references): compares each tick with its predecessor
    only, and ignores flags. Kept to document the extractor's behaviour at those heads; it MISSES
    non-adjacent duplicates. The oracle for duplicates is
    `duplicate_oracle.classify` (ORACLE-SPEC.md)."""
    nm = dup = same_ts = 0
    for p, c in zip(ticks, ticks[1:], strict=False):
        if c[0] < p[0]:
            nm += 1
        elif c[0] == p[0]:
            if c[1:3] == p[1:3]:
                dup += 1
            else:
                same_ts += 1
    return {"non_monotonic": nm, "exact_duplicate": dup, "same_ts_diff_quote": same_ts}


# ---- case 7 ---------------------------------------------------------------------------------
class ReadOnlyGate:
    """Attribute gate: only allowlisted names reach the wrapped module; shutdown on every path.
    The module lives only in a closure, never in an attribute, so no attribute of the gate hands
    out the raw module (minor F8: the earlier `_m` attribute did)."""

    __slots__ = ("_fetch",)

    def __init__(self, module: Any, allowed: frozenset[str]) -> None:
        def fetch(name: str) -> Any:
            if name not in allowed:
                raise ReadinessError("mt5_call_not_allowlisted", name)
            return getattr(module, name)

        object.__setattr__(self, "_fetch", fetch)

    def __getattr__(self, name: str) -> Any:
        fetch: Callable[[str], Any] = object.__getattribute__(self, "_fetch")
        return fetch(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise ReadinessError("read_only_gate_is_immutable")


def run_with_cleanup(gate: ReadOnlyGate, body: Callable[[ReadOnlyGate], Any]) -> Any:
    if not gate.initialize():
        raise ReadinessError("initialize_failed", str(gate.last_error()))
    try:
        return body(gate)
    finally:
        gate.shutdown()
