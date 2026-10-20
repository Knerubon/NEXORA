"""Regression tests for PR #79 review findings D-1 and D-2. SYNTHETIC fixtures only.

D-1: an unsupported `ambiguity` value silently selected the favourable target outcome.
D-2: cost/net arithmetic depended on the caller's ambient Decimal context while the run id did
     not, so one run id could describe different financial output.

Nothing here is market data or evidence of profitability.
"""

from __future__ import annotations

import decimal
import threading
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest
from nexora.artifacts import canonical_hash
from nexora.edge import arithmetic
from nexora.edge.arithmetic import arithmetic_descriptor, is_deterministic
from nexora.edge.costs import (
    KNOWN_ZERO,
    CostComponent,
    CostModel,
    compute_costs,
)
from nexora.edge.exits import (
    AMBIGUITY_POLICIES,
    CONCURRENCY_POLICIES,
    EdgeExitRun,
    ExitInputError,
    ExitPolicy,
    ExitResult,
    TradePlan,
    run_exit_mode,
    simulate_exit,
    to_edge_trade,
)

from tests.edge_fixtures import BAR, T0, make_bar, registry_args, sim, synthetic_manifest

# --------------------------------------------------------------------------------------
# D-1: ambiguity validation
# --------------------------------------------------------------------------------------


