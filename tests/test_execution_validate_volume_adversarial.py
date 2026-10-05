"""Adversarial regression tests for the total, exact ``validate_volume`` (PR-6)."""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest
from nexora.autonomous.broker_capabilities import (
    REASON_VOLUME_ABOVE_MAX,
    REASON_VOLUME_BELOW_MIN,
    REASON_VOLUME_STEP_MISMATCH,
    BrokerCapabilities,
    validate_volume,
)
from nexora.market_data.instruments import FeedBinding, InstrumentDefinition, PriceGrid

NOW = datetime(2026, 1, 1, tzinfo=UTC)
D = Decimal


def _caps(**overrides: object) -> BrokerCapabilities:
    fields: dict[str, object] = {
        "binding": FeedBinding(
            instrument_id="inst-1",
            broker_id="broker-a",
            symbol="SYM",
            price_grid=PriceGrid(digits=2, point=D("0.01"), trade_tick_size=None),
            time_offset_seconds=0,
        ),
        "instrument": InstrumentDefinition(
            instrument_id="inst-1",
            currency_base="USD",
            currency_profit="USD",
            trade_calc_mode=0,
            trade_contract_size=D("100"),
            chart_mode=0,
        ),
        "volume_min": D("0.10"),
        "volume_max": D("10"),
        "volume_step": D("0.05"),
        "stops_level": D("0"),
        "freeze_level": D("0"),
        "filling_modes": ("FOK",),
        "execution_mode": "market",
        "session_policy_ref": "p:s",
        "spread_policy_ref": "p:sp",
        "margin_policy_ref": "p:m",
        "observed_at": NOW,
    }
    fields.update(overrides)
    return BrokerCapabilities(**fields)  # type: ignore[arg-type]


def _oracle(
    vmin: Decimal, vmax: Decimal, step: Decimal, anchor: Decimal | None, q: Decimal
) -> str | None:
    """Independent exact oracle (Fraction arithmetic; moderate sizes only)."""

    fq, fmin, fmax, fs = Fraction(q), Fraction(vmin), Fraction(vmax), Fraction(step)
    fa = fmin if anchor is None else Fraction(anchor)
    if fq < fmin:
        return REASON_VOLUME_BELOW_MIN
    if fq > fmax:
        return REASON_VOLUME_ABOVE_MAX
    return None if ((fq - fa) / fs).denominator == 1 else REASON_VOLUME_STEP_MISMATCH


def test_previously_reproduced_huge_quotient() -> None:
    caps = _caps(volume_max=D("1E+40"), volume_step=D("0.01"))
    assert validate_volume(caps, D("1E+35")) is None


def test_extreme_precision_thousands_of_digits() -> None:
    n = 6000
    big = D("1" + "0" * n)
    caps = _caps(volume_min=D(1), volume_max=big * 10, volume_step=D(1))
    assert validate_volume(caps, D("1" + "0" * (n - 1) + "7")) is None
    assert validate_volume(caps, D("1" + "0" * n + ".5")) == REASON_VOLUME_STEP_MISMATCH
    fine = D("0." + "0" * (n - 1) + "1")
    caps2 = _caps(volume_min=fine, volume_max=big * 10, volume_step=fine)
    assert validate_volume(caps2, D("1" + "0" * (n - 1) + "3")) is None
    assert validate_volume(caps2, D("0." + "0" * (n - 1) + "13")) == REASON_VOLUME_STEP_MISMATCH


