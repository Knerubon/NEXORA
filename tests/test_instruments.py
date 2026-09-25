"""ADR-025 Phase 2A: pure instrument/broker/binding contracts, resolver and feed_id."""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nexora.artifacts import canonical_hash
from nexora.market_data.instruments import (
    BINDING_TIME_CONTRACT,
    FEED_CONTRACT,
    BrokerIdentity,
    FeedBinding,
    InstrumentDefinition,
    InstrumentError,
    InstrumentsConfig,
    ObservedSymbol,
    ObservedTerminal,
    PriceGrid,
    ResolvedFeed,
    compare_feed_identity,
    decode_instruments_config,
    feed_id,
    feed_identity_payload,
    legacy_session_fingerprint,
    observe_symbol,
    observe_terminal,
    observed_decimal,
    parse_instruments_config,
    resolve_feed,
    select_feed_mode,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "packages/nexora/market_data/instruments.py"
EXAMPLE = ROOT / "config/instruments.example.json"
MT5_PATH = r"C:\Path\To\terminal64.exe"

# Golden vector: the example config's canonical identity bytes, written out literally.
GOLDEN_CANONICAL = (
    '{"broker":{"broker_id":"example-broker","company":"Example Company"},'
    '"contract":"adr025-feed-v1",'
    '"instrument":{"chart_mode":0,"currency_base":"EXA","currency_profit":"USD",'
    '"instrument_id":"EXAMPLE-INSTRUMENT","trade_calc_mode":0,"trade_contract_size":"100"},'
    '"price_grid":{"digits":2,"point":"0.01","trade_tick_size":"0.01"},'
    '"symbol":"EXAMPLE_SYMBOL",'
    '"time_contract":{"offset_seconds":0,"version":"adr025-binding-v1"}}'
)
GOLDEN_FEED_ID = "mt5f1-10bd7808c117f7aa851a72e6488a72be5c5f3b15604eaec193178b1055ca5575"


def _payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "instruments": [
            {
                "instrument_id": "GOLD-SPOT",
                "currency_base": "XAU",
                "currency_profit": "USD",
                "trade_calc_mode": 0,
                "trade_contract_size": "100",
                "chart_mode": 0,
            }
        ],
        "brokers": [
            {"broker_id": "broker-a", "company": "Broker A Ltd", "terminal_path": MT5_PATH}
        ],
        "bindings": [
            {
                "instrument_id": "GOLD-SPOT",
                "broker_id": "broker-a",
                "symbol": "XAUUSD",
                "price_grid": {"digits": 2, "point": "0.01", "trade_tick_size": "0.01"},
                "time_offset_seconds": 10800,
            }
        ],
    }


def _config(mutate: Callable[[dict[str, Any]], None] | None = None) -> InstrumentsConfig:
    payload = copy.deepcopy(_payload())
    if mutate:
        mutate(payload)
    return decode_instruments_config(payload)


