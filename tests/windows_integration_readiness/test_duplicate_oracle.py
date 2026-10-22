"""Finding B-M4 regression: specification-based duplicate oracle and golden vectors D1-D10.

Every expected count below was derived by hand from ORACLE-SPEC.md (the derivation is written next
to each vector), not by running the oracle. The adjacency-only `order_report` (restated from the
extractor) is run on the same vectors to document the known shared blind spot.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.windows_integration_readiness.duplicate_oracle import (
    Malformed,
    canonical_decimal,
    classify,
    from_harness_ticks,
)
from tests.windows_integration_readiness.reference_model import order_report
from tests.windows_integration_readiness.vectors import TICKS_SYNTH

T1, T2, T3 = 1_000, 2_000, 3_000


def tk(t: int, bid: str = "1.1", ask: str = "1.2", flags: int | None = 6) -> dict[str, Any]:
    d: dict[str, Any] = {"time_msc": t, "bid": bid, "ask": ask}
    if flags is not None:
        d["flags"] = flags
    return d


A = ("1.1", "1.2")
B = ("1.3", "1.4")
C = ("1.5", "1.6")


def q(t: int, quote: tuple[str, str], flags: int | None = 6) -> dict[str, Any]:
    return tk(t, quote[0], quote[1], flags)


def counts(**kw: int) -> dict[str, int]:
    base = {
        "malformed": 0,
        "exact_duplicate": 0,
        "same_ts_diff_quote": 0,
        "same_quote_diff_flags": 0,
        "out_of_order": 0,
    }
    base.update(kw)
    return base


def _big() -> list[dict[str, Any]]:
    # 10 000 strictly increasing distinct ticks; tick 0 repeated at position 5000 and at the end.
    seq = [q(T1 + i, (f"1.{i:05d}", f"2.{i:05d}")) for i in range(10_000)]
    return seq[:5000] + [seq[0]] + seq[5000:] + [seq[0]]


# (id, ticks, expected, derivation)
GOLDEN: list[tuple[str, list[dict[str, Any]], dict[str, int], str]] = [
    (
        "D1_adjacent_exact",
        [q(T1, A), q(T1, A)],
        counts(exact_duplicate=1),
        "identity (T1,A,6) occurs 2x -> 1 duplicate; one quote at T1",
    ),
    (
        "D2_non_adjacent_exact",
        [q(T1, A), q(T1, B), q(T1, A)],
        counts(exact_duplicate=1, same_ts_diff_quote=1),
        "(T1,A,6) 2x -> 1 dup; quotes at T1 = {A,B} -> 2-1 = 1",
    ),
    (
        "D3_three_identical_separated",
        [q(T1, A), q(T2, B), q(T1, A), q(T3, C), q(T1, A)],
        counts(exact_duplicate=2, out_of_order=2),
        "(T1,A) 3x -> 2 dups; 3rd obs T1 < max T2, 5th obs T1 < max T3 -> 2 out of order",
    ),
    (
        "D4_same_ts_different_quote",
        [q(T1, A), q(T1, B)],
        counts(same_ts_diff_quote=1),
        "distinct identities; quotes at T1 = {A,B} -> 1",
    ),
    (
        "D5_same_quote_different_ts",
        [q(T1, A), q(T2, A)],
        counts(),
        "legitimate repeat of a quote at a later time: nothing to report",
    ),
    (
        "D6_out_of_order",
        [q(T3, A), q(T1, B), q(T2, C)],
        counts(out_of_order=2),
        "running max T3: T1 < T3 and T2 < T3 -> 2 (adjacency sees only T1 < T3)",
    ),
    (
        "D7_missing_optional_and_required",
        [q(T1, A), q(T1, A, flags=None), {"time_msc": T2, "bid": "1.1"}],
        counts(same_quote_diff_flags=1, malformed=1),
        "flags 6 vs ABSENT -> not exact, 1 metadata conflict; missing ask -> malformed",
    ),
    (
        "D8_equivalent_representations",
        [tk(T1, "1.10", "1.20"), tk(T1, "1.1", "1.2"), tk(T1, "1.1E0", "+1.20")],
        counts(exact_duplicate=2),
        "all canonicalize to (T1,'1.1','1.2',6) -> 3x -> 2 dups",
    ),
    (
        "D9_large_far_apart",
        _big(),
        counts(exact_duplicate=2, out_of_order=2),
        "tick 0 occurs 3x -> 2 dups; both repeats have T1 < running max -> 2 out of order",
    ),
]


@pytest.mark.parametrize(("name", "ticks", "expected", "why"), GOLDEN)
def test_golden_vector(
    name: str, ticks: list[dict[str, Any]], expected: dict[str, int], why: str
) -> None:
    got = classify(ticks)
    assert got["observations"] == len(ticks)
    assert {k: got[k] for k in expected} == expected, (name, why)


def test_harness_synthetic_ticks_agree_with_adjacency_model_where_adjacent() -> None:
    got = classify(from_harness_ticks(TICKS_SYNTH))
    assert got == {
        "observations": 8,
        "malformed": 0,
        "exact_duplicate": 1,
        "same_ts_diff_quote": 1,
        "same_quote_diff_flags": 0,
        "out_of_order": 0,
    }
    legacy = order_report(TICKS_SYNTH)
    assert legacy["exact_duplicate"] == got["exact_duplicate"]
    assert legacy["same_ts_diff_quote"] == got["same_ts_diff_quote"]


def _as_harness(ticks: list[dict[str, Any]]) -> list[tuple[int, str, str, int]]:
    """Well-formed records only; quotes canonicalized so only the adjacency logic differs."""
    return [
        (t["time_msc"], canonical_decimal(t["bid"]), canonical_decimal(t["ask"]), t.get("flags", 0))
        for t in ticks
        if {"time_msc", "bid", "ask"} <= set(t)
    ]


@pytest.mark.parametrize(
    ("name", "ticks", "expected"),
    [
        (n, t, e)
        for n, t, e, _ in GOLDEN
        if n in ("D2_non_adjacent_exact", "D3_three_identical_separated", "D9_large_far_apart")
    ],
)
def test_known_shared_blind_spot_is_detected(
    name: str, ticks: list[dict[str, Any]], expected: dict[str, int]
) -> None:
    """The adjacency model (= the extractor's quality counting, restated) under-counts exact
    duplicates that are not adjacent. Cross-track defect EXT-DEF-1 for PR #96."""
    legacy = order_report(_as_harness(ticks))
    assert legacy["exact_duplicate"] < expected["exact_duplicate"], name


def test_flags_only_difference_is_not_an_exact_duplicate() -> None:
    """Reviewer B: the adjacency model counts ticks differing only in flags as exact duplicates."""
    ticks = [q(T1, A, flags=2), q(T1, A, flags=4)]
    assert order_report(_as_harness(ticks))["exact_duplicate"] == 1
    got = classify(ticks)
    assert got["exact_duplicate"] == 0 and got["same_quote_diff_flags"] == 1


@pytest.mark.parametrize(
    ("text", "canon"),
    [
        ("1.10", "1.1"),
        ("+1.1", "1.1"),
        ("01.1", "1.1"),
        ("1.1E0", "1.1"),
        ("11E-1", "1.1"),
        ("1E+2", "100"),
        ("-0.0", "0"),
        ("0.000", "0"),
        ("0.00001", "0.00001"),
        ("NaN", "NaN"),
        ("-Infinity", "-Infinity"),
        ("123456789012345678901234567890.5", "123456789012345678901234567890.5"),
    ],
)
def test_canonical_decimal(text: str, canon: str) -> None:
    assert canonical_decimal(text) == canon


@pytest.mark.parametrize("bad", [1.1, True, None, "", " ", "1,1", "abc", 1])
def test_canonical_decimal_refuses_non_text_and_garbage(bad: Any) -> None:
    with pytest.raises(Malformed):
        canonical_decimal(bad)


@pytest.mark.parametrize(
    "tick",
    [
        {"time_msc": 1.0, "bid": "1", "ask": "2"},
        {"time_msc": True, "bid": "1", "ask": "2"},
        {"bid": "1", "ask": "2"},
        {"time_msc": 1, "bid": "1", "ask": "2", "flags": "6"},
        {"time_msc": 1, "bid": 1.1, "ask": "2"},
    ],
)
def test_malformed_records_are_counted_not_classified(tick: dict[str, Any]) -> None:
    assert classify([tick, tk(T1)]) == {**counts(malformed=1), "observations": 2}


def test_order_of_input_does_not_change_duplicate_counts() -> None:
    ticks = [q(T1, A), q(T2, B), q(T1, A), q(T3, C), q(T1, A), q(T2, B)]
    fwd, rev = classify(ticks), classify(list(reversed(ticks)))
    for k in ("exact_duplicate", "same_ts_diff_quote", "same_quote_diff_flags"):
        assert fwd[k] == rev[k]


# ---- D10: mutants of the duplicate logic must be killed by the golden vectors --------------------
def _mutant_adjacent_only(ticks: list[dict[str, Any]]) -> dict[str, int]:
    legacy = order_report(_as_harness(ticks))
    return classify(ticks) | {
        "exact_duplicate": legacy["exact_duplicate"],
        "same_ts_diff_quote": legacy["same_ts_diff_quote"],
    }


def _mutant_ignore_flags(ticks: list[dict[str, Any]]) -> dict[str, int]:
    stripped = [{k: v for k, v in t.items() if k != "flags"} for t in ticks]
    return classify(stripped)


def _mutant_raw_text(ticks: list[dict[str, Any]]) -> dict[str, int]:
    seen: set[tuple[Any, ...]] = set()
    dup = 0
    for t in ticks:
        key = (t.get("time_msc"), t.get("bid"), t.get("ask"), t.get("flags"))
        dup += key in seen
        seen.add(key)
    return classify(ticks) | {"exact_duplicate": dup}


def _mutant_adjacent_out_of_order(ticks: list[dict[str, Any]]) -> dict[str, int]:
    ts = [t["time_msc"] for t in ticks if isinstance(t.get("time_msc"), int)]
    return classify(ticks) | {"out_of_order": sum(b < a for a, b in zip(ts, ts[1:], strict=False))}


MUTANTS: list[tuple[str, Callable[[list[dict[str, Any]]], dict[str, int]]]] = [
    ("adjacent_only_duplicates", _mutant_adjacent_only),
    ("flags_ignored", _mutant_ignore_flags),
    ("no_canonicalization", _mutant_raw_text),
    ("adjacent_out_of_order", _mutant_adjacent_out_of_order),
]


@pytest.mark.parametrize(("name", "mutant"), MUTANTS)
def test_d10_mutant_is_killed_by_some_golden_vector(
    name: str, mutant: Callable[[list[dict[str, Any]]], dict[str, int]]
) -> None:
    killed = []
    for vec, ticks, expected, _ in GOLDEN:
        got = mutant(ticks)
        if any(got.get(k) != v for k, v in expected.items()):
            killed.append(vec)
    assert killed, f"mutant {name} survived every golden vector"