class _AlwaysEqualStr(str):
    """A str subclass that claims to equal anything: must not pass a membership check."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash("stop_first")


def _dual_hit_bars() -> tuple[Any, ...]:
    # open 100, high 110, low 90: touches BOTH stop 95 and target 105 in one bar.
    return (
        make_bar(0, "100", "110", "90", "100", bid="100", ask="101"),
        make_bar(1, "100", "100", "100", "100", bid="100", ask="101"),
    )


def _long_plan() -> TradePlan:
    return TradePlan("fixture-only", "long", T0, Decimal("95"), Decimal("105"))


def _policy(**overrides: Any) -> ExitPolicy:
    base: dict[str, Any] = {
        "version": "fixture",
        "concurrency": "one_position",
        "bar_interval": BAR,
    }
    return ExitPolicy(**{**base, **overrides})


def test_vocabularies_are_the_two_documented_values() -> None:
    assert AMBIGUITY_POLICIES == ("stop_first", "target_first")
    assert CONCURRENCY_POLICIES == ("one_position", "allow_overlap")


@pytest.mark.parametrize(
    "bad",
    [
        "typo",
        "",
        " stop_first",
        "stop_first ",
        "STOP_FIRST",
        "Stop_First",
        "stop-first",
        "stop_first\n",
        "target first",
        None,
        0,
        1,
        True,
        False,
        b"stop_first",
        ("stop_first",),
        ["stop_first"],
        object(),
        _AlwaysEqualStr("anything"),
        _AlwaysEqualStr("stop_first"),
    ],
)
def test_unsupported_ambiguity_is_rejected_at_construction(bad: Any) -> None:
    with pytest.raises(ExitInputError) as excinfo:
        _policy(ambiguity=bad)
    assert excinfo.value.code == "invalid_ambiguity"


@pytest.mark.parametrize(
    "bad",
    ["", "ONE_POSITION", "one_position ", None, 0, b"one_position", _AlwaysEqualStr("x")],
)
def test_unsupported_concurrency_is_rejected_at_construction(bad: Any) -> None:
    with pytest.raises(ExitInputError) as excinfo:
        _policy(concurrency=bad)
    assert excinfo.value.code == "invalid_concurrency"


def test_valid_ambiguity_values_keep_their_approved_semantics() -> None:
    plan, bars = _long_plan(), _dual_hit_bars()
    stop_first = sim(plan, bars, _policy(ambiguity="stop_first"))
    target_first = sim(plan, bars, _policy(ambiguity="target_first"))
    assert (stop_first.exit_price, stop_first.exit_reason) == (Decimal("95"), "stop")
    assert (target_first.exit_price, target_first.exit_reason) == (Decimal("105"), "target")
    assert stop_first.ambiguous and target_first.ambiguous
    expected_alternatives = (("stop_first", Decimal("95")), ("target_first", Decimal("105")))
    assert stop_first.alternative_exit_prices == expected_alternatives
    assert target_first.alternative_exit_prices == expected_alternatives


def test_default_ambiguity_is_still_stop_first() -> None:
    # Preserves the existing default; this fix must not change execution semantics.
    assert _policy().ambiguity == "stop_first"
    result = sim(_long_plan(), _dual_hit_bars(), _policy())
    assert result.exit_reason == "stop"


@pytest.mark.parametrize("bad", ["typo", None, 0, _AlwaysEqualStr("stop_first")])
def test_bypassed_policy_still_cannot_select_an_outcome(bad: Any) -> None:
    """A frozen dataclass can be mutated by bypassing __setattr__. The point of use must still
    refuse instead of falling through to the favourable (target) outcome."""
    policy = _policy()
    object.__setattr__(policy, "ambiguity", bad)
    with pytest.raises(ExitInputError) as excinfo:
        simulate_exit(_long_plan(), _dual_hit_bars(), policy, ohlc_basis="bid")
    assert excinfo.value.code == "invalid_ambiguity"


def test_bypassed_concurrency_is_refused_at_the_point_of_use() -> None:
    policy = _policy()
    object.__setattr__(policy, "concurrency", "typo")
    with pytest.raises(ExitInputError) as excinfo:
        simulate_exit(_long_plan(), _dual_hit_bars(), policy, ohlc_basis="bid")
    assert excinfo.value.code == "invalid_concurrency"


def test_single_hit_bars_do_not_depend_on_ambiguity() -> None:
    # Boundary: when only one level is touched nothing is ambiguous and both policies agree.
    stop_only = (make_bar(0, "100", "104", "90", "100", bid="100", ask="101"),)
    target_only = (make_bar(0, "100", "110", "96", "100", bid="100", ask="101"),)
    for bars, reason, price in ((stop_only, "stop", "95"), (target_only, "target", "105")):
        a = sim(_long_plan(), bars, _policy(ambiguity="stop_first"))
        b = sim(_long_plan(), bars, _policy(ambiguity="target_first"))
        assert (a.exit_reason, a.exit_price, a.ambiguous) == (reason, Decimal(price), False)
        assert (a.exit_reason, a.exit_price) == (b.exit_reason, b.exit_price)


def test_ambiguity_policy_changes_the_hash_payload() -> None:
    assert (
        _policy(ambiguity="stop_first").hash_payload()
        != _policy(ambiguity="target_first").hash_payload()
    )


# --------------------------------------------------------------------------------------
# D-2: Decimal determinism
# --------------------------------------------------------------------------------------

_COST = Decimal("0.123456789")
_SWAP = Decimal("0.3333333333333333333333333333333")  # needs more than 28 significant digits


def _cost_model(commission: Decimal = _COST, swap: CostComponent = KNOWN_ZERO) -> CostModel:
    return CostModel(
        "fixture",
        "SYNTH-UNIT",
        KNOWN_ZERO,
        CostComponent("known", commission),
        KNOWN_ZERO,
        swap,
    )


def _run(cost_model: CostModel | None = None) -> EdgeExitRun:
    bars = (make_bar(0, "100", "105", "99", "105", bid="100", ask="101"),)
    manifest, manifest_hash = synthetic_manifest(bars)
    return run_exit_mode(
        (_long_plan(),),
        bars,
        manifest=manifest,
        expected_manifest_hash=manifest_hash,
        policy=_policy(),
        cost_model=cost_model or _cost_model(),
        unit_size=Decimal("1"),
        **registry_args(),
    )


def _fingerprint(run: EdgeExitRun) -> tuple[Any, ...]:
    trade = run.trades[0]
    return (
        run.run_id,
        trade.gross_pnl,
        trade.costs.known_total,
        trade.costs.known,
        trade.net_pnl_known_costs,
        trade.exit.r_multiple,
        canonical_hash(run.trades),
    )


_ROUNDINGS = [
    decimal.ROUND_UP,
    decimal.ROUND_DOWN,
    decimal.ROUND_CEILING,
    decimal.ROUND_FLOOR,
    decimal.ROUND_HALF_UP,
    decimal.ROUND_HALF_DOWN,
    decimal.ROUND_HALF_EVEN,
    decimal.ROUND_05UP,
]


def test_reference_result_is_exact_at_the_declared_arithmetic() -> None:
    run = _run()
    trade = run.trades[0]
    assert trade.gross_pnl == Decimal("5")
    assert trade.costs.known_total == Decimal("0.123456789")
    assert trade.net_pnl_known_costs == Decimal("4.876543211")


@pytest.mark.parametrize("precision", [1, 2, 3, 6, 15, 28, 50, 120])
def test_result_and_run_id_do_not_depend_on_ambient_precision(precision: int) -> None:
    reference = _fingerprint(_run())
    with localcontext() as ctx:
        ctx.prec = precision
        assert _fingerprint(_run()) == reference


@pytest.mark.parametrize("rounding", _ROUNDINGS)
def test_result_and_run_id_do_not_depend_on_ambient_rounding(rounding: str) -> None:
    reference = _fingerprint(_run())
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = rounding
        assert _fingerprint(_run()) == reference


def test_ambient_trap_settings_cannot_change_or_abort_the_run() -> None:
    reference = _fingerprint(_run())
    with localcontext() as ctx:
        ctx.prec = 4
        ctx.traps[decimal.Inexact] = True
        ctx.traps[decimal.Rounded] = True
        assert _fingerprint(_run()) == reference
    with localcontext() as ctx:
        ctx.traps[decimal.InvalidOperation] = False
        ctx.traps[decimal.DivisionByZero] = False
        ctx.traps[decimal.Overflow] = False
        assert _fingerprint(_run()) == reference


def test_global_context_replacement_is_ignored() -> None:
    reference = _fingerprint(_run())
    saved = decimal.getcontext().copy()
    try:
        decimal.setcontext(decimal.Context(prec=2, rounding=decimal.ROUND_DOWN))
        assert _fingerprint(_run()) == reference
    finally:
        decimal.setcontext(saved)


def test_other_threads_with_other_contexts_get_the_same_result() -> None:
    reference = _fingerprint(_run())
    seen: list[tuple[Any, ...]] = []

    def worker() -> None:
        decimal.setcontext(decimal.Context(prec=5, rounding=decimal.ROUND_CEILING))
        seen.append(_fingerprint(_run()))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert seen == [reference] * 4


def test_inexact_division_in_holding_days_is_context_independent() -> None:
    """Swap cost uses holding_days = seconds / 86400, an inexact division. It is the place
    where precision most visibly changes a result."""
    model = _cost_model(swap=CostComponent("known", _SWAP))
    reference = _fingerprint(_run(model))
    for precision in (1, 6, 28, 80):
        with localcontext() as ctx:
            ctx.prec = precision
            assert _fingerprint(_run(model)) == reference
    # And it really does differ from what a different arithmetic would give.
    with localcontext(decimal.Context(prec=6)):
        different = (Decimal(300) / Decimal(86400)) * _SWAP
    assert different != _run(model).trades[0].costs.known[-1][1]


def test_compute_costs_and_to_edge_trade_are_context_independent_on_their_own() -> None:
    # The public building blocks must be deterministic when called directly, not only through
    # run_exit_mode.
    plan, bars = _long_plan(), (make_bar(0, "100", "105", "99", "105", bid="100", ask="101"),)
    outcome = sim(plan, bars, _policy())
    model = _cost_model(swap=CostComponent("known", _SWAP))
    ref_costs = compute_costs(model, size=Decimal("1"), holding_days=Decimal(300) / Decimal(86400))
    ref_trade = to_edge_trade(outcome, model, unit_size=Decimal("1"))
    ref_gross = outcome.gross_per_unit()
    for precision in (1, 7, 60):
        with localcontext() as ctx:
            ctx.prec = precision
            assert compute_costs(
                model, size=Decimal("1"), holding_days=Decimal(300) / Decimal(86400)
            ) == compute_costs(model, size=Decimal("1"), holding_days=Decimal(300) / Decimal(86400))
            assert to_edge_trade(outcome, model, unit_size=Decimal("1")) == ref_trade
            assert outcome.gross_per_unit() == ref_gross
    assert ref_costs.known_total >= 0


def test_public_arithmetic_entry_points_are_marked_deterministic() -> None:
    for fn in (compute_costs, to_edge_trade, simulate_exit, run_exit_mode):
        assert is_deterministic(fn), fn.__name__
    assert is_deterministic(ExitResult.gross_per_unit)


def test_run_id_changes_when_the_arithmetic_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The original defect: different net output under an identical run id. Now a different
    arithmetic is a different identity AND the computed ledger is part of that identity."""
    baseline = _run()
    coarse = decimal.Context(
        prec=6,
        rounding=decimal.ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
    )
    monkeypatch.setattr(arithmetic, "EDGE_CONTEXT", coarse)
    assert arithmetic_descriptor()[1] == 6
    other = _run()
    assert other.trades[0].net_pnl_known_costs != baseline.trades[0].net_pnl_known_costs
    assert other.run_id != baseline.run_id