def _info(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "name": "XAUUSD",
        "visible": True,
        "custom": False,
        "currency_base": "XAU",
        "currency_profit": "USD",
        "trade_calc_mode": 0,
        "trade_contract_size": 100.0,
        "chart_mode": 0,
        "digits": 2,
        "point": 0.01,
        "trade_tick_size": 0.01,
        "description": "Gold vs US Dollar",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _terminal(**overrides: Any) -> ObservedTerminal:
    values: dict[str, Any] = {
        "connected": True,
        "company": "Broker A Ltd",
        "path": r"C:\Path\To",
    }
    values.update(overrides)
    return ObservedTerminal(**values)


class Lookup:
    """Fake exact lookup: records every name asked for; knows several similar symbols."""

    def __init__(self, symbols: dict[str, SimpleNamespace]) -> None:
        self.symbols = symbols
        self.calls: list[str] = []

    def __call__(self, name: str) -> ObservedSymbol | None:
        self.calls.append(name)
        return observe_symbol(name, self.symbols.get(name))


def _resolve(
    config: InstrumentsConfig | None = None,
    *,
    symbols: dict[str, SimpleNamespace] | None = None,
    terminal: ObservedTerminal | None = None,
    instrument_id: str = "GOLD-SPOT",
    mt5_path: str = MT5_PATH,
) -> tuple[ResolvedFeed, Lookup]:
    lookup = Lookup({"XAUUSD": _info()} if symbols is None else symbols)
    resolved = resolve_feed(
        config or _config(),
        instrument_id=instrument_id,
        mt5_path=mt5_path,
        terminal=_terminal() if terminal is None else terminal,
        symbol_info=lookup,
    )
    return resolved, lookup


def _code(action: Callable[[], object]) -> str:
    with pytest.raises(InstrumentError) as caught:
        action()
    return caught.value.code


# --- config ---------------------------------------------------------------------------------


def test_valid_config_decodes_to_frozen_contracts() -> None:
    config = _config()
    assert config.instruments[0] == InstrumentDefinition(
        "GOLD-SPOT", "XAU", "USD", 0, Decimal("100"), 0
    )
    assert config.brokers[0] == BrokerIdentity("broker-a", "Broker A Ltd", MT5_PATH)
    assert config.bindings[0].price_grid == PriceGrid(2, Decimal("0.01"), Decimal("0.01"))
    assert parse_instruments_config(json.dumps(_payload())) == config


def test_example_config_is_valid_and_placeholder_only() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    config = parse_instruments_config(text)
    assert config.instruments[0].instrument_id == "EXAMPLE-INSTRUMENT"
    assert config.brokers[0].broker_id == "example-broker"
    assert config.bindings[0].symbol == "EXAMPLE_SYMBOL"
    lowered = text.lower()
    for word in ("password", "login", "server", "account", "token", "secret", "investor"):
        assert word not in lowered


@pytest.mark.parametrize(
    "text",
    [
        "{",  # malformed
        "[]",  # not an object
        '{"schema_version":1,"schema_version":1,"instruments":[],"brokers":[],"bindings":[]}',
        '{"schema_version":NaN,"instruments":[],"brokers":[],"bindings":[]}',
        '{"schema_version":Infinity,"instruments":[],"brokers":[],"bindings":[]}',
        '{"schema_version":1.0,"instruments":[],"brokers":[],"bindings":[]}',
    ],
)
def test_malformed_json_is_rejected(text: str) -> None:
    assert _code(lambda: parse_instruments_config(text)) == "instrument_config_invalid"


def _set(path: tuple[Any, ...], value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(payload: dict[str, Any]) -> None:
        target: Any = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _delete(path: tuple[Any, ...]) -> Callable[[dict[str, Any]], None]:
    def mutate(payload: dict[str, Any]) -> None:
        target: Any = payload
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


def _append(section: str, index: int = 0, **changes: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(payload: dict[str, Any]) -> None:
        item = copy.deepcopy(payload[section][index])
        item.update(changes)
        payload[section].append(item)

    return mutate


INVALID_CONFIGS: dict[str, Callable[[dict[str, Any]], None]] = {
    "unknown root key": _set(("extra",), 1),
    "unknown instrument key": _set(("instruments", 0, "isin"), "X"),
    "unknown grid key": _set(("bindings", 0, "price_grid", "tick_value"), "1"),
    "missing root key": _delete(("bindings",)),
    "missing identity field": _delete(("instruments", 0, "trade_contract_size")),
    "missing price grid field": _delete(("bindings", 0, "price_grid", "point")),
    "schema version 2": _set(("schema_version",), 2),
    "schema version bool": _set(("schema_version",), True),
    "bad instrument id charset": _set(("instruments", 0, "instrument_id"), "gold spot"),
    "instrument id too long": _set(("instruments", 0, "instrument_id"), "x" * 65),
    "empty currency": _set(("instruments", 0, "currency_base"), ""),
    "calc mode as string": _set(("instruments", 0, "trade_calc_mode"), "0"),
    "calc mode bool": _set(("instruments", 0, "trade_calc_mode"), False),
    "contract size number": _set(("instruments", 0, "trade_contract_size"), 100),
    "contract size zero": _set(("instruments", 0, "trade_contract_size"), "0"),
    "contract size exponent": _set(("instruments", 0, "trade_contract_size"), "1e2"),
    "contract size padded": _set(("instruments", 0, "trade_contract_size"), " 100"),
    "digits too large": _set(("bindings", 0, "price_grid", "digits"), 11),
    "digits negative": _set(("bindings", 0, "price_grid", "digits"), -1),
    "point negative": _set(("bindings", 0, "price_grid", "point"), "-0.01"),
    "tick size zero string": _set(("bindings", 0, "price_grid", "trade_tick_size"), "0"),
    "offset out of range": _set(("bindings", 0, "time_offset_seconds"), 50401),
    "offset string": _set(("bindings", 0, "time_offset_seconds"), "0"),
    "relative terminal path": _set(("brokers", 0, "terminal_path"), r"MT5\terminal64.exe"),
    "empty symbol": _set(("bindings", 0, "symbol"), ""),
    "malformed broker": _set(("brokers", 0), "broker-a"),
    "duplicate instrument": _append("instruments"),
    "duplicate broker": _append("brokers", company="Other", terminal_path=r"D:\Other\t.exe"),
    "indistinguishable brokers": _append("brokers", broker_id="broker-b"),
    "same terminal differently cased": _append(
        "brokers", broker_id="broker-b", terminal_path=MT5_PATH.upper()
    ),
    "duplicate binding": _append("bindings", symbol="XAUUSD.a"),
    "duplicate broker symbol": _append("bindings", instrument_id="OTHER"),
    "dangling instrument": _set(("bindings", 0, "instrument_id"), "UNKNOWN"),
    "dangling broker": _set(("bindings", 0, "broker_id"), "unknown"),
    "credential in broker": _set(("brokers", 0, "login"), "12345"),
    "credential password": _set(("brokers", 0, "password"), "x"),
    "credential server in binding": _set(("bindings", 0, "server"), "Demo-01"),
    "credential at root": _set(("account",), "1"),
}


@pytest.mark.parametrize("name", sorted(INVALID_CONFIGS))
def test_invalid_config_fails_closed(name: str) -> None:
    code = _code(lambda: _config(INVALID_CONFIGS[name]))
    assert code == "instrument_config_invalid"


def test_credential_fields_are_named_and_values_never_echoed() -> None:
    with pytest.raises(InstrumentError) as caught:
        _config(_set(("brokers", 0, "password"), "hunter2-secret"))
    assert caught.value.field.endswith("credential_field")
    assert "hunter2" not in str(caught.value) and "hunter2" not in caught.value.field
    assert str(caught.value) == "instrument_config_invalid"


def test_null_tick_size_is_allowed_and_means_not_provided() -> None:
    config = _config(_set(("bindings", 0, "price_grid", "trade_tick_size"), None))
    assert config.bindings[0].price_grid.trade_tick_size is None


# --- mode selection (ADR-025 §9) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("config_set", "instrument", "symbol", "offset", "expected"),
    [
        (True, True, False, False, "binding"),
        (True, False, False, False, "not_configured"),
        (True, False, True, False, "feed_mode_conflict"),
        (True, True, True, False, "feed_mode_conflict"),
        (False, True, False, False, "feed_mode_conflict"),
        (False, True, True, False, "feed_mode_conflict"),
        (False, False, True, False, "legacy"),
        (False, False, True, True, "legacy"),
        (False, False, False, False, "not_configured"),
        (True, True, False, True, "time_offset_conflict"),
    ],
)
def test_mode_matrix(
    config_set: bool, instrument: bool, symbol: bool, offset: bool, expected: str
) -> None:
    def select() -> str:
        return select_feed_mode(
            instruments_config=config_set,
            instrument=instrument,
            symbol=symbol,
            legacy_time_offset=offset,
        )

    if expected in ("binding", "legacy"):
        assert select() == expected
    else:
        assert _code(select) == expected


# --- resolution: success, broker ------------------------------------------------------------


def test_exact_binding_resolves_and_pins_feed_id() -> None:
    resolved, lookup = _resolve()
    assert lookup.calls == ["XAUUSD"]
    assert resolved.feed_id.startswith("mt5f1-")
    assert resolved.feed_id == feed_id(resolved.instrument, resolved.broker, resolved.binding)
    assert resolved.time_contract == (BINDING_TIME_CONTRACT, 10800)


def test_terminal_path_match_is_normalized() -> None:
    resolved, _ = _resolve(
        mt5_path=r"c:\path\TO\.\terminal64.exe", terminal=_terminal(path="C:\\PATH\\to\\")
    )
    assert resolved.broker.broker_id == "broker-a"


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (_terminal(company="Broker A Limited"), "broker_identity_mismatch"),
        (_terminal(company="broker a ltd"), "broker_identity_mismatch"),
        (_terminal(company=None), "broker_identity_mismatch"),
        (_terminal(path=r"D:\Other"), "broker_identity_mismatch"),
        (_terminal(path=None), "broker_identity_mismatch"),
        (_terminal(connected=False), "terminal_disconnected"),
    ],
)
def test_broker_identity_mismatch(terminal: ObservedTerminal, expected: str) -> None:
    assert _code(lambda: _resolve(terminal=terminal)) == expected


