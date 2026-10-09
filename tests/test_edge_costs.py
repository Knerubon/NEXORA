"""ENGINEERING tests (synthetic inputs): cost accounting and unknown-cost states."""

from __future__ import annotations

from decimal import Decimal

import pytest
from nexora.edge import (
    KNOWN_ZERO,
    UNAVAILABLE,
    UNKNOWN,
    CostComponent,
    CostInputError,
    CostModel,
    compute_costs,
)

D = Decimal


def model(**overrides: object) -> CostModel:
    base: dict[str, object] = {
        "version": "t1",
        "currency": "XXX",
        "spread": CostComponent("known", D("0.2")),
        "commission": CostComponent("known", D("0.1")),
        "slippage": CostComponent("known", D("0.3")),
        "swap": CostComponent("known", D("0.05")),
    }
    base.update(overrides)
    return CostModel(**base)  # type: ignore[arg-type]


def test_all_known_costs_sum_exactly_and_are_complete() -> None:
    out = compute_costs(model(), size=D("2"), holding_days=D("3"))
    # (0.2 + 0.1 + 0.3) * 2 + 0.05 * 2 * 3
    assert out.known_total == D("1.5")
    assert out.complete and not out.unknown and not out.unavailable


def test_decimal_arithmetic_is_exact_not_float() -> None:
    m = model(
        spread=CostComponent("known", D("0.1")),
        commission=CostComponent("known", D("0.2")),
        slippage=KNOWN_ZERO,
        swap=KNOWN_ZERO,
    )
    assert compute_costs(m, size=D("1"), holding_days=D("0")).known_total == D("0.3")


def test_unknown_cost_is_reported_not_treated_as_zero() -> None:
    out = compute_costs(model(slippage=UNKNOWN), size=D("1"), holding_days=D("0"))
    assert out.unknown == ("slippage",)
    assert not out.complete
    assert dict(out.known).keys() == {"spread", "commission", "swap"}
    assert "slippage" not in dict(out.known)


def test_unavailable_is_distinct_from_unknown() -> None:
    out = compute_costs(
        model(swap=UNAVAILABLE, commission=UNKNOWN), size=D("1"), holding_days=D("1")
    )
    assert out.unavailable == ("swap",)
    assert out.unknown == ("commission",)
    assert not out.complete


def test_spread_from_quotes_requires_an_observation() -> None:
    m = model(spread_from_quotes=True)
    missing = compute_costs(m, size=D("1"), holding_days=D("0"))
    assert missing.unavailable == ("spread",) and not missing.complete
    seen = compute_costs(m, size=D("1"), holding_days=D("0"), observed_spread=D("0.7"))
    assert dict(seen.known)["spread"] == D("0.7") and seen.complete


def test_swap_scales_with_holding_days_but_others_do_not() -> None:
    short = compute_costs(model(), size=D("1"), holding_days=D("1"))
    long = compute_costs(model(), size=D("1"), holding_days=D("10"))
    assert dict(long.known)["swap"] == dict(short.known)["swap"] * 10
    assert dict(long.known)["spread"] == dict(short.known)["spread"]


@pytest.mark.parametrize("bad", [D("-1"), D("NaN"), D("Infinity")])
def test_invalid_known_cost_rejected(bad: Decimal) -> None:
    with pytest.raises(CostInputError):
        CostComponent("known", bad)


def test_known_cost_requires_amount_and_unknown_forbids_it() -> None:
    with pytest.raises(CostInputError):
        CostComponent("known")
    with pytest.raises(CostInputError):
        CostComponent("unknown", D("1"))


@pytest.mark.parametrize("size,days", [(D("0"), D("1")), (D("-1"), D("1")), (D("1"), D("-1"))])
def test_invalid_size_or_holding_rejected(size: Decimal, days: Decimal) -> None:
    with pytest.raises(CostInputError):
        compute_costs(model(), size=size, holding_days=days)


def test_invalid_observed_spread_rejected() -> None:
    with pytest.raises(CostInputError):
        compute_costs(model(), size=D("1"), holding_days=D("0"), observed_spread=D("-0.1"))


def test_extreme_magnitudes_stay_exact() -> None:
    m = model(
        spread=CostComponent("known", D("1E-12")),
        commission=KNOWN_ZERO,
        slippage=KNOWN_ZERO,
        swap=KNOWN_ZERO,
    )
    out = compute_costs(m, size=D("1E+12"), holding_days=D("0"))
    assert out.known_total == D("1")
