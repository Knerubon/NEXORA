"""PR-7 hardening delta: seam replacement, exact residual arithmetic, exact Decimal types.

Every regression below FAILS on 52702fa (the reviewed head) and passes after the delta.
Production stays DENY-ONLY; SIMULATION is the only executable mode."""

from __future__ import annotations

import copy
import dataclasses
import pickle
import random
import time
from decimal import Decimal
from fractions import Fraction
from typing import Any
from unittest import mock

import pytest
from nexora.autonomous.broker_capabilities import (
    REASON_VOLUME_BELOW_MIN,
    REASON_VOLUME_STEP_MISMATCH,
    BrokerCapabilities,
    validate_residual_volume,
)
from nexora.autonomous_contracts import TradeIntentKind
from nexora.execution.broker_adapter import (
    PHASE1_EXECUTABLE_ADAPTER_MODES,
    SIMULATION_MODE,
    SimulatedBrokerAdapter,
)
from nexora.execution.dedup_store import InMemoryExecutionDedupStore
from nexora.execution.models import ExecutionRequest
from nexora.execution.pipeline import (
    ExecutionPipeline,
    NonProductionTransmissionSeam,
    PipelineStatus,
    PipelineWiringError,
    non_production_transmission_seam,
)
from nexora.execution.preflight import (
    REASON_REDUCE_RESIDUAL_VOLUME_INVALID,
    ExecutionPreflight,
)

from tests.execution_resolver_fixtures import TableResolverForTestsOnly
from tests.test_execution_pipeline import (
    BOUND,
    NOW,
    AllowPreflightForTestsOnly,
    RecordingStore,
    _caps,
    _inputs,
)
from tests.test_execution_preflight import _request

WRITES = ("claim", "record_attempt", "record_abort", "record_result", "release_for_retry")


# =========================================================================== 1. seam replacement


class EvilAdapter:
    """Unmarked, mode 'real'; records any submit."""

    def __init__(self) -> None:
        self.submits = 0

    @property
    def adapter_mode(self) -> str:
        return "real"

    def capabilities(self) -> BrokerCapabilities:
        return _caps()

    def submit(self, request: ExecutionRequest) -> Any:
        self.submits += 1
        raise AssertionError("EVIL SUBMIT REACHED")


class MarkedOtherCaps:
    NEXORA_NON_PRODUCTION_TEST_ADAPTER = True

    @property
    def adapter_mode(self) -> str:
        return SIMULATION_MODE

    def capabilities(self) -> BrokerCapabilities:
        return dataclasses.replace(_caps(), volume_max=Decimal("3"))

    def submit(self, request: ExecutionRequest) -> Any:
        raise AssertionError


def _sim_seam() -> NonProductionTransmissionSeam:
    return non_production_transmission_seam(
        SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    )


def _pipeline(seam: Any) -> ExecutionPipeline:
    store = InMemoryExecutionDedupStore()
    store.establish_index_genesis()
    return ExecutionPipeline(
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver=TableResolverForTestsOnly({"SYM": "inst-1"}),
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        max_capabilities_age=BOUND,
        transmission=seam,
    )


def test_replace_adapter_with_evil_fails_closed_and_never_submits() -> None:
    seam = _sim_seam()
    evil = EvilAdapter()
    with pytest.raises(PipelineWiringError):
        dataclasses.replace(seam, adapter=evil, preflight=AllowPreflightForTestsOnly([]))
    assert evil.submits == 0


def test_replace_of_captured_fields_fails_closed() -> None:
    seam = _sim_seam()
    for changes in (
        {"adapter_mode": "real"},
        {"adapter_mode": "paper"},
        {"adapter_mode": "SIMULATION"},
        {"capabilities": dataclasses.replace(_caps(), volume_max=Decimal("1"))},
        {"adapter": MarkedOtherCaps()},  # permitted class, but captured caps are not its own
        {"adapter": EvilAdapter(), "adapter_mode": "simulation"},
    ):
        with pytest.raises(PipelineWiringError):
            dataclasses.replace(seam, **changes)


def test_replaced_seam_never_carries_the_factory_token() -> None:
    seam = _sim_seam()
    replaced = dataclasses.replace(seam, preflight=AllowPreflightForTestsOnly([]))
    with pytest.raises(PipelineWiringError) as err:
        _pipeline(replaced)
    assert err.value.code == "transmission_wiring_not_permitted"
    assert _pipeline(seam)._transmission is seam  # the minted seam still works