def test_unbound_terminal_path_is_broker_not_bound() -> None:
    assert _code(lambda: _resolve(mt5_path=r"D:\Unknown\terminal64.exe")) == "broker_not_bound"


def test_unbound_instrument_is_broker_not_bound() -> None:
    assert _code(lambda: _resolve(instrument_id="SILVER-SPOT")) == "broker_not_bound"


def test_ambiguous_broker_is_reported_defensively() -> None:
    config = _config()
    twin = replace(config.brokers[0], broker_id="broker-b", company="Other Co")
    ambiguous = replace(config, brokers=(*config.brokers, twin))  # bypasses validation
    assert _code(lambda: _resolve(ambiguous)) == "broker_ambiguous"


def test_missing_instrument_or_path_is_not_configured() -> None:
    assert _code(lambda: _resolve(instrument_id="")) == "not_configured"
    assert _code(lambda: _resolve(mt5_path="")) == "not_configured"


# --- exact symbol ---------------------------------------------------------------------------

SIMILAR = {
    "XAUUSD.a": _info(name="XAUUSD.a"),
    "XAUUSD-STD": _info(name="XAUUSD-STD"),
    "xauusd": _info(name="xauusd"),
    "XAUUSDm": _info(name="XAUUSDm"),
}


def test_missing_exact_symbol_never_falls_back_to_similar_symbols() -> None:
    lookup = Lookup(SIMILAR)
    with pytest.raises(InstrumentError) as caught:
        resolve_feed(
            _config(),
            instrument_id="GOLD-SPOT",
            mt5_path=MT5_PATH,
            terminal=_terminal(),
            symbol_info=lookup,
        )
    assert caught.value.code == "symbol_not_found"
    assert lookup.calls == ["XAUUSD"]


