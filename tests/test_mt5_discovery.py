"""ADR-025 §12 discovery tool: read-only presentation, never runtime identity (fake MT5 only)."""

from __future__ import annotations

import builtins
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from nexora.market_data.instruments import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mt5_discover_symbols.py"
EXAMPLE = ROOT / "config/instruments.example.json"
TERMINAL = r"C:\Path\To\terminal64.exe"

ALLOWED = {"terminal_info", "symbols_get", "symbol_info"}
MAIN_ONLY = {"initialize", "shutdown"}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mt5_discover_symbols", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


discovery = _load()


class TrapTerminal:
    connected = True
    company = "Example Company"
    path = r"C:\Path\To"

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"terminal_info().{name} is outside the allowlist")


def _symbol(name: str, **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "name": name,
        "visible": True,
        "custom": False,
        "currency_base": "EXA",
        "currency_profit": "USD",
        "trade_calc_mode": 0,
        "trade_contract_size": 100.0,
        "chart_mode": 0,
        "digits": 2,
        "point": 0.01,
        "trade_tick_size": 0.01,
        "description": f"{name} description",
        "path": f"Metals\\{name}",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TrapMt5:
    """Fake MetaTrader5: any call outside the discovery allowlist fails the test."""

    def __init__(self, symbols: dict[str, SimpleNamespace], *, allow_main: bool = False) -> None:
        self._symbols = symbols
        self._allowed = ALLOWED | (MAIN_ONLY if allow_main else set())
        self.calls: list[str] = []

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_") or name in {"calls"}:
            return object.__getattribute__(self, name)
        if name not in object.__getattribute__(self, "_allowed"):
            raise AssertionError(f"forbidden MT5 call: {name}")
        object.__getattribute__(self, "calls").append(name)
        return object.__getattribute__(self, "_" + name)

    def _terminal_info(self) -> TrapTerminal:
        return TrapTerminal()

    def _symbols_get(self) -> tuple[SimpleNamespace, ...]:
        return tuple(self._symbols.values())

    def _symbol_info(self, name: str) -> SimpleNamespace | None:
        return self._symbols.get(name)

    def _initialize(self, path: str, timeout: int) -> bool:
        return path == TERMINAL

    def _shutdown(self) -> None:
        return None


DECLARED = InstrumentDefinition("EXAMPLE-INSTRUMENT", "EXA", "USD", 0, Decimal("100"), 0)
SYMBOLS = {
    "EXAMPLE_SYMBOL": _symbol("EXAMPLE_SYMBOL"),
    "EXAMPLE_SYMBOL.a": _symbol("EXAMPLE_SYMBOL.a"),
    "EXAMPLE_SYMBOL-MINI": _symbol("EXAMPLE_SYMBOL-MINI", trade_contract_size=10.0),
    "OTHER": _symbol("OTHER", currency_base="OTH"),
    "CUSTOM": _symbol("CUSTOM", custom=True),
}


def test_lists_fake_symbols_with_semantic_metadata() -> None:
    api = TrapMt5(SYMBOLS)
    report = discovery.discover(api)
    assert {c.symbol.name for c in report.candidates} == set(SYMBOLS)
    text = discovery.format_report(report)
    for field in (
        "currency_base=EXA",
        "trade_contract_size=100",
        "digits=2",
        "point=0.01",
        "trade_tick_size=0.01",
        "chart_mode=0",
        "visible=True",
    ):
        assert field in text
    assert "(informational, never identity)" in text
    assert set(api.calls) <= ALLOWED


def test_semantic_filtering_groups_exact_matches_first() -> None:
    report = discovery.discover(TrapMt5(SYMBOLS), declared=DECLARED)
    matched = [c.symbol.name for c in report.candidates if c.semantic_match]
    assert set(matched) == {"EXAMPLE_SYMBOL", "EXAMPLE_SYMBOL.a"}
    # contract size, currency and custom differences are never "similar enough"
    others = {c.symbol.name for c in report.candidates if not c.semantic_match}
    assert others == {"EXAMPLE_SYMBOL-MINI", "OTHER", "CUSTOM"}
    assert [c.semantic_match for c in report.candidates] == sorted(
        (c.semantic_match for c in report.candidates), reverse=True
    )


def test_name_similarity_orders_within_a_group_only() -> None:
    hinted = discovery.discover(TrapMt5(SYMBOLS), declared=DECLARED, name_hint="OTHER")
    assert hinted.candidates[0].semantic_match  # a hint never lifts a non-match above matches
    other_group = [c.symbol.name for c in hinted.candidates if not c.semantic_match]
    assert other_group[0] == "OTHER"
    assert "presentation-only" in discovery.format_report(hinted)


def test_candidates_are_presented_never_selected() -> None:
    report = discovery.discover(TrapMt5(SYMBOLS), declared=DECLARED)
    text = discovery.format_report(report)
    proposals = [line for line in text.splitlines() if "proposed binding" in line]
    assert len(proposals) == 2  # every exact match proposed; none chosen
    binding = json.loads(proposals[0].split(": ", 1)[1])
    assert binding["broker_id"] == "<operator broker_id>"
    assert "never auto-detected" in binding["time_offset_seconds"]
    assert not hasattr(report, "selected") and not hasattr(report, "binding")


def test_missing_metadata_is_reported_safely() -> None:
    broken = _symbol("BROKEN")
    del broken.trade_tick_size
    broken.point = float("nan")
    symbols = {"BROKEN": broken, "GONE": SimpleNamespace(name="GONE")}
    api = TrapMt5(symbols)
    api._symbols = {"BROKEN": broken}  # GONE disappears between list and lookup
    api._symbols_get = lambda: tuple(symbols.values())  # type: ignore[method-assign]
    report = discovery.discover(api, declared=DECLARED)
    assert [c.symbol.name for c in report.candidates] == ["BROKEN"]
    text = discovery.format_report(report)
    assert "point=<unreadable>" in text and "trade_tick_size=<unreadable>" in text


def test_discover_core_never_initializes_or_calls_forbidden_apis() -> None:
    api = TrapMt5(SYMBOLS)  # initialize/shutdown not allowed here
    discovery.discover(api, declared=DECLARED)
    assert set(api.calls) <= ALLOWED
    for name in (
        "initialize",
        "symbol_select",
        "account_info",
        "login",
        "order_send",
        "positions_get",
        "orders_get",
        "history_deals_get",
        "market_book_add",
    ):
        with pytest.raises(AssertionError):
            getattr(api, name)


def test_output_contains_no_account_fields() -> None:
    text = discovery.format_report(discovery.discover(TrapMt5(SYMBOLS), declared=DECLARED))
    lowered = text.lower()
    for word in ("login", "balance", "equity", "server", "password", "account"):
        assert word not in lowered


class FakePsutil:
    def __init__(self, exe: str | None) -> None:
        self.exe = exe

    def process_iter(self, attrs: list[str]) -> list[SimpleNamespace]:
        return [SimpleNamespace(info={"exe": self.exe})]


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: TrapMt5,
    psutil: FakePsutil,
    argv: list[str],
) -> tuple[int, str]:
    modules = {"psutil": psutil, "MetaTrader5": api}
    monkeypatch.setattr(discovery.importlib, "import_module", lambda name: modules[name])
    real_open = builtins.open

    def read_only_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            raise AssertionError("discovery must never write files")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", read_only_open)
    code = discovery.main(argv)
    return code, capsys.readouterr().out


def test_main_is_read_only_and_never_writes_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    api = TrapMt5(SYMBOLS, allow_main=True)
    code, out = _run_main(
        monkeypatch,
        capsys,
        api,
        FakePsutil(TERMINAL),
        [
            "--path",
            TERMINAL,
            "--instruments-config",
            str(EXAMPLE),
            "--instrument",
            "EXAMPLE-INSTRUMENT",
        ],
    )
    assert code == 0
    assert api.calls[0] == "initialize" and api.calls[-1] == "shutdown"
    assert set(api.calls) <= ALLOWED | MAIN_ONLY
    assert "proposed binding" in out
    assert list(tmp_path.iterdir()) == []


def test_main_never_launches_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = TrapMt5(SYMBOLS)  # initialize would raise: it must not be reached
    code, _ = _run_main(monkeypatch, capsys, api, FakePsutil(None), ["--path", TERMINAL])
    assert code == 2
    assert api.calls == []


def test_script_source_has_no_forbidden_calls() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in (
        "symbol_select(",
        "account_info(",
        "login(",
        "order_",
        "positions_",
        "history_",
        "write_text",
        "os.environ",
        "getenv",
    ):
        assert token not in source, token
