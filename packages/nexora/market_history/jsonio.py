"""Hostile-input-safe JSON reading and timestamp parsing for evidence files (N-01).

Every file that can influence a trust decision (manifest, chunk rows, store entries, registry, pin,
anchor, lock record) is attacker-controllable. `json.loads` raises `RecursionError` on deep
nesting and `datetime.astimezone` raises `OverflowError` at the calendar edges; neither is a
`ValueError`, so a handler that only catches `ValueError` lets them escape as an uncontrolled
crash. These helpers turn every such failure into a typed `HistoryError` (a `ValueError`), so the
existing fail-closed handlers apply and a verdict is always UNTRUSTED with a stable reason code.

Limits are mechanical sanity bounds against resource abuse, not policy: legitimate evidence is far
inside them (manifests nest ~5 levels; the bound is 32).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from nexora.market_history.models import HistoryError

MAX_JSON_DEPTH = 32
# Largest `--max-pin-age-seconds` accepted: 10 years. A mechanical overflow guard (timedelta and
# datetime arithmetic stay well inside their range), not a statement about acceptable pin age:
# choosing the real maximum remains a governance decision of the verifier.
MAX_AGE_SECONDS_SANITY = 10 * 366 * 86400

# Depth scan, strictly linear and mostly C-speed:
#   1. delete every string literal. The pattern ALWAYS matches from an opening quote (an
#      unterminated string runs to the end of the input), so a failing match is never retried
#      from the next quote; a first version did that and was quadratic (100 KB of `"\\` cost
#      over a minute);
#   2. keep only the four bracket characters;
#   3. walk those (a Python loop over brackets alone, with an early exit past `stop_above`).
_STRINGS = re.compile(rb'"(?:[^"\\]++|\\.)*+(?:"|\\?\Z)', re.DOTALL)
_NOT_BRACKETS = bytes(sorted(set(range(256)) - set(b"[]{}")))
_OPEN_CLOSE = bytes.maketrans(b"[{]}", b"\x01\x01\x00\x00")


def json_depth(data: bytes, stop_above: int | None = None) -> int:
    """Maximum bracket nesting of `data`, ignoring brackets inside strings. Linear time, no
    recursion. Malformed input is not diagnosed here; the real parser rejects it afterwards. With
    `stop_above` the walk stops as soon as the depth exceeds it (the returned value is then only
    known to be greater than `stop_above`)."""
    brackets = _STRINGS.sub(b"", data).translate(None, _NOT_BRACKETS).translate(_OPEN_CLOSE)
    depth = deepest = 0
    for opens in brackets:
        if opens:
            depth += 1
            if depth > deepest:
                deepest = depth
                if stop_above is not None and deepest > stop_above:
                    break
        else:
            depth -= 1
    return deepest


def loads(data: bytes | str, what: str = "json", *, check_depth: bool = True) -> Any:
    """`json.loads` that can only fail with `HistoryError` (code `json_invalid` or
    `json_too_deep`). `check_depth=False` is for streams of small flat rows, where the parser's
    own recursion limit is still caught.

    Bytes are decoded strictly as UTF-8 here, and the depth scan and the parser both see that one
    text. `json.loads(bytes)` would auto-detect UTF-16/UTF-32, whose code units can contain byte
    0x22 (U+2200 is `00 22`) and desynchronise the byte-level string stripper, letting nesting far
    past the bound through (PR #99 review M-1). A UTF-8 BOM is refused by the parser."""
    if isinstance(data, str):
        text = data
        raw = data.encode("utf-8", "surrogatepass")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise HistoryError("json_invalid", what) from None
        raw = data  # valid UTF-8: bytes 0x22/0x5C/brackets only ever encode those ASCII characters
    if check_depth and json_depth(raw, MAX_JSON_DEPTH) > MAX_JSON_DEPTH:
        raise HistoryError("json_too_deep", what)
    try:
        return json.loads(text)
    except RecursionError:
        raise HistoryError("json_too_deep", what) from None
    except (ValueError, UnicodeError):  # JSONDecodeError is a ValueError
        raise HistoryError("json_invalid", what) from None


def aware_utc(value: Any, what: str) -> datetime:
    """ISO-8601 timestamp WITH an offset, as UTC; else `HistoryError(invalid_timestamp)`.
    Covers `OverflowError` for instants outside the representable calendar range."""
    try:
        when = datetime.fromisoformat(str(value))
        if when.tzinfo is None:
            raise HistoryError("invalid_timestamp", what)
        return when.astimezone(UTC)
    except (ValueError, OverflowError):
        raise HistoryError("invalid_timestamp", what) from None