def test_invisible_exact_symbol_is_never_switched() -> None:
    symbols = {"XAUUSD": _info(visible=False), **SIMILAR}
    lookup = Lookup(symbols)
    with pytest.raises(InstrumentError) as caught:
        resolve_feed(
            _config(),
            instrument_id="GOLD-SPOT",
            mt5_path=MT5_PATH,
            terminal=_terminal(),
            symbol_info=lookup,
        )
    assert caught.value.code == "symbol_not_visible"
    assert lookup.calls == ["XAUUSD"]


@pytest.mark.parametrize("symbol", ["XAUUSD", "XAUUSD.a", "XAUUSD-STD"])
def test_suffix_variants_are_distinct_raw_symbols(symbol: str) -> None:
    config = _config(_set(("bindings", 0, "symbol"), symbol))
    symbols = {name: _info(name=name) for name in ("XAUUSD", "XAUUSD.a", "XAUUSD-STD")}
    resolved, lookup = _resolve(config, symbols=symbols)
    assert lookup.calls == [symbol]
    assert resolved.binding.symbol == symbol
    ids = {
        _resolve(_config(_set(("bindings", 0, "symbol"), s)), symbols=symbols)[0].feed_id
        for s in symbols
    }
    assert len(ids) == 3


def test_symbol_lookup_is_case_sensitive() -> None:
    assert _code(lambda: _resolve(symbols={"xauusd": _info(name="xauusd")})) == "symbol_not_found"


def test_a_substituting_lookup_is_rejected() -> None:
    def substitute(name: str) -> ObservedSymbol | None:
        return observe_symbol("XAUUSD.a", _info(name="XAUUSD.a"))

    assert (
        _code(
            lambda: resolve_feed(
                _config(),
                instrument_id="GOLD-SPOT",
                mt5_path=MT5_PATH,
                terminal=_terminal(),
                symbol_info=substitute,
            )
        )
        == "symbol_not_found"
    )


def test_custom_symbol_is_rejected() -> None:
    code = _code(lambda: _resolve(symbols={"XAUUSD": _info(custom=True)}))
    assert code == "instrument_metadata_mismatch"


# --- semantic metadata and price grid -------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"currency_base": "XAG"},
        {"currency_profit": "EUR"},
        {"trade_calc_mode": 2},
        {"trade_contract_size": 1.0},
        {"chart_mode": 1},
        {"digits": 3},
        {"point": 0.001},
        {"trade_tick_size": 0.05},
        {"trade_tick_size": 0.0},
        {"currency_base": None},
        {"trade_contract_size": "100"},  # wrong observed type = unreadable
        {"digits": 2.0},
        {"custom": None},
        {"chart_mode": True},
    ],
)
def test_metadata_mismatch_fails_closed(override: dict[str, Any]) -> None:
    code = _code(lambda: _resolve(symbols={"XAUUSD": _info(**override)}))
    assert code == "instrument_metadata_mismatch"


def test_unreadable_attribute_fails_closed() -> None:
    info = _info()
    del info.trade_tick_size
    assert _code(lambda: _resolve(symbols={"XAUUSD": info})) == "instrument_metadata_mismatch"


