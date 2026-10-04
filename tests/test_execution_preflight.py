"""PR-6: ExecutionPreflight + total validate_volume (ADR-035 s3.5, s4.5)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.autonomous.broker_capabilities import (
    REASON_VOLUME_ABOVE_MAX,
    REASON_VOLUME_BELOW_MIN,
    REASON_VOLUME_STEP_MISMATCH,
    BrokerCapabilities,
    validate_volume,
)
from nexora.autonomous_contracts import TradeIntentKind as K
from nexora.execution import broker_adapter, preflight
from nexora.execution.models import ExecutionRequest, ProtectionRequest
from nexora.execution.preflight import ExecutionPreflight, PreflightDecision
from nexora.market_data.instruments import FeedBinding, InstrumentDefinition, PriceGrid

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BOUND = timedelta(seconds=30)
FRESHNESS_CODES = {
    preflight.REASON_FRESHNESS_BOUND_MISSING,
    preflight.REASON_CAPABILITIES_STALE,
}


def _caps(**overrides: object) -> BrokerCapabilities:
    fields: dict[str, object] = {
        "binding": FeedBinding(
            instrument_id="inst-1",
            broker_id="broker-a",
            symbol="SYM",
            price_grid=PriceGrid(digits=2, point=Decimal("0.01"), trade_tick_size=None),
            time_offset_seconds=0,
        ),
        "instrument": InstrumentDefinition(
            instrument_id="inst-1",
            currency_base="USD",
            currency_profit="USD",
            trade_calc_mode=0,
            trade_contract_size=Decimal("100"),
            chart_mode=0,
        ),
        "volume_min": Decimal("0.10"),
        "volume_max": Decimal("10"),
        "volume_step": Decimal("0.05"),
        "stops_level": Decimal("0"),
        "freeze_level": Decimal("0"),
        "filling_modes": ("FOK",),
        "execution_mode": "market",
        "session_policy_ref": "p:s",
        "spread_policy_ref": "p:sp",
        "margin_policy_ref": "p:m",
        "observed_at": NOW,
    }
    fields.update(overrides)
    return BrokerCapabilities(**fields)  # type: ignore[arg-type]


def _request(action: K, **overrides: object) -> ExecutionRequest:
    fields: dict[str, object] = {
        "request_id": "req-1",
        "idempotency_key": f"exec:{action.value}:p-1",
        "intent_proposal_id": "p-1",
        "origin_ref": "origin-1",
        "instrument_id": "inst-1",
        "side": "long",
        "action": action,
        "created_at": NOW,
    }
    if action is K.OPEN:
        fields["quantity"] = Decimal("1")
    elif action in (K.REDUCE, K.CLOSE):
        fields["position_ref"] = "pos-1"
        fields["quantity"] = Decimal("1")
    else:
        fields["position_ref"] = "pos-1"
        fields["protection"] = ProtectionRequest(stop_price=Decimal("1.0"))
    fields.update(overrides)
    return ExecutionRequest(**fields)  # type: ignore[arg-type]


_DEFAULT = object()


def _eval(
    request: ExecutionRequest,
    capabilities: object = _DEFAULT,
    *,
    now: datetime = NOW,
    bound: timedelta | None = BOUND,
) -> PreflightDecision:
    caps = _caps() if capabilities is _DEFAULT else capabilities
    return ExecutionPreflight().evaluate(
        request,
        caps,  # type: ignore[arg-type]
        None,
        now=now,
        max_capabilities_age=bound,
    )


# ------------------------------------------------- validate_volume totality


def test_validate_volume_huge_quotient_does_not_raise() -> None:
    caps = _caps(volume_max=Decimal("1E+40"), volume_step=Decimal("0.01"))
    assert validate_volume(caps, Decimal("1E+35")) is None


def test_validate_volume_exact_beyond_context_precision() -> None:
    caps = _caps(volume_min=Decimal("1"), volume_max=Decimal("1E+40"), volume_step=Decimal("1"))
    half = Decimal("1" + "0" * 35 + ".5")  # 37 digits, exceeds default context precision
    assert validate_volume(caps, half) == REASON_VOLUME_STEP_MISMATCH
    assert validate_volume(caps, Decimal("1" + "0" * 35 + ".0")) is None


@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_validate_volume_non_finite(bad: str) -> None:
    assert validate_volume(_caps(), Decimal(bad)) == REASON_VOLUME_STEP_MISMATCH


@pytest.mark.parametrize("bad", [1, "1", None, 1.0, True, False])
def test_validate_volume_non_decimal_quantity(bad: object) -> None:
    assert validate_volume(_caps(), bad) == REASON_VOLUME_STEP_MISMATCH  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [None, object(), "caps"])
def test_validate_volume_non_capabilities(bad: object) -> None:
    assert validate_volume(bad, Decimal("1")) == REASON_VOLUME_STEP_MISMATCH  # type: ignore[arg-type]


def test_validate_volume_zero_negative_and_bounds() -> None:
    caps = _caps()
    assert validate_volume(caps, Decimal("0")) == REASON_VOLUME_BELOW_MIN
    assert validate_volume(caps, Decimal("-1")) == REASON_VOLUME_BELOW_MIN
    assert validate_volume(caps, Decimal("10.05")) == REASON_VOLUME_ABOVE_MAX


def test_validate_volume_anchor_above_and_below_quantity() -> None:
    above = _caps(volume_step_anchor=Decimal("0.50"), volume_step=Decimal("0.05"))
    assert validate_volume(above, Decimal("0.15")) is None  # negative dividend, multiple
    assert validate_volume(above, Decimal("0.12")) == REASON_VOLUME_STEP_MISMATCH
    below = _caps(volume_step_anchor=Decimal("0.02"))
    assert validate_volume(below, Decimal("0.12")) is None
    assert validate_volume(below, Decimal("0.15")) == REASON_VOLUME_STEP_MISMATCH


def test_validate_volume_absurd_exponent_fails_closed() -> None:
    caps = _caps(volume_max=Decimal("1E+99999"))
    assert validate_volume(caps, Decimal("1E+50000")) == REASON_VOLUME_STEP_MISMATCH


def test_volume_codes_equal_adapter_constants() -> None:
    assert REASON_VOLUME_BELOW_MIN == broker_adapter.REASON_VOLUME_BELOW_MIN
    assert REASON_VOLUME_ABOVE_MAX == broker_adapter.REASON_VOLUME_ABOVE_MAX
    assert REASON_VOLUME_STEP_MISMATCH == broker_adapter.REASON_VOLUME_STEP_MISMATCH


# ------------------------------------------------- PreflightDecision


def _decision(**overrides: object) -> PreflightDecision:
    fields: dict[str, object] = {
        "allowed": True,
        "reason_codes": (),
        "request_ref": "r",
        "evaluated_at": NOW,
        "capabilities_observed_at": NOW,
    }
    fields.update(overrides)
    return PreflightDecision(**fields)  # type: ignore[arg-type]


def test_decision_invariants() -> None:
    assert _decision().allowed
    with pytest.raises(ValueError):
        _decision(reason_codes=("x",))
    with pytest.raises(ValueError):
        _decision(allowed=False)
    with pytest.raises(ValueError):
        _decision(evaluated_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        _decision(capabilities_observed_at=None)
    assert not _decision(allowed=False, reason_codes=("x",)).allowed


# ------------------------------------------------- Preflight


def test_request_ref_and_timestamps_reported() -> None:
    d = _eval(_request(K.OPEN))
    assert d.request_ref == "req-1"
    assert d.evaluated_at == NOW
    assert d.capabilities_observed_at == NOW


def test_instrument_mismatch_denies() -> None:
    d = _eval(_request(K.OPEN, instrument_id="other"))
    assert preflight.REASON_INSTRUMENT_MISMATCH in d.reason_codes


@pytest.mark.parametrize(
    ("quantity", "code"),
    [
        ("0.05", REASON_VOLUME_BELOW_MIN),
        ("10.05", REASON_VOLUME_ABOVE_MAX),
        ("0.12", REASON_VOLUME_STEP_MISMATCH),
    ],
)
def test_volume_reasons(quantity: str, code: str) -> None:
    d = _eval(_request(K.OPEN, quantity=Decimal(quantity)))
    assert not d.allowed
    assert code in d.reason_codes


@pytest.mark.parametrize("kind", [K.OPEN, K.REDUCE, K.CLOSE])
def test_none_quantity_denies_for_quantity_kinds(kind: K) -> None:
    request = _request(kind)
    object.__setattr__(request, "quantity", None)
    d = _eval(request)
    assert preflight.REASON_QUANTITY_MISSING in d.reason_codes


def test_modify_protection_has_no_volume_check() -> None:
    d = _eval(_request(K.MODIFY_PROTECTION))
    volume_codes = {
        REASON_VOLUME_BELOW_MIN,
        REASON_VOLUME_ABOVE_MAX,
        REASON_VOLUME_STEP_MISMATCH,
        preflight.REASON_QUANTITY_MISSING,
    }
    assert not volume_codes & set(d.reason_codes)
    assert preflight.REASON_STOPS_FREEZE_UNAVAILABLE in d.reason_codes
    assert not d.allowed


@pytest.mark.parametrize("bad", [None, object(), "caps"])
def test_missing_or_wrong_type_capabilities_deny(bad: object) -> None:
    d = _eval(_request(K.OPEN), bad)
    assert not d.allowed
    assert preflight.REASON_CAPABILITIES_MISSING in d.reason_codes
    assert d.capabilities_observed_at is None


def test_volume_validation_exception_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> str | None:
        raise RuntimeError("x")

    monkeypatch.setattr(preflight, "validate_volume", boom)
    d = _eval(_request(K.OPEN))
    assert "preflight_volume_validation_error" in d.reason_codes
    assert preflight.REASON_VOLUME_VALIDATION_ERROR == "preflight_volume_validation_error"
    assert not d.allowed


def test_freshness_no_bound_denies() -> None:
    d = _eval(_request(K.OPEN), bound=None)
    assert preflight.REASON_FRESHNESS_BOUND_MISSING in d.reason_codes
    d = _eval(_request(K.OPEN), bound=timedelta(seconds=-1))
    assert preflight.REASON_FRESHNESS_BOUND_MISSING in d.reason_codes


def test_freshness_stale_denies_and_fresh_passes() -> None:
    stale = _eval(_request(K.OPEN), now=NOW + BOUND + timedelta(seconds=1))
    assert preflight.REASON_CAPABILITIES_STALE in stale.reason_codes
    fresh = _eval(_request(K.OPEN), now=NOW + BOUND)
    assert not FRESHNESS_CODES & set(fresh.reason_codes)
    future = _eval(_request(K.OPEN), now=NOW - timedelta(seconds=1))
    assert preflight.REASON_CAPABILITIES_STALE in future.reason_codes


def test_naive_datetimes_deny() -> None:
    d = _eval(_request(K.OPEN), now=datetime(2026, 1, 1))
    assert preflight.REASON_CLOCK_REQUIRES_TIMEZONE in d.reason_codes
    assert not d.allowed
    caps = _caps()
    object.__setattr__(caps, "observed_at", datetime(2026, 1, 1))
    d = _eval(_request(K.OPEN), caps)
    assert preflight.REASON_CLOCK_REQUIRES_TIMEZONE in d.reason_codes


def test_risk_reducing_policy_undecided() -> None:
    for kind in (K.REDUCE, K.CLOSE, K.MODIFY_PROTECTION):
        d = _eval(_request(kind))
        assert "preflight_policy_undecided" in d.reason_codes
        assert not d.allowed
    d = _eval(_request(K.OPEN))
    assert preflight.REASON_POLICY_EVALUATION_UNAVAILABLE in d.reason_codes
    assert "preflight_policy_undecided" not in d.reason_codes


def test_deterministic_reason_order_and_repeatability() -> None:
    request = _request(K.OPEN, instrument_id="other", quantity=Decimal("0.05"))
    d1 = _eval(request, bound=None)
    d2 = _eval(request, bound=None)
    assert d1 == d2
    assert d1.reason_codes == (
        preflight.REASON_INSTRUMENT_MISMATCH,
        REASON_VOLUME_BELOW_MIN,
        preflight.REASON_FRESHNESS_BOUND_MISSING,
        preflight.REASON_POLICY_EVALUATION_UNAVAILABLE,
    )


# ------------------------------------------------- purity


def _source() -> str:
    return Path(preflight.__file__).read_text(encoding="utf-8")


def test_preflight_imports_are_pure() -> None:
    tree = ast.parse(_source())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    assert roots <= {
        "__future__",
        "dataclasses",
        "datetime",
        "nexora.autonomous.broker_capabilities",
        "nexora.autonomous_contracts",
        "nexora.execution.models",
    }


def test_preflight_reads_no_clock_and_has_no_io_calls() -> None:
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"now", "utcnow", "today", "open", "write", "submit"}
        if isinstance(node, ast.Name):
            assert node.id not in {"open", "print", "input"}


def test_preflight_never_constructs_or_calls_an_adapter() -> None:
    source = _source()
    assert "Adapter" not in source
    assert "submit" not in source
    assert "order_send" not in source
    assert "adapter" not in ExecutionPreflight.evaluate.__code__.co_varnames