@pytest.mark.parametrize("exp", [10**15, 10**12, 10**6])
def test_extreme_exponents_and_gaps(exp: int) -> None:
    t0 = time.monotonic()
    vmax = D(f"1E+{exp * 2}")
    caps = _caps(volume_min=D("0.1"), volume_max=vmax, volume_step=D("0.05"))
    assert validate_volume(caps, D(f"1E+{exp}")) is None  # (1E+exp - 0.1)/0.05 integral
    caps_odd = _caps(volume_min=D("0.1"), volume_max=vmax, volume_step=D("3"))
    assert validate_volume(caps_odd, D(f"1E+{exp}")) == REASON_VOLUME_STEP_MISMATCH
    caps_big = _caps(volume_min=D(1), volume_max=vmax, volume_step=D(f"1E+{exp // 2}"))
    # (2E+exp - 1) is not a multiple of 1E+(exp/2) (it is odd)
    assert validate_volume(caps_big, D(f"2E+{exp}")) == REASON_VOLUME_STEP_MISMATCH
    # anchor 0 makes 2E+exp a multiple of 1E+(exp/2)
    caps_big0 = _caps(
        volume_min=D(1), volume_max=vmax, volume_step=D(f"1E+{exp // 2}"), volume_step_anchor=D(0)
    )
    assert validate_volume(caps_big0, D(f"2E+{exp}")) is None
    tiny = D(f"1E-{exp}")
    caps_tiny = _caps(volume_min=tiny, volume_max=D(10), volume_step=tiny)
    assert validate_volume(caps_tiny, D("5")) is None
    assert validate_volume(caps_tiny, D(f"5E-{exp}")) is None
    assert validate_volume(caps_tiny, D(f"5.5E-{exp}")) == REASON_VOLUME_STEP_MISMATCH
    assert time.monotonic() - t0 < 60  # generous smoke guard only


def test_equivalent_decimal_representations_identical() -> None:
    mins = ["0.1", "0.10", "1E-1", "10E-2", "0.1000000000000000000000000000000000"]
    steps = ["0.05", "5E-2", "0.050", "50E-3"]
    maxes = ["10", "1E+1", "10.0", "100E-1", "0.1E+2"]
    for qs in (["0.15", "15E-2", "0.150", "1.5E-1"], ["0.12", "12E-2", "0.120"], mins, maxes):
        results = {
            validate_volume(_caps(volume_min=D(a), volume_step=D(s), volume_max=D(m)), D(q))
            for q in qs
            for a in mins
            for s in steps
            for m in maxes
        }
        assert len(results) == 1
    anchors = ["0.02", "2E-2", "0.020", "20E-3"]
    assert {
        validate_volume(_caps(volume_step_anchor=D(a)), D(q))
        for a in anchors
        for q in ("0.12", "12E-2", "0.120", "1.2E-1")
    } == {None}


def test_equivalent_zeros() -> None:
    for zero in ("0", "0.00", "0E+999999999999", "0E-999999999999", "-0", "-0.000"):
        caps = _caps(volume_step_anchor=D(zero), volume_min=D("0.05"), volume_step=D("0.05"))
        assert validate_volume(caps, D("0.10")) is None
        assert validate_volume(caps, D("0.12")) == REASON_VOLUME_STEP_MISMATCH
        assert validate_volume(caps, D(zero)) == REASON_VOLUME_BELOW_MIN


@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity", "-NaN"])
def test_non_finite(bad: str) -> None:
    assert validate_volume(_caps(), D(bad)) == REASON_VOLUME_STEP_MISMATCH


@pytest.mark.parametrize("bad", [1, "1", None, 1.0, True, False, b"1", [D(1)], (D(1),)])
def test_malformed_quantities(bad: object) -> None:
    assert validate_volume(_caps(), bad) == REASON_VOLUME_STEP_MISMATCH  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [None, object(), "caps", 1, b"x", [], {}])
def test_non_capabilities(bad: object) -> None:
    assert validate_volume(bad, D("1")) == REASON_VOLUME_STEP_MISMATCH  # type: ignore[arg-type]