def test_invisible_is_reported_before_metadata() -> None:
    info = _info(visible=False, currency_base="XAG")
    assert _code(lambda: _resolve(symbols={"XAUUSD": info})) == "symbol_not_visible"


def test_same_symbol_name_at_two_brokers_with_different_contract_is_never_equivalent() -> None:
    config = _config()
    other_path = r"D:\BrokerB\terminal64.exe"
    broker_b = BrokerIdentity("broker-b", "Broker B Inc", other_path)
    binding_b = replace(config.bindings[0], broker_id="broker-b")
    both = replace(
        config, brokers=(*config.brokers, broker_b), bindings=(*config.bindings, binding_b)
    )
    resolved_a, _ = _resolve(both)
    code = _code(
        lambda: _resolve(
            both,
            mt5_path=other_path,
            terminal=_terminal(company="Broker B Inc", path=r"D:\BrokerB"),
            symbols={"XAUUSD": _info(trade_contract_size=1.0)},
        )
    )
    assert code == "instrument_metadata_mismatch"
    resolved_b, _ = _resolve(
        both,
        mt5_path=other_path,
        terminal=_terminal(company="Broker B Inc", path=r"D:\BrokerB"),
    )
    assert resolved_a.feed_id != resolved_b.feed_id  # same symbol name, different broker


def test_null_declared_tick_size_requires_observed_zero() -> None:
    config = _config(_set(("bindings", 0, "price_grid", "trade_tick_size"), None))
    resolved, _ = _resolve(config, symbols={"XAUUSD": _info(trade_tick_size=0.0)})
    assert resolved.binding.price_grid.trade_tick_size is None
    code = _code(lambda: _resolve(config, symbols={"XAUUSD": _info(trade_tick_size=0.01)}))
    assert code == "instrument_metadata_mismatch"


def test_observed_floats_use_shortest_round_trip_decimal() -> None:
    assert observed_decimal(0.1) == Decimal("0.1")
    assert observed_decimal(1e-05) == Decimal("0.00001")
    assert observed_decimal(float("nan")) is None
    assert observed_decimal(float("inf")) is None
    assert observed_decimal(True) is None
    assert observed_decimal("0.1") is None


def test_declared_decimal_trailing_zeros_compare_and_hash_equal() -> None:
    padded = _config(_set(("bindings", 0, "price_grid", "point"), "0.010"))
    assert _resolve(padded)[0].feed_id == _resolve()[0].feed_id


# --- time contract --------------------------------------------------------------------------


def test_time_contract_is_part_of_identity() -> None:
    base = _resolve()[0]
    shifted = _resolve(_config(_set(("bindings", 0, "time_offset_seconds"), 7200)))[0]
    assert shifted.time_contract == (BINDING_TIME_CONTRACT, 7200)
    assert shifted.feed_id != base.feed_id
    payload = feed_identity_payload(base.instrument, base.broker, base.binding)
    assert payload["time_contract"] == {"version": "adr025-binding-v1", "offset_seconds": 10800}


# --- feed_id ----------------------------------------------------------------------------------


def test_golden_feed_id_from_literal_canonical_bytes() -> None:
    config = parse_instruments_config(EXAMPLE.read_text(encoding="utf-8"))
    instrument, broker, binding = config.instruments[0], config.brokers[0], config.bindings[0]
    independent = "mt5f1-" + hashlib.sha256(GOLDEN_CANONICAL.encode("utf-8")).hexdigest()
    assert independent == GOLDEN_FEED_ID
    assert feed_id(instrument, broker, binding) == GOLDEN_FEED_ID
    assert json.loads(GOLDEN_CANONICAL)["contract"] == FEED_CONTRACT


def test_feed_id_is_independent_of_config_key_order() -> None:
    shuffled = json.loads(json.dumps(_payload()), object_pairs_hook=lambda p: dict(reversed(p)))
    assert _resolve(decode_instruments_config(shuffled))[0].feed_id == _resolve()[0].feed_id


def test_feed_id_is_stable_across_processes() -> None:
    code = (
        "import json,sys;sys.dont_write_bytecode=True;"
        "from nexora.market_data.instruments import parse_instruments_config, feed_id;"
        f"c=parse_instruments_config(open(r'{EXAMPLE}',encoding='utf-8').read());"
        "print(feed_id(c.instruments[0],c.brokers[0],c.bindings[0]))"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-B", "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "SYSTEMROOT": _systemroot()},
        ).stdout.strip()
        for seed in ("0", "1", "4242")
    }
    assert outputs == {GOLDEN_FEED_ID}


def _systemroot() -> str:
    import os

    return os.environ.get("SYSTEMROOT", "")


