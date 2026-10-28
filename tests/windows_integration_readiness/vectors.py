"""Golden vectors and layout facts shared by the harness and (by copy) the Windows operator steps.

Every value is synthetic. Nothing here was observed from a real terminal: the MT5 tick layout is
the DOCUMENTED layout and stays NOT VERIFIED until case 1 is executed on the Windows node.
"""

from __future__ import annotations

# CASE 1 - documented layout of MetaTrader5.copy_ticks_range(..., COPY_TICKS_ALL) rows.
DOCUMENTED_TICK_DESCR: tuple[tuple[str, str], ...] = (
    ("time", "<i8"),
    ("bid", "<f8"),
    ("ask", "<f8"),
    ("last", "<f8"),
    ("volume", "<u8"),
    ("time_msc", "<i8"),
    ("flags", "<u4"),
    ("volume_real", "<f8"),
)
REQUIRED_TICK_FIELDS = ("time_msc", "bid", "ask")

# CASE 7 - the read-only allowlist (copied from the stacked extractor at e74ad03; re-verify at the
# named SHA). Anything else is refused.
ALLOWED_MT5_CALLS = frozenset(
    {
        "initialize",
        "shutdown",
        "last_error",
        "version",
        "terminal_info",
        "symbol_info",
        "copy_ticks_range",
        "COPY_TICKS_ALL",
    }
)
FORBIDDEN_MT5_NAMES = (
    "login",
    "order_send",
    "order_check",
    "order_calc_margin",
    "order_calc_profit",
    "positions_get",
    "positions_total",
    "orders_get",
    "orders_total",
    "history_orders_get",
    "history_deals_get",
    "account_info",
    "market_book_add",
    "symbol_select",
    "copy_rates_from",
    "TRADE_ACTION_DEAL",
)

# CASE 2 - float64 -> exact decimal text via repr(). (input float, expected text)
DECIMAL_TEXT_VECTORS: tuple[tuple[float, str], ...] = (
    (1.1, "1.1"),
    (1.10000, "1.1"),
    (0.1, "0.1"),
    (1e-05, "0.00001"),
    (1.2345e-07, "0.00000012345"),
    (123456.0, "123456"),
    (1e16, "10000000000000000"),
    (-0.0, "0"),
    (0.0, "0"),
    (0.1 + 0.2, "0.30000000000000004"),
)
NONFINITE_TEXT = {"nan": "NaN", "inf": "Infinity", "-inf": "-Infinity"}
# (bid, ask, expected class)
QUOTE_VECTORS: tuple[tuple[float, float, str], ...] = (
    (1.1, 1.2, "ok"),
    (1.1, 1.1, "zero_spread"),
    (1.2, 1.1, "crossed"),
    (0.0, 1.1, "nonpositive"),
    (-1.0, 1.1, "nonpositive"),
    (float("nan"), 1.1, "nonfinite"),
    (1.1, float("inf"), "nonfinite"),
)

# CASE 3/4 - time conversion. Raw ms are synthetic epoch values; offsets are NOT claims about any
# broker. segment = (from_raw, to_raw|None, offset_seconds, evidence_ref)
H = 3_600_000
T0 = 1_700_000_000_000
SEGMENTS_SINGLE = ((T0, None, 7200, "SYNTHETIC-EVIDENCE-1"),)
# offset falls by 3600 s at raw boundary T0 + 100h (fall-back shaped, synthetic).
SEGMENTS_FALLBACK = (
    (T0, T0 + 100 * H, 10800, "SYNTHETIC-EVIDENCE-2"),
    (T0 + 100 * H, None, 7200, "SYNTHETIC-EVIDENCE-2"),
)
EXPECTED_FOLD_FALLBACK = ((T0 + 99 * H, T0 + 101 * H),)
# a rising offset (spring-forward shaped) makes a gap, not a fold.
SEGMENTS_SPRING = (
    (T0, T0 + 100 * H, 7200, "SYNTHETIC-EVIDENCE-3"),
    (T0 + 100 * H, None, 10800, "SYNTHETIC-EVIDENCE-3"),
)

# CASE 5 - synthetic ticks as (time_msc, bid_text, ask_text, flags)
TICKS_SYNTH: tuple[tuple[int, str, str, int], ...] = (
    (T0 + 0, "1.1", "1.2", 6),
    (T0 + 500, "1.1", "1.2", 6),
    (T0 + 500, "1.1", "1.2", 6),  # exact duplicate
    (T0 + 500, "1.11", "1.21", 6),  # same ts, different quote
    (T0 + H - 1, "1.12", "1.22", 2),  # last ms of chunk 1
    (T0 + H, "1.13", "1.23", 2),  # first ms of chunk 2 (half-open boundary)
    (T0 + 2 * H - 1, "1.14", "1.24", 4),
    (T0 + 2 * H + 7, "1.15", "1.25", 4),
)
