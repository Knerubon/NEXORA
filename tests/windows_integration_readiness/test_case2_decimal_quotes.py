"""MATRIX CASE 2 - Decimal-preserving Bid/Ask normalization and quote validity."""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from tests.windows_integration_readiness.reference_model import classify_quote, decimal_text
from tests.windows_integration_readiness.vectors import (
    DECIMAL_TEXT_VECTORS,
    NONFINITE_TEXT,
    QUOTE_VECTORS,
)


@pytest.mark.parametrize(("value", "expected"), DECIMAL_TEXT_VECTORS)
def test_case2_decimal_text_vectors(value: float, expected: str) -> None:
    assert decimal_text(value) == expected


@pytest.mark.parametrize(("value", "expected"), DECIMAL_TEXT_VECTORS)
def test_case2_text_round_trips_to_same_float(value: float, expected: str) -> None:
    assert float(Decimal(expected)) == value


def test_case2_result_does_not_depend_on_ambient_decimal_context() -> None:
    baseline = [decimal_text(v) for v, _ in DECIMAL_TEXT_VECTORS]
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.Emax = 5
        ctx.Emin = -5
        ctx.rounding = "ROUND_DOWN"
        assert [decimal_text(v) for v, _ in DECIMAL_TEXT_VECTORS] == baseline


def test_case2_no_exponent_and_no_trailing_zero() -> None:
    for v, _ in DECIMAL_TEXT_VECTORS:
        t = decimal_text(v)
        assert "e" not in t.lower()
        assert not (("." in t) and t.endswith("0"))


@pytest.mark.parametrize(("raw", "expected"), [(float(k), v) for k, v in NONFINITE_TEXT.items()])
def test_case2_nonfinite_is_preserved_as_text(raw: float, expected: str) -> None:
    assert decimal_text(raw) == expected


@pytest.mark.parametrize(("bid", "ask", "expected"), QUOTE_VECTORS)
def test_case2_quote_classification(bid: float, ask: float, expected: str) -> None:
    assert classify_quote(bid, ask) == expected


def test_case2_float_arithmetic_would_have_been_wrong() -> None:
    """Why exact decimal subtraction matters: the float spread is not the quoted spread."""
    assert 1.1 - 1.0 != 0.1
    assert Decimal(decimal_text(1.1)) - Decimal(decimal_text(1.0)) == Decimal("0.1")