def test_run_id_binds_computed_costs_not_only_inputs() -> None:
    a = _run(_cost_model(commission=Decimal("0.10")))
    b = _run(_cost_model(commission=Decimal("0.20")))
    assert a.run_id != b.run_id
    assert a.trades[0].net_pnl_known_costs != b.trades[0].net_pnl_known_costs


def test_identical_inputs_reproduce_the_identical_run_twice() -> None:
    assert _fingerprint(_run()) == _fingerprint(_run())


def test_declared_arithmetic_equals_the_cpython_default_so_behaviour_is_unchanged() -> None:
    default = decimal.DefaultContext
    version, prec, rounding, emin, emax, traps = arithmetic_descriptor()
    assert version == "edge-decimal-context/1"
    assert (prec, rounding, emin, emax) == (
        default.prec,
        str(default.rounding),
        default.Emin,
        default.Emax,
    )
    assert traps == tuple(sorted(s.__name__ for s, enabled in default.traps.items() if enabled))


def test_run_notes_state_the_arithmetic_and_that_it_is_an_open_decision() -> None:
    notes = _run().notes
    assert any(n.startswith("arithmetic=edge-decimal-context/1:prec=28") for n in notes)
    assert "arithmetic_context_is_engineering_default_quant_decision_q_t12_open" in notes


def test_canonical_serialization_is_exponent_insensitive() -> None:
    # Equal values with different exponents must hash identically (stable serialization).
    assert canonical_hash(Decimal("4.87650")) == canonical_hash(Decimal("4.8765"))
    assert canonical_hash(Decimal("0E-10")) == canonical_hash(Decimal("0"))
    assert canonical_hash(timedelta(0).total_seconds()) == canonical_hash(0.0)
