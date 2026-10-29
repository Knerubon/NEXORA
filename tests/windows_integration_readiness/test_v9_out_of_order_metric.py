"""Wave V9 (finding 7): two out-of-order definitions, both pinned, none chosen.

#96 `QualityAccumulator.non_monotonic_count` counts ADJACENT DESCENTS: a tick whose time is lower
than the immediately preceding tick. The #95 oracle `duplicate_oracle.classify(...)["out_of_order"]`
counts ticks BELOW THE RUNNING MAXIMUM. Which one is the contract is an open decision (owner: Rin /
the #96 owner; DEPENDENCY-REGISTER.md). These tests make the difference visible and stop either
side changing silently; they do not assert that one is right.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.windows_integration_readiness.duplicate_oracle import classify
from tests.windows_integration_readiness.test_duplicate_oracle import GOLDEN, T1, A, q

T2, T3, T4 = T1 + 1, T1 + 2, T1 + 3


def adjacent_descents(ticks: list[dict[str, Any]]) -> int:
    """Mirror of the #96 definition (documented, restated; checked against the real code by
    test_v9_pr96_live_export.py when the pinned commit is available)."""
    return sum(1 for a, b in zip(ticks, ticks[1:], strict=False) if b["time_msc"] < a["time_msc"])


def below_running_max(ticks: list[dict[str, Any]]) -> int:
    top = None
    n = 0
    for t in ticks:
        if top is not None and t["time_msc"] < top:
            n += 1
        top = t["time_msc"] if top is None else max(top, t["time_msc"])
    return n


# (name, ticks, adjacent descents (#96), below running max (oracle))
TABLE: list[tuple[str, list[dict[str, Any]], int, int]] = [
    ("ascending", [q(T1, A), q(T2, A), q(T3, A)], 0, 0),
    ("single_descent", [q(T2, A), q(T1, A)], 1, 1),
    ("D6_one_big_then_two_small", [q(T3, A), q(T1, A), q(T2, A)], 1, 2),
    ("two_separated_descents", [q(T2, A), q(T1, A), q(T4, A), q(T3, A)], 2, 2),
    ("long_run_below_max", [q(T4, A), q(T3, A), q(T2, A), q(T1, A)], 3, 3),
    ("late_outlier", [q(T4, A), q(T1, A), q(T2, A), q(T3, A)], 1, 3),
]


@pytest.mark.parametrize(("name", "ticks", "adjacent", "running"), TABLE, ids=[t[0] for t in TABLE])
def test_both_definitions_are_pinned_on_the_table(
    name: str, ticks: list[dict[str, Any]], adjacent: int, running: int
) -> None:
    assert adjacent_descents(ticks) == adjacent
    assert below_running_max(ticks) == running
    assert classify(ticks)["out_of_order"] == running  # the oracle IS the running-max definition


def test_the_definitions_disagree_on_d6_and_that_is_not_hidden() -> None:
    d6 = next(t for t in GOLDEN if t[0] == "D6_out_of_order")[1]
    assert adjacent_descents(d6) == 1  # #96 non_monotonic_count
    assert classify(d6)["out_of_order"] == 2  # #95 oracle
    assert adjacent_descents(d6) != classify(d6)["out_of_order"]


@pytest.mark.parametrize("vector", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_agreement_set_on_the_golden_vectors(
    vector: tuple[str, list[dict[str, Any]], dict[str, int], str],
) -> None:
    name, ticks, _expected, _why = vector
    agree = adjacent_descents(ticks) == classify(ticks)["out_of_order"]
    assert agree == (name != "D6_out_of_order"), name  # D6 is the only golden vector that differs


def test_the_oracle_never_reports_fewer_than_adjacent_descents() -> None:
    """Every adjacent descent is below the running maximum, so oracle >= #96 on any input."""
    import itertools

    for seq in itertools.product((T1, T2, T3), repeat=5):
        ticks = [q(t, A) for t in seq]
        assert classify(ticks)["out_of_order"] >= adjacent_descents(ticks), seq
