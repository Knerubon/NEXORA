"""MT5 multi-broker instrument resolution V1, pure core (ADR-025 Phase 2A).

Operator-declared contracts, strict ``NEXORA_INSTRUMENTS_CONFIG`` decoding, the exact-symbol
resolver and the deterministic ``feed_id``. The resolver **validates declarations; it never
infers identity**: MT5 terminal/symbol reads are injected as plain observed values, so this
module imports no MT5 package and reads no environment, file, clock or network.

Q-Q1 boundary (ADR-025 §7): the price grid is represented, validated against the declared
binding and hashed into ``feed_id``. Nothing here derives, rounds or checks P&F
``box_size``/``price_precision``.
"""

from __future__ import annotations

import json
import ntpath
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from nexora.artifacts import canonical_hash

INSTRUMENTS_CONFIG_SCHEMA_VERSION = 1
FEED_ID_PREFIX = "mt5f1-"
FEED_CONTRACT = "adr025-feed-v1"
BINDING_TIME_CONTRACT = "adr025-binding-v1"
MAX_TIME_OFFSET_SECONDS = 50400  # same range as ADR-019
MAX_DIGITS = 10

InstrumentReason = Literal[
    "not_configured",
    "instrument_config_invalid",
    "feed_mode_conflict",
    "time_offset_conflict",
    "broker_not_bound",
    "broker_ambiguous",
    "broker_identity_mismatch",
    "symbol_not_found",
    "symbol_not_visible",
    "instrument_metadata_mismatch",
    "feed_identity_changed",
    "terminal_disconnected",
]
FeedMode = Literal["binding", "legacy"]
IdentityComparison = Literal["unchanged", "feed_identity_changed"]

_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_DECIMAL = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
# Closed schema already rejects every unknown key; these names get an explicit detail so a
# secret-looking field is never mistaken for a typo.
_CREDENTIAL_KEYS = frozenset(
    {
        "login",
        "password",
        "passwd",
        "server",
        "account",
        "account_id",
        "investor",
        "token",
        "secret",
    }
)


class InstrumentError(ValueError):
    """Fail-closed resolution/config error.

    ``str()`` is the reason code only (ADR-025 §11 sanitization). ``field`` names the schema
    field or check that failed, never a value, path, company string or account datum.
    """

    def __init__(self, code: InstrumentReason, field: str = "") -> None:
        super().__init__(code)
        self.code: InstrumentReason = code
        self.field = field

    def __str__(self) -> str:
        return self.code


# --- declared contracts (ADR-025 §3, §4.1, §6.1) ------------------------------------------


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    instrument_id: str
    currency_base: str
    currency_profit: str
    trade_calc_mode: int
    trade_contract_size: Decimal
    chart_mode: int
    description: str = ""  # informational only; never matched or hashed


@dataclass(frozen=True, slots=True)
class BrokerIdentity:
    broker_id: str
    company: str
    terminal_path: str  # verification only; never part of feed_id


@dataclass(frozen=True, slots=True)
class PriceGrid:
    digits: int
    point: Decimal
    trade_tick_size: Decimal | None  # None = observed value must be 0 (not provided)


@dataclass(frozen=True, slots=True)
class FeedBinding:
    instrument_id: str
    broker_id: str
    symbol: str  # exact broker symbol; case-sensitive, never normalized
    price_grid: PriceGrid
    time_offset_seconds: int


@dataclass(frozen=True, slots=True)
class InstrumentsConfig:
    schema_version: int
    instruments: tuple[InstrumentDefinition, ...]
    brokers: tuple[BrokerIdentity, ...]
    bindings: tuple[FeedBinding, ...]


# --- observed read-only metadata (injected; ADR-003 Amendment 1 allowlist) -----------------


@dataclass(frozen=True, slots=True)
class ObservedTerminal:
    """Only ``terminal_info().connected``, ``.company`` and ``.path``. ``None`` = unreadable."""

    connected: bool
    company: str | None
    path: str | None