def test_anchor_variants() -> None:
    assert validate_volume(_caps(), D("0.15")) is None  # default anchor volume_min
    above = _caps(volume_step_anchor=D("5"))
    assert validate_volume(above, D("0.15")) is None  # 5 - 0.15 = 97 * 0.05
    assert validate_volume(above, D("0.12")) == REASON_VOLUME_STEP_MISMATCH
    below = _caps(volume_step_anchor=D("0.02"))
    assert validate_volume(below, D("0.12")) is None
    neg = _caps(volume_step_anchor=D("-0.03"))
    assert validate_volume(neg, D("0.12")) is None  # 0.15 = 3 * 0.05
    assert validate_volume(neg, D("0.15")) == REASON_VOLUME_STEP_MISMATCH
    huge_anchor = _caps(volume_step_anchor=D("1E+500000000000"), volume_step=D("0.05"))
    assert validate_volume(huge_anchor, D("0.15")) is None  # 1E+k is a multiple of 0.05


@pytest.mark.parametrize("prec", [1, 5, 28, 1000])
def test_context_precision_independence(prec: int) -> None:
    cases = [
        (_caps(volume_max=D("1E+40"), volume_step=D("0.01")), D("1E+35")),
        (_caps(), D("0.15")),
        (_caps(), D("0.12")),
        (_caps(), D("0.05")),
        (_caps(), D("10.05")),
        (_caps(volume_step_anchor=D("0.02")), D("0.12")),
        (_caps(volume_min=D(1), volume_max=D("1E+40"), volume_step=D(1)), D("1" + "0" * 35 + ".5")),
    ]
    with localcontext() as ctx:
        ctx.prec = 28
        baseline = [validate_volume(c, q) for c, q in cases]
    with localcontext() as ctx:
        ctx.prec = prec
        assert [validate_volume(c, q) for c, q in cases] == baseline
    with localcontext() as ctx:
        ctx.prec = prec
        for trap in list(ctx.traps):
            ctx.traps[trap] = True
        assert [validate_volume(c, q) for c, q in cases] == baseline


def _rand_dec(rng: random.Random, positive: bool = False) -> Decimal:
    magnitude = rng.randint(1, 10 ** rng.randint(1, 12))
    coeff = magnitude if positive or rng.random() < 0.5 else -magnitude
    return D(f"{coeff}E{rng.randint(-15, 15)}")


def test_randomized_agreement_with_exact_oracle() -> None:
    rng = random.Random(20261005)
    for _ in range(6000):
        step = _rand_dec(rng, True)
        vmin = _rand_dec(rng, True)
        vmax = vmin + abs(_rand_dec(rng)) + 1
        anchor = None if rng.random() < 0.4 else _rand_dec(rng)
        if rng.random() < 0.5:  # near-grid quantities exercise the integral side
            base = vmin if anchor is None else anchor
            with localcontext() as ctx:
                ctx.prec = 400
                q = base + step * rng.randint(-5, 50)
                if rng.random() < 0.3:
                    q = q + D(f"1E{rng.randint(-20, 0)}")
        else:
            q = _rand_dec(rng)
        caps = _caps(volume_min=vmin, volume_max=vmax, volume_step=step, volume_step_anchor=anchor)
        assert validate_volume(caps, q) == _oracle(vmin, vmax, step, anchor, q), (
            vmin,
            vmax,
            step,
            anchor,
            q,
        )


def test_structured_edge_cases_against_oracle() -> None:
    values = ["0", "1", "-1", "0.5", "0.25", "3", "7", "1E+30", "1E-30", "12345678901234567890"]
    values.append("2.5E+20")
    for vs in values:
        for ss in ("1", "0.5", "3", "0.01", "1E+10", "1E-20", "7", "0.125", "25"):
            for a in (None, "0", "1", "-3", "1E+25", "1E-25"):
                step, q = D(ss), D(vs)
                anchor = None if a is None else D(a)
                vmin, vmax = D("1E-40"), D("1E+60")
                caps = _caps(
                    volume_min=vmin, volume_max=vmax, volume_step=step, volume_step_anchor=anchor
                )
                assert validate_volume(caps, q) == _oracle(vmin, vmax, step, anchor, q), (vs, ss, a)


def test_bounds_order_unchanged() -> None:
    caps = _caps()
    assert validate_volume(caps, D("0.05")) == REASON_VOLUME_BELOW_MIN
    assert validate_volume(caps, D("10.05")) == REASON_VOLUME_ABOVE_MAX
    assert validate_volume(caps, D("-0")) == REASON_VOLUME_BELOW_MIN