def _both(*mutators: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    def mutate(payload: dict[str, Any]) -> None:
        for mutator in mutators:
            mutator(payload)

    return mutate


IDENTITY_CHANGES: dict[str, Callable[[dict[str, Any]], None]] = {
    "instrument_id": _both(
        _set(("instruments", 0, "instrument_id"), "GOLD-2"),
        _set(("bindings", 0, "instrument_id"), "GOLD-2"),
    ),
    "currency_base": _set(("instruments", 0, "currency_base"), "XAG"),
    "currency_profit": _set(("instruments", 0, "currency_profit"), "EUR"),
    "trade_calc_mode": _set(("instruments", 0, "trade_calc_mode"), 2),
    "trade_contract_size": _set(("instruments", 0, "trade_contract_size"), "1"),
    "chart_mode": _set(("instruments", 0, "chart_mode"), 1),
    "broker_id": _both(
        _set(("brokers", 0, "broker_id"), "broker-z"),
        _set(("bindings", 0, "broker_id"), "broker-z"),
    ),
    "company": _set(("brokers", 0, "company"), "Broker A Holdings"),
    "symbol": _set(("bindings", 0, "symbol"), "XAUUSD.a"),
    "digits": _set(("bindings", 0, "price_grid", "digits"), 3),
    "point": _set(("bindings", 0, "price_grid", "point"), "0.001"),
    "trade_tick_size": _set(("bindings", 0, "price_grid", "trade_tick_size"), "0.05"),
    "tick_size_null": _set(("bindings", 0, "price_grid", "trade_tick_size"), None),
    "offset": _set(("bindings", 0, "time_offset_seconds"), 0),
}


def _declared_feed_id(config: InstrumentsConfig) -> str:
    return feed_id(config.instruments[0], config.brokers[0], config.bindings[0])


@pytest.mark.parametrize("name", sorted(IDENTITY_CHANGES))
def test_every_identity_bearing_field_changes_feed_id(name: str) -> None:
    assert _declared_feed_id(_config(IDENTITY_CHANGES[name])) != _declared_feed_id(_config())


def test_excluded_fields_do_not_change_feed_id() -> None:
    base = _declared_feed_id(_config())
    moved = _config(_set(("brokers", 0, "terminal_path"), r"E:\Elsewhere\terminal64.exe"))
    described = _config(_set(("instruments", 0, "description"), "anything"))
    assert _declared_feed_id(moved) == base
    assert _declared_feed_id(described) == base
    resolved, _ = _resolve(
        symbols={"XAUUSD": _info(description="changed", spread=35, bid=2400.5, volume_min=0.01)}
    )
    assert resolved.feed_id == base


def test_feed_identity_payload_has_no_host_or_account_fields() -> None:
    payload = feed_identity_payload(
        *(_config().instruments[0], _config().brokers[0], _config().bindings[0])
    )
    text = json.dumps(payload, default=str).lower()
    for forbidden in ("terminal_path", "path", "login", "server", "account", "build", "host"):
        assert forbidden not in text
    assert set(payload) == {
        "contract",
        "instrument",
        "broker",
        "symbol",
        "price_grid",
        "time_contract",
    }


# --- reconnect pure check -------------------------------------------------------------------


def test_reconnect_identity_comparison() -> None:
    pinned = _resolve()[0].feed_id
    assert compare_feed_identity(pinned, _resolve()[0].feed_id) == "unchanged"
    changed = _resolve(_config(_set(("bindings", 0, "time_offset_seconds"), 0)))[0].feed_id
    assert compare_feed_identity(pinned, changed) == "feed_identity_changed"


def test_legacy_session_fingerprint() -> None:
    observed = observe_symbol("XAUUSD", _info())
    assert observed is not None
    first = legacy_session_fingerprint("Broker A Ltd", observed, 10800)
    assert first == legacy_session_fingerprint("Broker A Ltd", observed, 10800)
    assert first != legacy_session_fingerprint("Broker B", observed, 10800)
    assert first != legacy_session_fingerprint("Broker A Ltd", observed, 0)
    grid = observe_symbol("XAUUSD", _info(digits=3))
    assert grid is not None and first != legacy_session_fingerprint("Broker A Ltd", grid, 10800)
    unreadable = observe_symbol("XAUUSD", _info(point=None))
    assert unreadable is not None
    code = _code(lambda: legacy_session_fingerprint("Broker A Ltd", unreadable, 10800))
    assert code == "instrument_metadata_mismatch"


# --- observed metadata allowlist ------------------------------------------------------------


class _TrapTerminal:
    connected = True
    company = "Broker A Ltd"
    path = r"C:\Path\To"

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"terminal_info().{name} is outside the ADR-003 allowlist")


