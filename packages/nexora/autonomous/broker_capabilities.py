"""BrokerCapabilities: broker-agnostic capability model (ADR-033 section 14).

A sibling contract to ADR-025's InstrumentDefinition/FeedBinding, not a merge into
them. No broker name, symbol, lot, digits, stops or filling-mode assumption is
hard-coded anywhere in this module — every value is adapter-supplied data. Pure
contract shape only; no CapabilityProvider implementation, no discovery, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexora.market_data.instruments import FeedBinding, InstrumentDefinition

# Normalized volume reason codes (ADR-035 s4.5). Kept equal to the strings already
# used by execution/broker_adapter.py; a test pins the equality.
REASON_VOLUME_BELOW_MIN = "volume_below_min"
REASON_VOLUME_ABOVE_MAX = "volume_above_max"
REASON_VOLUME_STEP_MISMATCH = "volume_not_multiple_of_step"


@dataclass(frozen=True, slots=True, kw_only=True)
class BrokerCapabilities:
    """ADR-033 section 14. ``contract_size`` is read from ``instrument``, never
    duplicated as its own field, so it cannot drift from the bound InstrumentDefinition.
    """

    binding: FeedBinding
    instrument: InstrumentDefinition
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    stops_level: Decimal
    freeze_level: Decimal
    filling_modes: tuple[str, ...]
    execution_mode: str
    session_policy_ref: str
    spread_policy_ref: str
    margin_policy_ref: str
    observed_at: datetime
    # ADR-035 s4.5: None => the volume grid is anchored at volume_min.
    volume_step_anchor: Decimal | None = None

    def __post_init__(self) -> None:
        if self.volume_step_anchor is not None and not self.volume_step_anchor.is_finite():
            raise ValueError("invalid_volume_step_anchor")
        if self.instrument.instrument_id != self.binding.instrument_id:
            raise ValueError("broker_capabilities_instrument_binding_mismatch")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("broker_capabilities_observed_at_requires_timezone")
        for field_name, value in (
            ("volume_min", self.volume_min),
            ("volume_max", self.volume_max),
            ("volume_step", self.volume_step),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"invalid_{field_name}")
        if self.volume_max < self.volume_min:
            raise ValueError("volume_max_below_volume_min")
        for field_name, value in (
            ("stops_level", self.stops_level),
            ("freeze_level", self.freeze_level),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(f"invalid_{field_name}")
        if not self.filling_modes:
            raise ValueError("missing_filling_modes")
        for field_name, text in (
            ("execution_mode", self.execution_mode),
            ("session_policy_ref", self.session_policy_ref),
            ("spread_policy_ref", self.spread_policy_ref),
            ("margin_policy_ref", self.margin_policy_ref),
        ):
            if not text.strip():
                raise ValueError(f"missing_{field_name}")

    @property
    def contract_size(self) -> Decimal:
        return self.instrument.trade_contract_size


def _digits_to_int(digits: tuple[int, ...]) -> int:
    """Digit tuple -> int by divide and conquer (no ``int(str)``: that is subject to
    the interpreter's decimal-string length limit)."""

    if len(digits) <= 18:
        value = 0
        for digit in digits:
            value = value * 10 + digit
        return value
    half = len(digits) // 2
    tail = digits[half:]
    scale: int = 10 ** len(tail)
    return _digits_to_int(digits[:half]) * scale + _digits_to_int(tail)


def _signed_coefficient(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)  # finite Decimal
    coefficient = _digits_to_int(digits)
    return (-coefficient if sign else coefficient), exponent


def _two_adic_valuation(value: int) -> int:
    """Exponent of 2 in a non-zero integer (O(size) bit trick)."""

    value = abs(value)
    return (value & -value).bit_length() - 1


def _strip_prime(value: int, prime: int) -> tuple[int, int]:
    """Return ``(v, value / prime**v)`` for a positive integer, ``v`` the exponent of
    ``prime`` in ``value``.

    Doubling search then greedy descent over the binary digits of ``v`` (instead of
    dividing by ``prime`` ``v`` times): ``prime**(2**j)`` is built by repeated
    squaring while it still divides ``value``; if the largest dividing power is
    ``2**(k-1)`` and ``2**k`` does not divide, then ``0 <= v < 2**k`` and testing the
    powers from large to small recovers the bits of ``v`` exactly. About ``2k`` big
    divisions with ``k = O(log v)``, instead of ``v`` divisions.
    """

    powers: list[tuple[int, int]] = [(prime, 1)]
    while value % powers[-1][0] == 0:
        power, exponent = powers[-1]
        powers.append((power * power, exponent * 2))
    count = 0
    for power, exponent in reversed(powers[:-1]):
        quotient, remainder = divmod(value, power)
        if remainder == 0:
            value = quotient
            count += exponent
    return count, value


def _divisible_by_prime_power(value: int, prime: int, need: int) -> bool:
    """``prime**need`` divides the non-zero integer ``value`` (``need`` may be any int).

    Only the comparison is computed, never the full valuation. ``need <= 0`` is
    trivially true. If ``2 * need >= bit_length(value)`` then ``5**need >= 4**need
    >= 2**bit_length(value) > |value|``, so it cannot divide a non-zero value. Else
    ``prime**need`` has at most about ``log2(prime) * bit_length / 2`` bits, one
    big modulo.
    """

    if need <= 0:
        return True
    value = abs(value)
    if prime == 2:
        return bool(_two_adic_valuation(value) >= need)
    if 2 * need >= value.bit_length():
        return False
    return bool(value % prime**need == 0)


def _on_step_grid(quantity: Decimal, anchor: Decimal, step: Decimal) -> bool:
    """Exact test that ``(quantity - anchor) / step`` is an integer, for any finite
    Decimals, independent of the active decimal context.

    Algorithm. Write each operand as ``c * 10**e`` (integer ``c``, ``as_tuple``).
    A zero operand takes the other operand's exponent (its exponent is irrelevant).
    With ``e0`` the smaller exponent and ``g`` the exponent gap, the difference is
    ``D = 10**e0 * T`` where ``T = x * 10**g - y`` (x the coefficient with the larger
    exponent, y the other; the overall sign of T is irrelevant). The step is
    ``s = cs * 10**es`` with ``cs = 2**a2 * 5**a5 * m``, ``gcd(m, 10) == 1``, so
    ``s = m * 2**A * 5**B`` with ``A = a2 + es``, ``B = a5 + es``. Then
    ``D / s = T * 2**(e0-A) * 5**(e0-B) / m`` is an integer iff (1) ``m | T``,
    (2) ``v2(T) >= A - e0`` and (3) ``v5(T) >= B - e0`` (or T == 0).

    Avoiding ``10**g``: if ``g > bit_length(|y|)`` then ``v2(x*10**g) >= g`` and
    ``v5(x*10**g) >= g`` strictly exceed ``v2(y)``/``v5(y)`` (each at most
    ``log2|y|``), so ``v2(T) = v2(y)``, ``v5(T) = v5(y)`` (the valuation of a sum with
    distinct valuations is the minimum) and ``T mod m`` is
    ``(x * pow(10, g, m) - y) mod m`` by modular exponentiation. Otherwise
    ``g <= bit_length(|y|)`` and T is computed exactly; ``10**g`` is then no larger
    than the operand representation itself.

    Only the COMPARISONS ``v2(T) >= need2`` / ``v5(T) >= need5`` are evaluated
    (``_divisible_by_prime_power``): trivially true when the need is <= 0, false when
    ``prime**need`` would exceed ``|T|`` (a mathematical bound, not a cap), else one
    modulo by a power no larger than about the operand. The step's own ``a5`` and ``m``
    come from ``_strip_prime`` (doubling + descent, ``O(log a5)`` big divisions).

    Cost (honest statement): the exponents are only added, compared and passed to
    ``pow`` as Python ints (``O(log g)`` modular multiplications), so exponents of any
    magnitude are fine; the remaining work is a bounded number (``O(log)`` of the
    operand size) of CPython big-integer multiplications/divisions on integers of the
    operands' own size. Those are not linear: CPython division is quadratic in the
    worst case on very large operands (subquadratic only on recent versions), but there
    is no per-factor repeated-division loop and no arbitrary cap, timeout or
    iteration limit.
    """

    cq, eq = _signed_coefficient(quantity)
    ca, ea = _signed_coefficient(anchor)
    cs, es = _signed_coefficient(step)
    if cs == 0:
        return False
    if cq == 0 and ca == 0:
        return True
    if cq == 0:
        eq = ea
    elif ca == 0:
        ea = eq
    if eq >= ea:
        x, y, e0, gap = cq, ca, ea, eq - ea
    else:
        x, y, e0, gap = ca, cq, eq, ea - eq

    a2 = _two_adic_valuation(cs)
    a5, m = _strip_prime(abs(cs) >> a2, 5)
    need2 = a2 + es - e0
    need5 = a5 + es - e0

    if gap > abs(y).bit_length():
        # y != 0 here: a zero operand was given the other's exponent (gap == 0).
        if (x % m * pow(10, gap, m) - y) % m != 0:
            return False
        valuation_operand = y
    else:
        t = x * 10**gap - y
        if t == 0:
            return True
        if t % m != 0:
            return False
        valuation_operand = t
    return _divisible_by_prime_power(valuation_operand, 2, need2) and _divisible_by_prime_power(
        valuation_operand, 5, need5
    )


def validate_volume(capabilities: BrokerCapabilities, quantity: Decimal) -> str | None:
    """Shared pure, TOTAL volume validation (ADR-035 s4.5). Returns a reason code,
    or ``None`` when valid. Never raises.

    Valid iff ``volume_min <= q <= volume_max`` and ``(q - anchor) % volume_step
    == 0`` where ``anchor`` is ``volume_step_anchor`` if declared, else
    ``volume_min``. The step test is exact integer arithmetic on the Decimal
    coefficients/exponents (see ``_on_step_grid``), independent of the decimal
    context and of the operands' representation. A non-finite quantity,
    a non-Decimal quantity (including ``bool``), a non-``BrokerCapabilities``
    object, or any unexpected failure is rejected fail-closed with the
    step-mismatch code.
    """

    try:
        if not isinstance(capabilities, BrokerCapabilities):
            return REASON_VOLUME_STEP_MISMATCH
        if not isinstance(quantity, Decimal) or not quantity.is_finite():
            return REASON_VOLUME_STEP_MISMATCH
        if quantity < capabilities.volume_min:
            return REASON_VOLUME_BELOW_MIN
        if quantity > capabilities.volume_max:
            return REASON_VOLUME_ABOVE_MAX
        anchor = (
            capabilities.volume_step_anchor
            if capabilities.volume_step_anchor is not None
            else capabilities.volume_min
        )
        if not _on_step_grid(quantity, anchor, capabilities.volume_step):
            return REASON_VOLUME_STEP_MISMATCH
        return None
    except Exception:
        return REASON_VOLUME_STEP_MISMATCH