def _exact_5_pow(k: int, times: int = 1) -> Decimal:
    """Exact ``times * 5**k`` (``Decimal(int)`` on huge ints is very slow)."""

    with localcontext() as ctx:
        ctx.prec = 1_000_000
        ctx.Emax = 10**15
        ctx.Emin = -(10**15)
        return D(5) ** k * times


def _wide_caps(**overrides: object) -> BrokerCapabilities:
    return _caps(volume_min=D(1), volume_max=D("1E+2000000"), **overrides)


def test_large_coefficient_shapes_complete_fast() -> None:
    """Shapes that were quadratic with the repeated-division 5-valuation."""

    t0 = time.monotonic()
    zero = D(0)
    ten_60k = D("1" + "0" * 60000)
    caps = _wide_caps(volume_step=D(1), volume_step_anchor=zero)
    assert validate_volume(caps, ten_60k) is None
    caps = _wide_caps(volume_step=D("1" + "0" * 30000), volume_step_anchor=zero)
    assert validate_volume(caps, ten_60k) is None
    assert validate_volume(caps, D("1" + "0" * 29999)) == REASON_VOLUME_STEP_MISMATCH
    five_60k = _exact_5_pow(85000)  # about 59,400 digits
    assert len(five_60k.as_tuple().digits) > 59000
    caps = _wide_caps(volume_step=D(5), volume_step_anchor=zero)
    assert validate_volume(caps, five_60k) is None
    assert (
        validate_volume(caps, D((0, _exact_5_pow(85000).as_tuple().digits[:-1] + (6,), 0)))
        == REASON_VOLUME_STEP_MISMATCH
    )
    caps = _wide_caps(volume_step=five_60k, volume_step_anchor=zero)
    assert validate_volume(caps, _exact_5_pow(85000, 7)) is None
    assert (
        validate_volume(caps, D((0, _exact_5_pow(85000, 7).as_tuple().digits[:-1] + (0,), 0)))
        == REASON_VOLUME_STEP_MISMATCH
    )
    assert time.monotonic() - t0 < 60  # generous smoke guard only


def test_300k_digit_shapes_complete() -> None:
    t0 = time.monotonic()
    zero = D(0)
    ten_300k = D("1" + "0" * 300000)
    caps = _wide_caps(volume_step=D(1), volume_step_anchor=zero)
    assert validate_volume(caps, ten_300k) is None
    caps = _wide_caps(volume_step=D("1" + "0" * 150000), volume_step_anchor=zero)
    assert validate_volume(caps, ten_300k) is None
    # 5^k with k so that the coefficient has ~300,000 digits
    step5 = _exact_5_pow(430000)
    assert len(step5.as_tuple().digits) > 300000
    _, digits, _ = step5.as_tuple()
    ten_times = D((0, digits, 1))  # exactly 10 * step: a multiple
    tenth = D((0, digits, -1))  # exactly step / 10: not a multiple
    caps = _wide_caps(volume_step=step5, volume_step_anchor=zero)
    assert validate_volume(caps, ten_times) is None
    assert validate_volume(caps, tenth) == REASON_VOLUME_STEP_MISMATCH
    assert time.monotonic() - t0 < 120  # generous smoke guard only


def test_prime_power_helpers_match_naive() -> None:
    from nexora.autonomous import broker_capabilities as bc

    rng = random.Random(7)
    for _ in range(3000):
        v5 = rng.randint(0, 40)
        v2 = rng.randint(0, 40)
        rest = rng.choice([1, 3, 7, 11, 13, 101, 99991])
        n = 5**v5 * 2**v2 * rest
        assert bc._strip_prime(n, 5) == (v5, n // 5**v5)
        for need in range(-2, 45):
            assert bc._divisible_by_prime_power(n, 5, need) == (v5 >= need)
            assert bc._divisible_by_prime_power(-n, 2, need) == (v2 >= need)