def test_direct_construction_and_subclass_of_seam_are_refused() -> None:
    seam = _sim_seam()
    direct = NonProductionTransmissionSeam(
        adapter=seam.adapter,
        preflight=seam.preflight,
        adapter_mode=seam.adapter_mode,
        capabilities=seam.capabilities,
    )
    with pytest.raises(PipelineWiringError):
        _pipeline(direct)
    assert "_token" not in {f.name for f in dataclasses.fields(seam) if f.init}

    class Sub(NonProductionTransmissionSeam):
        pass

    sub = Sub(
        adapter=seam.adapter,
        preflight=seam.preflight,
        adapter_mode=seam.adapter_mode,
        capabilities=seam.capabilities,
    )
    object.__setattr__(sub, "_token", seam._token)  # even with the token copied
    with pytest.raises(PipelineWiringError):
        _pipeline(sub)


def test_copy_deepcopy_and_pickle_cannot_mint_a_valid_seam_for_another_adapter() -> None:
    seam = _sim_seam()
    shallow = copy.copy(seam)
    assert shallow.adapter is seam.adapter  # same adapter only; nothing to swap (frozen)
    with pytest.raises(dataclasses.FrozenInstanceError):
        shallow.adapter = EvilAdapter()  # type: ignore[misc]
    with pytest.raises(PipelineWiringError):
        _pipeline(copy.deepcopy(seam))  # token is a fresh object
    with pytest.raises(PipelineWiringError):
        _pipeline(pickle.loads(pickle.dumps(seam)))  # noqa: S301 - own test object


def test_ctor_second_layer_refuses_a_seam_forged_around_post_init() -> None:
    seam = _sim_seam()
    forged = object.__new__(NonProductionTransmissionSeam)
    evil = EvilAdapter()
    for name, value in (
        ("adapter", evil),
        ("preflight", AllowPreflightForTestsOnly([])),
        ("adapter_mode", "simulation"),
        ("capabilities", seam.capabilities),
        ("_token", seam._token),
    ):
        object.__setattr__(forged, name, value)
    with pytest.raises(PipelineWiringError):
        _pipeline(forged)
    assert evil.submits == 0


def test_phase1_vocabulary_closed_and_production_deny_only_unchanged() -> None:
    assert PHASE1_EXECUTABLE_ADAPTER_MODES == frozenset({"simulation"})
    log: list[str] = []
    inner = InMemoryExecutionDedupStore()
    inner.establish_index_genesis()
    store = RecordingStore(inner, log)
    simulator = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    wired = ExecutionPipeline(
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver=TableResolverForTestsOnly({"SYM": "inst-1"}),
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        max_capabilities_age=BOUND,
        transmission=non_production_transmission_seam(simulator),  # real preflight
    )
    for kind in (
        TradeIntentKind.OPEN,
        TradeIntentKind.REDUCE,
        TradeIntentKind.CLOSE,
        TradeIntentKind.MODIFY_PROTECTION,
    ):
        with mock.patch.object(SimulatedBrokerAdapter, "submit", side_effect=AssertionError):
            outcome = wired.run(_inputs(kind))
        assert outcome.status is PipelineStatus.DENIED
    assert not any(call in WRITES for call in log) and simulator.simulated_fill_count == 0


# =========================================================================== 2. exact arithmetic

D = Decimal


def _dec(coefficient: int, exponent: int) -> Decimal:
    """Exact Decimal ``coefficient * 10**exponent`` (no context rounding, any exponent)."""

    return Decimal((0, tuple(int(ch) for ch in str(coefficient)), exponent))


def _caps_with(**kw: Any) -> BrokerCapabilities:
    base: dict[str, Any] = {"volume_max": D("1E+9999999")}
    base.update(kw)
    return dataclasses.replace(_caps(), **base)


def test_huge_exponent_gap_is_judged_exactly_not_capped() -> None:
    caps = _caps()
    # pos 1E+999999, reduce 1E+999998: residual 9E+999998 is on the 0.05 grid anchored at 0.10
    assert validate_residual_volume(caps, D("1E+999999"), D("1E+999998")) is None
    # same shape on a 0.03 step anchored at 0.09: valid; on 0.07 step it is not
    c3 = dataclasses.replace(_caps(), volume_min=D("0.09"), volume_step=D("0.03"))
    assert validate_residual_volume(c3, D("1E+999999"), D("1E+999998")) is None
    c7 = dataclasses.replace(_caps(), volume_min=D("0.07"), volume_step=D("0.07"))
    assert validate_residual_volume(c7, D("1E+999999"), D("1E+999998")) == (
        REASON_VOLUME_STEP_MISMATCH
    )
    for n in (10**6, 10**9, 10**12, 10**15, 10**17):
        assert validate_residual_volume(caps, D(f"1E+{n}"), D(f"1E+{n - 1}")) is None
        assert validate_residual_volume(caps, D(f"1E+{n}"), D(f"1E+{n}")) is not None
        assert validate_residual_volume(caps, D(f"2E+{n}"), D(f"1E+{n}")) is None
        # negative / mixed exponents (reduce tiny, position gigantic)
        assert validate_residual_volume(caps, D(f"1E+{n}"), D("0.5")) is None
        assert validate_residual_volume(caps, D(f"1E-{n}"), D(f"1E-{n + 5}")) is not None
        assert validate_residual_volume(caps, D("1"), D(f"1E-{n}")) is not None