def test_observe_terminal_reads_only_the_allowlist() -> None:
    observed = observe_terminal(_TrapTerminal())
    assert observed == ObservedTerminal(True, "Broker A Ltd", r"C:\Path\To")
    assert observe_terminal(None) is None


# --- Q-Q1 boundary, purity, immutability ----------------------------------------------------


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_core_imports_only_stdlib_and_artifacts() -> None:
    imports = _imports(MODULE)
    allowed = {
        "__future__",
        "json",
        "ntpath",
        "re",
        "collections.abc",
        "dataclasses",
        "decimal",
        "typing",
        "nexora.artifacts",
    }
    assert imports <= allowed, imports - allowed


def test_core_has_no_io_clock_env_or_pnf_references() -> None:
    source = MODULE.read_text(encoding="utf-8")
    for token in (
        "open(",
        "os.environ",
        "getenv",
        "datetime",
        "time.",
        "socket",
        "subprocess",
        "sqlite",
        "MetaTrader5",
        "initialize(",
        "account_info",
        "symbol_select",
        "symbols_get",
        "box_size =",
        "price_precision =",
        "PnfConfig",
        "nexora.pnf",
    ):
        assert token not in source, token


def test_importing_core_loads_no_runtime_decision_or_mt5_module() -> None:
    code = (
        "import sys;sys.dont_write_bytecode=True;import nexora.market_data.instruments;"
        "print('\\n'.join(sorted(sys.modules)))"
    )
    loaded = set(
        subprocess.run(
            [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=True
        ).stdout.split()
    )
    forbidden = (
        "MetaTrader5",
        "psutil",
        "fastapi",
        "nexora_api",
        "nexora.research",
        "nexora.pnf",
        "nexora.patterns",
        "nexora.features",
        "nexora.signals",
        "nexora.matrix",
        "nexora.risk",
        "nexora.paper",
        "nexora.experience",
        "nexora.entry_readiness",
        "socket",
    )
    leaked = sorted(m for m in loaded if any(m == f or m.startswith(f + ".") for f in forbidden))
    assert leaked == []


def test_resolver_never_produces_pnf_configuration() -> None:
    resolved, _ = _resolve()
    names = {f.name for f in dataclasses.fields(ResolvedFeed)} | {
        f.name
        for cls in (FeedBinding, PriceGrid, InstrumentDefinition)
        for f in dataclasses.fields(cls)
    }
    assert not names & {"box_size", "price_precision", "reversal", "precision"}
    assert "box" not in json.dumps(dataclasses.asdict(resolved), default=str)


def test_identity_objects_are_immutable() -> None:
    resolved, _ = _resolve()
    targets: list[tuple[object, str]] = [
        (resolved, "feed_id"),
        (resolved.binding, "symbol"),
        (resolved.binding.price_grid, "digits"),
        (resolved.instrument, "trade_contract_size"),
        (resolved.broker, "company"),
    ]
    for target, name in targets:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(target, name, None)
    assert isinstance(_config().bindings, tuple)


def test_errors_are_sanitized_codes() -> None:
    actions: tuple[Callable[[], object], ...] = (
        lambda: _resolve(terminal=_terminal(company="Secret Co", path=r"C:\Users\me\MT5")),
        lambda: _resolve(symbols={"XAUUSD": _info(currency_base="XAG")}),
        lambda: _resolve(mt5_path=r"C:\Users\me\private\terminal64.exe"),
    )
    for action in actions:
        with pytest.raises(InstrumentError) as caught:
            action()
        rendered = f"{caught.value} {caught.value.args} {caught.value.field}"
        for secret in ("Secret Co", "Users", "private", "XAG", "Broker A Ltd"):
            assert secret not in rendered


# --- legacy compatibility characterization (4f69e9c behaviour, no runtime change) ------------

LEGACY_QUOTE = {
    "ask": "101.0",
    "bid": "100.0",
    "digits": 1,
    "event_time": "2026-09-18T10:00:00Z",
    "raw_event_time": "2026-09-18T13:00:00Z",
    "received_at": "2026-09-18T10:00:00Z",
    "spread": "1.0",
    "symbol": "XAUUSD",
    "time_offset_seconds": 10800,
}
LEGACY_IDENTITY = "quote:e572a319739c1a854e2b5735ec850ef6f4d568af57c134c9a9fc8697915bdb1d"
LEGACY_STREAM = "research:881f69b7bdf3c0860975004bd86af7e7f2e6a604e4bed59af0cd2d0949aab9d3"
LEGACY_METADATA_HASH = "6c4cc7d5bbdc18d5d67877c8b628c7e0c6996da150b45fa7a1237409128b1b12"


def _legacy_pipeline_config() -> Any:
    """Same values as ``test_readiness_regressions.pipeline_config`` (fixes the stream id)."""
    from nexora.adaptive_box import AdaptiveBoxConfig
    from nexora.market_regime import RegimeConfig
    from nexora.pnf import PnfConfig
    from nexora.research import PipelineConfig, ResolutionConfig
    from nexora.signals import SignalConfig

    return PipelineConfig(
        version="audit-v2",
        resolutions=tuple(
            ResolutionConfig(
                name,
                PnfConfig("XAUUSD", Decimal(size), 2, 1, "close", f"{name}-v2"),
                AdaptiveBoxConfig(
                    "fixed",
                    Decimal(size),
                    1,
                    f"{name}-v2",
                    atr_period=2,
                    min_box_size=Decimal("0.1"),
                    max_box_size=Decimal("5"),
                ),
            )
            for name, size in (("fast", "0.5"), ("medium", "1"), ("slow", "2"))
        ),
        structure_resolution="fast",
        regime=RegimeConfig("XAUUSD", 4, Decimal("2"), Decimal("8"), Decimal("0.5"), "regime-v2"),
        signals=SignalConfig("XAUUSD", 1, 20, "signal-v2"),
        stale_after_events=100,
    )


def _legacy_quote() -> Any:
    from nexora_api.quotes import make_quote

    now = datetime(2026, 9, 18, 10, tzinfo=UTC)
    return make_quote(
        "XAUUSD",
        100,
        101,
        1,
        int((now + timedelta(hours=3)).timestamp() * 1000),
        now,
        time_offset_seconds=10800,
    )


def test_legacy_quote_and_snapshot_dumps_are_byte_stable() -> None:
    from nexora_api.quotes import Snapshot

    quote = _legacy_quote()
    assert quote.model_dump(mode="json") == LEGACY_QUOTE
    snapshot = Snapshot(
        stream_id="s-1", sequence=1, status="live", code="ok", symbol="XAUUSD", quote=quote
    ).model_dump(mode="json")
    assert snapshot == {
        "code": "ok",
        "quote": LEGACY_QUOTE,
        "schema_version": 1,
        "sequence": 1,
        "source": "MT5",
        "status": "live",
        "stream_id": "s-1",
        "symbol": "XAUUSD",
    }
    assert "feed" not in snapshot  # binding-only field must stay absent in legacy mode


def test_legacy_observation_provenance_and_streams_are_byte_stable(tmp_path: Path) -> None:
    from nexora.research.runtime import ResearchRuntime, RuntimeConfig
    from nexora.storage import SQLiteJournal
    from nexora_api.quotes import Snapshot
    from nexora_api.research import observe_quote

    cfg = _legacy_pipeline_config()
    cfg = replace(
        cfg,
        resolutions=tuple(
            replace(r, pnf=replace(r.pnf, price_source="bid")) for r in cfg.resolutions
        ),
    )
    journal = SQLiteJournal(tmp_path / "legacy.sqlite")
    runtime = ResearchRuntime(RuntimeConfig(cfg, "USD/oz"), journal)
    quote = _legacy_quote()
    snapshot = Snapshot(
        stream_id="s-1", sequence=1, status="live", code="ok", symbol="XAUUSD", quote=quote
    )
    metadata = {"quality": {"status": "complete"}, "status": snapshot.model_dump(mode="json")}
    observe_quote(runtime, quote, metadata=metadata)
    (event,) = runtime.events()
    assert event.identity_key == LEGACY_IDENTITY
    assert event.source == "MT5-quote-observation:time-offset=10800"
    assert event.source_event_id == (
        f"{LEGACY_IDENTITY}:raw-time=2026-09-18 13:00:00+00:00:offset=10800"
    )
    assert (event.symbol, event.precision) == ("XAUUSD", 1)
    assert runtime.stream == LEGACY_STREAM
    (row,) = journal.read(LEGACY_STREAM)
    assert sorted(row) == ["completeness", "event", "observation_metadata", "output"]
    assert canonical_hash(row["observation_metadata"]) == LEGACY_METADATA_HASH
    assert len(journal.read(LEGACY_STREAM + ":config")) == 1
    assert journal.read("feed:" + LEGACY_STREAM) == ()  # legacy mode never uses a feed prefix
    journal.close()
