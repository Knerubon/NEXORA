"""Read-only MT5 symbol discovery for operators (ADR-025 §12). Never runtime identity.

Prints the ADR-025 §3/§6.1 fields of every symbol so an operator can hand-write a binding.
It never writes files or config, never calls ``symbol_select``/``account_info``/``login`` or any
order/position/history API, and never chooses a runtime symbol. Name similarity only orders
rows inside a semantic group and is labelled presentation-only.

Usage (operator, terminal already running; nothing is launched)::

    .venv/Scripts/python scripts/mt5_discover_symbols.py --path "C:\\...\\terminal64.exe" \
        [--instruments-config FILE --instrument ID] [--name-hint TEXT]
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from nexora.artifacts import canonical_serialize
from nexora.market_data.instruments import (
    BINDING_TIME_CONTRACT,
    InstrumentDefinition,
    ObservedSymbol,
    ObservedTerminal,
    observe_symbol,
    observe_terminal,
    parse_instruments_config,
)

INFORMATIONAL_FIELDS = ("description", "path", "exchange", "category", "basis", "isin")


class DiscoveryApi(Protocol):
    """The only MT5 calls the discovery core makes (ADR-025 §12 allowlist)."""

    def terminal_info(self) -> Any: ...
    def symbols_get(self) -> Any: ...
    def symbol_info(self, name: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: ObservedSymbol
    semantic_match: bool
    informational: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    terminal: ObservedTerminal | None
    declared: InstrumentDefinition | None
    candidates: tuple[Candidate, ...]  # exact semantic matches first; never a selection


def _semantic_match(declared: InstrumentDefinition | None, symbol: ObservedSymbol) -> bool:
    if declared is None:
        return False
    return (
        symbol.custom is False
        and symbol.currency_base == declared.currency_base
        and symbol.currency_profit == declared.currency_profit
        and symbol.trade_calc_mode == declared.trade_calc_mode
        and symbol.trade_contract_size == declared.trade_contract_size
        and symbol.chart_mode == declared.chart_mode
    )


def _informational(info: Any) -> tuple[tuple[str, str], ...]:
    rows = []
    for name in INFORMATIONAL_FIELDS:
        value = getattr(info, name, None)
        if isinstance(value, str) and value:
            rows.append((name, value))
    return tuple(rows)


def discover(
    api: DiscoveryApi,
    *,
    declared: InstrumentDefinition | None = None,
    name_hint: str = "",
) -> DiscoveryReport:
    """Collect candidates from an already initialized, injected MT5 API. Pure presentation."""
    terminal = observe_terminal(api.terminal_info())
    names: list[str] = []
    for entry in api.symbols_get() or ():
        name = getattr(entry, "name", None)
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    candidates: list[Candidate] = []
    for name in names:
        info = api.symbol_info(name)
        observed = observe_symbol(name, info)
        if observed is None:
            continue  # vanished between listing and lookup; nothing to present
        candidates.append(
            Candidate(observed, _semantic_match(declared, observed), _informational(info))
        )

    def order(candidate: Candidate) -> tuple[bool, float, str]:
        # Presentation-only: similarity never turns into identity or selection.
        similarity = (
            difflib.SequenceMatcher(None, name_hint, candidate.symbol.name).ratio()
            if name_hint
            else 0.0
        )
        return (not candidate.semantic_match, -similarity, candidate.symbol.name)

    return DiscoveryReport(terminal, declared, tuple(sorted(candidates, key=order)))


def _value(value: Any) -> str:
    if value is None:
        return "<unreadable>"
    if isinstance(value, Decimal):
        return str(canonical_serialize(value))
    return str(value)


def proposed_binding(declared: InstrumentDefinition, symbol: ObservedSymbol) -> dict[str, Any]:
    """A binding skeleton for the operator to review and copy by hand. Never applied."""
    tick = symbol.trade_tick_size
    return {
        "instrument_id": declared.instrument_id,
        "broker_id": "<operator broker_id>",
        "symbol": symbol.name,
        "price_grid": {
            "digits": symbol.digits,
            "point": _value(symbol.point),
            "trade_tick_size": None if tick is None or tick == 0 else _value(tick),
        },
        "time_offset_seconds": "<operator-declared offset; never auto-detected>",
    }


def format_report(report: DiscoveryReport) -> str:
    lines = ["MT5 symbol discovery (read-only; presentation only; nothing is selected or written)"]
    if report.terminal is not None:
        lines.append(
            f"terminal: connected={report.terminal.connected} "
            f"company={_value(report.terminal.company)} path={_value(report.terminal.path)}"
        )
    groups = (
        ("exact semantic match", True),
        ("other", False),
    )
    for title, matched in groups:
        rows = [c for c in report.candidates if c.semantic_match is matched]
        if report.declared is None and matched:
            continue
        lines.append(f"== {title} ({len(rows)}) — order within group is presentation-only")
        for candidate in rows:
            s = candidate.symbol
            lines.append(
                f"{s.name}: visible={_value(s.visible)} custom={_value(s.custom)} "
                f"currency_base={_value(s.currency_base)} "
                f"currency_profit={_value(s.currency_profit)} "
                f"trade_calc_mode={_value(s.trade_calc_mode)} "
                f"trade_contract_size={_value(s.trade_contract_size)} "
                f"chart_mode={_value(s.chart_mode)} digits={_value(s.digits)} "
                f"point={_value(s.point)} trade_tick_size={_value(s.trade_tick_size)}"
            )
            for name, text in candidate.informational:
                lines.append(f"    {name} (informational, never identity): {text}")
            if matched and report.declared is not None:
                binding = json.dumps(proposed_binding(report.declared, s), sort_keys=True)
                lines.append(f"    proposed binding (copy by hand after review): {binding}")
    lines.append(f"binding time contract: {BINDING_TIME_CONTRACT} (offset is operator-declared)")
    return "\n".join(lines)


def _terminal_running(psutil: Any, path: str) -> bool:
    target = os.path.normcase(os.path.abspath(path))
    return any(
        process.info.get("exe") and os.path.normcase(os.path.abspath(process.info["exe"])) == target
        for process in psutil.process_iter(["exe"])
    )


def _declared(config: Path | None, instrument_id: str | None) -> InstrumentDefinition | None:
    if config is None and instrument_id is None:
        return None
    if config is None or instrument_id is None:
        raise SystemExit("--instruments-config and --instrument must be given together")
    parsed = parse_instruments_config(config.read_text(encoding="utf-8"))
    for instrument in parsed.instruments:
        if instrument.instrument_id == instrument_id:
            return instrument
    raise SystemExit("instrument not declared in the given config")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", required=True, help="terminal64.exe of a running terminal")
    parser.add_argument("--instruments-config", type=Path)
    parser.add_argument("--instrument")
    parser.add_argument("--name-hint", default="", help="presentation-only ordering hint")
    args = parser.parse_args(argv)
    declared = _declared(args.instruments_config, args.instrument)
    try:
        psutil = importlib.import_module("psutil")
        mt5 = importlib.import_module("MetaTrader5")
    except ImportError:
        print("adapter_not_installed", file=sys.stderr)
        return 2
    if not _terminal_running(psutil, args.path):
        print("terminal_not_running (discovery never launches a terminal)", file=sys.stderr)
        return 2
    if not mt5.initialize(args.path, timeout=5000):
        print("connection_failed", file=sys.stderr)
        return 2
    try:
        report = discover(mt5, declared=declared, name_hint=args.name_hint)
    finally:
        mt5.shutdown()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
