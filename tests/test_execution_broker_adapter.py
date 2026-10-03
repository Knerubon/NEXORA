"""BROKER-EXEC-1 tests: adapter Protocol, simulated adapter, architecture guard.

No I/O, no broker, no network.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import nexora.execution as execution_pkg
import pytest
from nexora.autonomous.broker_capabilities import BrokerCapabilities
from nexora.autonomous_contracts import TradeIntentKind
from nexora.execution.broker_adapter import (
    SIMULATION_MODE,
    BrokerAdapterError,
    BrokerExecutionAdapter,
    ScriptedOutcome,
    SimulatedBrokerAdapter,
    SimulatedOutcome,
)
from nexora.execution.models import (
    ExecutionRequest,
    ExecutionStatus,
    is_safe_to_retry_without_reconciliation,
)
from nexora.market_data.instruments import FeedBinding, InstrumentDefinition, PriceGrid

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _caps() -> BrokerCapabilities:
    return BrokerCapabilities(
        binding=FeedBinding(
            instrument_id="inst-1",
            broker_id="broker-a",
            symbol="SYM",
            price_grid=PriceGrid(digits=2, point=Decimal("0.01"), trade_tick_size=None),
            time_offset_seconds=0,
        ),
        instrument=InstrumentDefinition(
            instrument_id="inst-1",
            currency_base="USD",
            currency_profit="USD",
            trade_calc_mode=0,
            trade_contract_size=Decimal("100"),
            chart_mode=0,
        ),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("10"),
        volume_step=Decimal("0.01"),
        stops_level=Decimal("0"),
        freeze_level=Decimal("0"),
        filling_modes=("FOK",),
        execution_mode="market",
        session_policy_ref="p:s",
        spread_policy_ref="p:sp",
        margin_policy_ref="p:m",
        observed_at=NOW,
    )


def _request(
    key: str = "k1", quantity: str | None = "1.00", instrument: str = "inst-1"
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=f"req-{key}",
        idempotency_key=key,
        intent_proposal_id="prop-1",
        origin_ref="origin-1",
        instrument_id=instrument,
        side="long",
        action=TradeIntentKind.OPEN,
        quantity=Decimal(quantity) if quantity is not None else None,
        created_at=NOW,
    )


def _adapter(*outcomes: ScriptedOutcome) -> SimulatedBrokerAdapter:
    return SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps(), script=outcomes)


def test_simulated_adapter_satisfies_protocol() -> None:
    assert isinstance(_adapter(), BrokerExecutionAdapter)


def test_filled_is_default_and_not_safe_to_retry() -> None:
    result = _adapter().submit(_request())
    assert result.status is ExecutionStatus.FILLED
    assert result.filled_quantity == Decimal("1.00")
    assert result.remaining_quantity == 0
    assert not is_safe_to_retry_without_reconciliation(result)


def test_partial_fill() -> None:
    adapter = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.PARTIALLY_FILLED))
    result = adapter.submit(_request())
    assert result.status is ExecutionStatus.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("0.50")
    assert result.remaining_quantity == Decimal("0.50")
    assert not is_safe_to_retry_without_reconciliation(result)


def test_unrepresentable_partial_fill_raises() -> None:
    adapter = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.PARTIALLY_FILLED))
    with pytest.raises(BrokerAdapterError):
        adapter.submit(_request(quantity="0.01"))


def test_accepted_is_not_safe_to_retry() -> None:
    result = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.ACCEPTED)).submit(_request())
    assert result.status is ExecutionStatus.ACCEPTED
    assert not is_safe_to_retry_without_reconciliation(result)


def test_scripted_rejection_is_safe_to_retry() -> None:
    result = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.REJECTED)).submit(_request())
    assert result.status is ExecutionStatus.REJECTED
    assert result.filled_quantity == 0
    assert is_safe_to_retry_without_reconciliation(result)


def test_timeout_returns_unknown_never_rejected() -> None:
    result = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.TIMEOUT)).submit(_request())
    assert result.status is ExecutionStatus.UNKNOWN
    assert result.remaining_quantity is None
    assert result.reason_code == "timeout_after_transmission"
    assert not is_safe_to_retry_without_reconciliation(result)


def test_idempotent_resubmit_returns_same_result_without_second_fill() -> None:
    adapter = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.REJECTED))
    first = adapter.submit(_request("dup"))
    second = adapter.submit(_request("dup"))
    assert second is first
    # Script was not consumed again: a new key now gets the default FILLED, once.
    assert adapter.submit(_request("other")).status is ExecutionStatus.FILLED
    assert adapter.simulated_fill_count == 1
    adapter.submit(_request("other"))
    assert adapter.simulated_fill_count == 1


def test_idempotent_resubmit_after_unknown_stays_unknown() -> None:
    adapter = _adapter(ScriptedOutcome(outcome=SimulatedOutcome.TIMEOUT))
    first = adapter.submit(_request("t"))
    again = adapter.submit(_request("t"))
    assert again is first
    assert again.status is ExecutionStatus.UNKNOWN
    assert adapter.simulated_fill_count == 0


@pytest.mark.parametrize(
    ("quantity", "reason"),
    [
        ("0.005", "volume_below_min"),
        ("10.01", "volume_above_max"),
        ("1.005", "volume_not_multiple_of_step"),
    ],
)
def test_volume_limits_rejected_with_normalized_reason(quantity: str, reason: str) -> None:
    adapter = _adapter()
    result = adapter.submit(_request(quantity=quantity))
    assert result.status is ExecutionStatus.REJECTED
    assert result.reason_code == reason
    assert adapter.simulated_fill_count == 0


def test_volume_boundaries_accepted() -> None:
    adapter = _adapter()
    assert adapter.submit(_request("a", "0.01")).status is ExecutionStatus.FILLED
    assert adapter.submit(_request("b", "10")).status is ExecutionStatus.FILLED


def test_unknown_instrument_rejected_and_broker_symbol_not_accepted() -> None:
    adapter = _adapter()
    for key, instrument in (("x", "inst-2"), ("y", "SYM")):
        result = adapter.submit(_request(key, instrument=instrument))
        assert result.status is ExecutionStatus.REJECTED
        assert result.reason_code == "instrument_not_supported"


def test_quantity_less_request_refused() -> None:
    request = ExecutionRequest(
        request_id="r",
        idempotency_key="c",
        intent_proposal_id="p",
        origin_ref="o",
        instrument_id="inst-1",
        side="long",
        action=TradeIntentKind.CLOSE,
        position_ref="pos-1",
        created_at=NOW,
    )
    with pytest.raises(BrokerAdapterError):
        _adapter().submit(request)


@pytest.mark.parametrize("mode", ["live", "demo", "paper", "", "SIMULATION"])
def test_refuses_non_simulation_mode(mode: str) -> None:
    with pytest.raises(BrokerAdapterError) as info:
        SimulatedBrokerAdapter(mode=mode, capabilities=_caps())
    assert info.value.code == "simulated_adapter_requires_simulation_mode"


def test_refuses_use_if_mode_mutated() -> None:
    adapter = _adapter()
    adapter._mode = "live"
    with pytest.raises(BrokerAdapterError):
        adapter.submit(_request())


# --- architecture guard -------------------------------------------------------

_FORBIDDEN_MODULES = {
    "metatrader5",
    "socket",
    "ssl",
    "http",
    "urllib",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
    "ftplib",
    "smtplib",
    "xmlrpc",
    "subprocess",
}
_FORBIDDEN_NAME = "order" + "_send"


def _execution_sources() -> list[Path]:
    return sorted(Path(execution_pkg.__file__).parent.glob("*.py"))


def test_execution_package_has_no_broker_or_network_imports() -> None:
    sources = _execution_sources()
    assert any(p.name == "broker_adapter.py" for p in sources)
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0].lower() not in _FORBIDDEN_MODULES, (path.name, name)


def test_execution_package_never_references_order_transmission_call() -> None:
    for path in _execution_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                assert node.id != _FORBIDDEN_NAME, path.name
            elif isinstance(node, ast.Attribute):
                assert node.attr != _FORBIDDEN_NAME, path.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert _FORBIDDEN_NAME not in node.value, path.name