def _expected_huge(a: int, n: int, b: int, m: int, k: int, q: int, s: int, p: int) -> bool:
    """Independent modular oracle: pos=a*10^n, red=b*10^m, anchor=min=k/10^q, step=s/10^p.
    (r-anchor)/step = A*10^(p-q)/s with A = a*10^(n+q) - b*10^(m+q) - k; no gap materialised."""

    modulus = s * 10 ** max(q - p, 0)
    a_mod = (a * pow(10, n + q, modulus) - b * pow(10, m + q, modulus) - k) % modulus
    return bool((a_mod * 10 ** max(p - q, 0)) % modulus == 0)


def test_huge_gap_shapes_match_an_independent_modular_oracle() -> None:
    rng = random.Random(11)
    for _ in range(400):
        n = rng.choice([10**3, 10**6, 10**9, 10**12, 10**15, rng.randint(10**3, 10**13)])
        m = rng.choice([n - 1, n - 2, n // 2, 0, -1, -2, rng.randint(-2, n - 1)])
        a, b = rng.randint(1, 9), rng.randint(1, 9)
        q, p = 2, rng.choice([0, 1, 2, 3])
        k = rng.choice([10, 20, 5, 7, 100])  # anchor == min == k/100
        s = rng.choice([1, 2, 3, 5, 7, 25, 125, 6, 15, 49])
        caps = dataclasses.replace(
            _caps(),
            volume_min=_dec(k, -q),
            volume_step=_dec(s, -p),
            volume_max=D("1E+99"),
        )
        got = validate_residual_volume(caps, _dec(a, n), _dec(b, m))
        if n == m:
            continue
        assert (got is None) is _expected_huge(a, n, b, m, k, q, s, p), (a, n, b, m, k, s, p)


def test_huge_gap_cost_is_bounded_smoke() -> None:
    caps = _caps()
    start = time.perf_counter()
    for n in (10**12, 10**15, 10**17):
        validate_residual_volume(caps, D(f"1E+{n}"), D(f"1E+{n - 1}"))
        validate_residual_volume(caps, D(f"1E-{n}"), D(f"1E-{n + 1}"))
    assert time.perf_counter() - start < 5.0  # generous; real cost is microseconds


def _shape(rng: random.Random) -> Decimal:
    kind = rng.randint(0, 5)
    exp = rng.randint(-12, 12)
    if kind == 0:
        coefficient = rng.randint(1, 9999)
    elif kind == 1:
        coefficient = 2 ** rng.randint(0, 60)
    elif kind == 2:
        coefficient = 5 ** rng.randint(0, 40)
    elif kind == 3:
        coefficient = 10 ** rng.randint(0, 30) * rng.randint(1, 99)
    elif kind == 4:
        coefficient = 2 ** rng.randint(0, 20) * 5 ** rng.randint(0, 15) * rng.randint(1, 7)
    else:
        coefficient = rng.randint(1, 10**30)
    return _dec(coefficient, exp)


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def test_residual_matches_independent_fraction_oracle_on_30k_cases() -> None:
    rng = random.Random(20261005)
    mismatches: list[tuple[Any, ...]] = []
    for _ in range(30000):
        vmin, step, pos, red = _shape(rng), _shape(rng), _shape(rng), _shape(rng)
        anchor = _shape(rng) if rng.random() < 0.4 else None
        caps = dataclasses.replace(
            _caps(),
            volume_min=vmin,
            volume_step=step,
            volume_step_anchor=anchor,
            volume_max=D("1E+99"),
        )
        anchor_f = _fraction(anchor if anchor is not None else vmin)
        residual = _fraction(pos) - _fraction(red)
        expected = (
            residual > 0
            and residual >= _fraction(vmin)
            and ((residual - anchor_f) / _fraction(step)).denominator == 1
        )
        got = validate_residual_volume(caps, pos, red)
        if (got is None) is not expected:
            mismatches.append((pos, red, vmin, step, anchor, got, expected))
    assert mismatches == []


def test_near_cancellation_and_dominance_shapes_against_fraction() -> None:
    rng = random.Random(5)
    mismatches = []
    for _ in range(6000):
        base = Fraction(rng.randint(1, 10**6), 10 ** rng.randint(0, 8))
        delta = Fraction(rng.randint(0, 50), 10 ** rng.randint(0, 12))
        vmin = Fraction(rng.randint(1, 30), 10 ** rng.randint(0, 3))
        pos_f, red_f = base + delta, base
        # the residual is delta: below / equal / above the minimum, on and off the grid
        pos = D(pos_f.numerator) / D(pos_f.denominator)
        red = D(red_f.numerator) / D(red_f.denominator)
        if Fraction(pos) != pos_f or Fraction(red) != red_f:
            continue  # not exactly representable in default context: skip
        step = Fraction(rng.choice([1, 5, 25, 3]), 10 ** rng.randint(1, 3))
        caps = dataclasses.replace(
            _caps(),
            volume_min=D(vmin.numerator) / D(vmin.denominator),
            volume_step=D(step.numerator) / D(step.denominator),
            volume_max=D("1E+99"),
        )
        residual = pos_f - red_f
        expected = residual > 0 and residual >= vmin and ((residual - vmin) / step).denominator == 1
        if (validate_residual_volume(caps, pos, red) is None) is not expected:
            mismatches.append((pos, red, vmin, step))
    assert mismatches == []


def test_totality_never_raises_on_any_decimal_input() -> None:
    caps = _caps()
    values: list[Any] = [
        D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"), D(0), D("-0"), D("-1"),
        D("1E+999999999999999"), D("1E-999999999999999"), D("9" * 20000), D("1E+5000"),
        D("0.5"), D(1), None, 1.5, "1", True, object(),
    ]  # fmt: skip
    for position in values:
        for reduce in values:
            out = validate_residual_volume(caps, position, reduce)
            assert out is None or out in (REASON_VOLUME_BELOW_MIN, REASON_VOLUME_STEP_MISMATCH)
    # the preflight uses the single existing reason, never an exception
    decision = ExecutionPreflight().evaluate(
        _request(TradeIntentKind.REDUCE, quantity=D("0.5")),
        caps,
        None,
        now=NOW,
        max_capabilities_age=BOUND,
        position_quantity=D("sNaN"),
    )
    assert REASON_REDUCE_RESIDUAL_VOLUME_INVALID in decision.reason_codes
    assert decision.allowed is False


def test_cap_language_and_helpers_are_gone() -> None:
    import nexora.autonomous.broker_capabilities as module

    assert not hasattr(module, "_MAX_EXACT_SUM_DIGITS") and not hasattr(module, "_exact_sum")


# =========================================================================== 3. exact Decimal types


class LyingDecimal(Decimal):
    """Subclass whose comparisons lie once armed."""

    armed = False

    def _lie(self, other: Any) -> bool:
        return True

    def __lt__(self, other: Any) -> bool:
        return False if LyingDecimal.armed else super().__lt__(other)

    def __le__(self, other: Any) -> bool:
        return False if LyingDecimal.armed else super().__le__(other)

    def __gt__(self, other: Any) -> bool:
        return True if LyingDecimal.armed else super().__gt__(other)

    def __ge__(self, other: Any) -> bool:
        return True if LyingDecimal.armed else super().__ge__(other)

    def is_finite(self) -> bool:
        return True if LyingDecimal.armed else super().is_finite()


@pytest.mark.parametrize("armed", [False, True])
@pytest.mark.parametrize(
    "slot", ["position", "reduce", "volume_min", "volume_step", "volume_step_anchor"]
)
def test_decimal_subclasses_fail_closed_at_every_operand(slot: str, armed: bool) -> None:
    values = {
        "position": D("1.00"),
        "reduce": D("0.50"),
        "volume_min": D("0.10"),
        "volume_step": D("0.05"),
        "volume_step_anchor": D("0.10"),
    }
    assert (
        validate_residual_volume(
            dataclasses.replace(_caps(), volume_step_anchor=D("0.10")),
            values["position"],
            values["reduce"],
        )
        is None
    )
    values[slot] = LyingDecimal(values[slot])
    caps = dataclasses.replace(
        _caps(),
        volume_min=values["volume_min"],
        volume_step=values["volume_step"],
        volume_step_anchor=values["volume_step_anchor"],
    )
    LyingDecimal.armed = armed  # armed only AFTER construction (its validators are not under test)
    try:
        out = validate_residual_volume(caps, values["position"], values["reduce"])
    finally:
        LyingDecimal.armed = False
    assert out == REASON_VOLUME_STEP_MISMATCH  # the existing single fail-closed reason


def test_decimal_subclass_position_denied_through_preflight() -> None:
    decision = ExecutionPreflight().evaluate(
        _request(TradeIntentKind.REDUCE, quantity=D("0.50")),
        _caps(),
        None,
        now=NOW,
        max_capabilities_age=BOUND,
        position_quantity=LyingDecimal("1.00"),
    )
    assert REASON_REDUCE_RESIDUAL_VOLUME_INVALID in decision.reason_codes
    assert decision.allowed is False