@dataclass(frozen=True, slots=True)
class ObservedSymbol:
    """``symbol_info(<exact symbol>)`` fields used by ADR-025. ``None`` = unreadable."""

    name: str
    visible: bool | None
    custom: bool | None
    currency_base: str | None
    currency_profit: str | None
    trade_calc_mode: int | None
    trade_contract_size: Decimal | None
    chart_mode: int | None
    digits: int | None
    point: Decimal | None
    trade_tick_size: Decimal | None


@dataclass(frozen=True, slots=True)
class ResolvedFeed:
    instrument: InstrumentDefinition
    broker: BrokerIdentity
    binding: FeedBinding
    feed_id: str

    @property
    def time_contract(self) -> tuple[str, int]:
        return BINDING_TIME_CONTRACT, self.binding.time_offset_seconds


# --- observed-value conversion (ADR-025 §6.2) ----------------------------------------------


def observed_decimal(value: Any) -> Decimal | None:
    """MT5 floats/ints via ``Decimal(str(value))`` (shortest round-trip repr)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _observed_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _observed_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _observed_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def observe_terminal(info: Any) -> ObservedTerminal | None:
    """Read exactly the three allowlisted ``terminal_info()`` fields."""
    if info is None:
        return None
    return ObservedTerminal(
        connected=getattr(info, "connected", False) is True,
        company=_observed_str(getattr(info, "company", None)),
        path=_observed_str(getattr(info, "path", None)),
    )


def observe_symbol(name: str, info: Any) -> ObservedSymbol | None:
    """Read the ADR-025 §3/§6.1 fields of one ``symbol_info(name)`` result."""
    if info is None:
        return None
    return ObservedSymbol(
        name=name,
        visible=_observed_bool(getattr(info, "visible", None)),
        custom=_observed_bool(getattr(info, "custom", None)),
        currency_base=_observed_str(getattr(info, "currency_base", None)),
        currency_profit=_observed_str(getattr(info, "currency_profit", None)),
        trade_calc_mode=_observed_int(getattr(info, "trade_calc_mode", None)),
        trade_contract_size=observed_decimal(getattr(info, "trade_contract_size", None)),
        chart_mode=_observed_int(getattr(info, "chart_mode", None)),
        digits=_observed_int(getattr(info, "digits", None)),
        point=observed_decimal(getattr(info, "point", None)),
        trade_tick_size=observed_decimal(getattr(info, "trade_tick_size", None)),
    )


# --- strict config decoding (ADR-025 §13) --------------------------------------------------


def _invalid(field: str) -> InstrumentError:
    return InstrumentError("instrument_config_invalid", field)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise _invalid("non_finite_number")


def _reject_float(_: str) -> Any:
    raise _invalid("json_float")  # decimals are strings; floats would make identity unstable


def _object(value: Any, where: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(where)
    keys = set(value)
    if keys & _CREDENTIAL_KEYS:
        raise _invalid(f"{where}.credential_field")
    if keys - required - optional or required - keys:
        raise _invalid(where)
    return cast(dict[str, Any], value)


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise _invalid(where)
    return value


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise _invalid(where)
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(where)
    return value


def _int(value: Any, where: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise _invalid(where)
    return value


def _positive_decimal(value: Any, where: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise _invalid(where)
    result = Decimal(value)
    if result <= 0:
        raise _invalid(where)
    return result


def _terminal_path(value: Any, where: str) -> str:
    path = _text(value, where)
    if not ntpath.isabs(path) or not ntpath.basename(path):
        raise _invalid(where)
    return path


def normalize_terminal_path(path: str) -> str:
    """Windows path comparison (ADR-025 §4.1): case-folded and normalized, no cwd lookup."""
    return ntpath.normcase(ntpath.normpath(path))


def _instrument(value: Any, index: int) -> InstrumentDefinition:
    where = f"instruments[{index}]"
    item = _object(
        value,
        where,
        {
            "instrument_id",
            "currency_base",
            "currency_profit",
            "trade_calc_mode",
            "trade_contract_size",
            "chart_mode",
        },
        {"description"},
    )
    description = item.get("description", "")
    if not isinstance(description, str):
        raise _invalid(f"{where}.description")
    return InstrumentDefinition(
        instrument_id=_id(item["instrument_id"], f"{where}.instrument_id"),
        currency_base=_text(item["currency_base"], f"{where}.currency_base"),
        currency_profit=_text(item["currency_profit"], f"{where}.currency_profit"),
        trade_calc_mode=_int(item["trade_calc_mode"], f"{where}.trade_calc_mode", 0, 2**31),
        trade_contract_size=_positive_decimal(
            item["trade_contract_size"], f"{where}.trade_contract_size"
        ),
        chart_mode=_int(item["chart_mode"], f"{where}.chart_mode", 0, 2**31),
        description=description,
    )


def _broker(value: Any, index: int) -> BrokerIdentity:
    where = f"brokers[{index}]"
    item = _object(value, where, {"broker_id", "company", "terminal_path"}, set())
    return BrokerIdentity(
        broker_id=_id(item["broker_id"], f"{where}.broker_id"),
        company=_text(item["company"], f"{where}.company"),
        terminal_path=_terminal_path(item["terminal_path"], f"{where}.terminal_path"),
    )


def _binding(value: Any, index: int) -> FeedBinding:
    where = f"bindings[{index}]"
    item = _object(
        value,
        where,
        {"instrument_id", "broker_id", "symbol", "price_grid", "time_offset_seconds"},
        set(),
    )
    grid = _object(
        item["price_grid"], f"{where}.price_grid", {"digits", "point", "trade_tick_size"}, set()
    )
    tick = grid["trade_tick_size"]
    return FeedBinding(
        instrument_id=_id(item["instrument_id"], f"{where}.instrument_id"),
        broker_id=_id(item["broker_id"], f"{where}.broker_id"),
        symbol=_text(item["symbol"], f"{where}.symbol"),
        price_grid=PriceGrid(
            digits=_int(grid["digits"], f"{where}.price_grid.digits", 0, MAX_DIGITS),
            point=_positive_decimal(grid["point"], f"{where}.price_grid.point"),
            trade_tick_size=(
                None
                if tick is None
                else _positive_decimal(tick, f"{where}.price_grid.trade_tick_size")
            ),
        ),
        time_offset_seconds=_int(
            item["time_offset_seconds"],
            f"{where}.time_offset_seconds",
            -MAX_TIME_OFFSET_SECONDS,
            MAX_TIME_OFFSET_SECONDS,
        ),
    )


def _unique(values: Iterable[Any], field: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise _invalid(field)
        seen.add(value)


def validate_instruments_config(config: InstrumentsConfig) -> InstrumentsConfig:
    """Cross-reference rules of ADR-025 §11 ``instrument_config_invalid``."""
    _unique((i.instrument_id for i in config.instruments), "duplicate_instrument_id")
    _unique((b.broker_id for b in config.brokers), "duplicate_broker_id")
    _unique(
        ((b.company, normalize_terminal_path(b.terminal_path)) for b in config.brokers),
        "indistinguishable_brokers",
    )
    _unique(((b.broker_id, b.symbol) for b in config.bindings), "duplicate_broker_symbol")
    _unique(((b.instrument_id, b.broker_id) for b in config.bindings), "duplicate_binding")
    instruments = {i.instrument_id for i in config.instruments}
    brokers = {b.broker_id for b in config.brokers}
    for binding in config.bindings:
        if binding.instrument_id not in instruments or binding.broker_id not in brokers:
            raise _invalid("dangling_reference")
    return config


def decode_instruments_config(payload: Mapping[str, Any]) -> InstrumentsConfig:
    root = _object(
        dict(payload), "config", {"schema_version", "instruments", "brokers", "bindings"}, set()
    )
    version = root["schema_version"]
    if type(version) is not int or version != INSTRUMENTS_CONFIG_SCHEMA_VERSION:
        raise _invalid("schema_version")
    config = InstrumentsConfig(
        schema_version=version,
        instruments=tuple(
            _instrument(item, index)
            for index, item in enumerate(_array(root["instruments"], "instruments"))
        ),
        brokers=tuple(
            _broker(item, index) for index, item in enumerate(_array(root["brokers"], "brokers"))
        ),
        bindings=tuple(
            _binding(item, index) for index, item in enumerate(_array(root["bindings"], "bindings"))
        ),
    )
    return validate_instruments_config(config)


def parse_instruments_config(text: str) -> InstrumentsConfig:
    """Strict JSON: duplicate keys, NaN/Infinity and JSON floats are rejected."""
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except InstrumentError:
        raise
    except ValueError:
        raise _invalid("undecodable_json") from None
    if not isinstance(payload, dict):
        raise _invalid("config")
    return decode_instruments_config(payload)


# --- mode selection (ADR-025 §8, §9) -------------------------------------------------------


def select_feed_mode(
    *,
    instruments_config: bool,
    instrument: bool,
    symbol: bool,
    legacy_time_offset: bool,
) -> FeedMode:
    """Pure §9 matrix over *whether* each setting is present; the caller reads no values here."""
    if instruments_config and instrument and not symbol:
        if legacy_time_offset:
            raise InstrumentError("time_offset_conflict")
        return "binding"
    if (instruments_config and symbol) or (not instruments_config and instrument):
        raise InstrumentError("feed_mode_conflict")
    if instruments_config:
        raise InstrumentError("not_configured")  # config set, instrument unset
    if symbol:
        return "legacy"
    raise InstrumentError("not_configured")


# --- feed identity (ADR-025 §6.3, §6.4) ----------------------------------------------------


def feed_identity_payload(
    instrument: InstrumentDefinition, broker: BrokerIdentity, binding: FeedBinding
) -> dict[str, Any]:
    """Exactly the identity-bearing fields of §6.3, from declared values."""
    return {
        "contract": FEED_CONTRACT,
        "instrument": {
            "instrument_id": instrument.instrument_id,
            "currency_base": instrument.currency_base,
            "currency_profit": instrument.currency_profit,
            "trade_calc_mode": instrument.trade_calc_mode,
            "trade_contract_size": instrument.trade_contract_size,
            "chart_mode": instrument.chart_mode,
        },
        "broker": {"broker_id": broker.broker_id, "company": broker.company},
        "symbol": binding.symbol,
        "price_grid": {
            "digits": binding.price_grid.digits,
            "point": binding.price_grid.point,
            "trade_tick_size": binding.price_grid.trade_tick_size,
        },
        "time_contract": {
            "version": BINDING_TIME_CONTRACT,
            "offset_seconds": binding.time_offset_seconds,
        },
    }


def feed_id(instrument: InstrumentDefinition, broker: BrokerIdentity, binding: FeedBinding) -> str:
    return FEED_ID_PREFIX + canonical_hash(feed_identity_payload(instrument, broker, binding))


def compare_feed_identity(pinned: str, current: str) -> IdentityComparison:
    """Reconnect check: any difference is ``feed_identity_changed`` (latching is runtime's job)."""
    return "unchanged" if pinned == current else "feed_identity_changed"


def legacy_session_fingerprint(
    company: str, observed: ObservedSymbol, time_offset_seconds: int
) -> str:
    """In-memory legacy reconnect pin (§6.4); never journaled or exposed."""
    fields = (
        observed.currency_base,
        observed.currency_profit,
        observed.trade_calc_mode,
        observed.trade_contract_size,
        observed.chart_mode,
        observed.digits,
        observed.point,
        observed.trade_tick_size,
    )
    if any(value is None for value in fields):
        raise InstrumentError("instrument_metadata_mismatch", "unreadable_metadata")
    return canonical_hash((company, observed.name, fields, time_offset_seconds))


# --- pure resolver (ADR-025 §4.1, §5, §6.1) ------------------------------------------------


def _select_one[T](candidates: list[T]) -> T:
    if not candidates:
        raise InstrumentError("broker_not_bound")
    if len(candidates) > 1:
        raise InstrumentError("broker_ambiguous")
    return candidates[0]


def _check_broker(broker: BrokerIdentity, terminal: ObservedTerminal | None) -> None:
    if terminal is None or not terminal.connected:
        raise InstrumentError("terminal_disconnected")
    if terminal.path is None or normalize_terminal_path(terminal.path) != normalize_terminal_path(
        ntpath.dirname(broker.terminal_path)
    ):
        raise InstrumentError("broker_identity_mismatch", "terminal_path")
    if terminal.company is None or terminal.company != broker.company:
        raise InstrumentError("broker_identity_mismatch", "company")


def _check_symbol(
    instrument: InstrumentDefinition, binding: FeedBinding, observed: ObservedSymbol | None
) -> None:
    if observed is None:
        raise InstrumentError("symbol_not_found")
    if observed.name != binding.symbol:
        raise InstrumentError("symbol_not_found")  # an injected lookup must never substitute
    if observed.visible is not True:
        raise InstrumentError("symbol_not_visible")
    if observed.custom is not False:
        raise InstrumentError("instrument_metadata_mismatch", "custom")
    semantic: tuple[tuple[str, Any, Any], ...] = (
        ("currency_base", observed.currency_base, instrument.currency_base),
        ("currency_profit", observed.currency_profit, instrument.currency_profit),
        ("trade_calc_mode", observed.trade_calc_mode, instrument.trade_calc_mode),
        ("trade_contract_size", observed.trade_contract_size, instrument.trade_contract_size),
        ("chart_mode", observed.chart_mode, instrument.chart_mode),
    )
    grid = binding.price_grid
    expected_tick = Decimal(0) if grid.trade_tick_size is None else grid.trade_tick_size
    price_grid: tuple[tuple[str, Any, Any], ...] = (
        ("digits", observed.digits, grid.digits),
        ("point", observed.point, grid.point),
        ("trade_tick_size", observed.trade_tick_size, expected_tick),
    )
    for name, actual, expected in (*semantic, *price_grid):
        if actual is None or actual != expected:
            raise InstrumentError("instrument_metadata_mismatch", name)


def resolve_feed(
    config: InstrumentsConfig,
    *,
    instrument_id: str,
    mt5_path: str,
    terminal: ObservedTerminal | None,
    symbol_info: Callable[[str], ObservedSymbol | None],
) -> ResolvedFeed:
    """Resolve one binding-mode feed or fail closed (ADR-025 §4.1 → §5 → §6.1 → §6.3).

    ``symbol_info`` is called at most once, with the exact bound symbol. There is no
    enumeration, similarity, prefix/suffix or first-visible fallback.
    """
    if not instrument_id or not mt5_path:
        raise InstrumentError("not_configured")
    target = normalize_terminal_path(mt5_path)
    broker = _select_one(
        [b for b in config.brokers if normalize_terminal_path(b.terminal_path) == target]
    )
    _check_broker(broker, terminal)
    binding = _select_one(
        [
            b
            for b in config.bindings
            if b.instrument_id == instrument_id and b.broker_id == broker.broker_id
        ]
    )
    instrument = _select_one([i for i in config.instruments if i.instrument_id == instrument_id])
    _check_symbol(instrument, binding, symbol_info(binding.symbol))
    return ResolvedFeed(
        instrument=instrument,
        broker=broker,
        binding=binding,
        feed_id=feed_id(instrument, broker, binding),
    )
