"""MATRIX CASE 7 - read-only API enforcement, terminal cleanup, provenance gating."""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness.fakes import (
    FakeMt5Module,
    RecordingModuleProxy,
    make_fake_rows,
)
from tests.windows_integration_readiness.reference_model import (
    ReadinessError,
    ReadOnlyGate,
    run_with_cleanup,
)
from tests.windows_integration_readiness.vectors import ALLOWED_MT5_CALLS, FORBIDDEN_MT5_NAMES


def _gate(mod: FakeMt5Module) -> ReadOnlyGate:
    return ReadOnlyGate(mod, ALLOWED_MT5_CALLS)


@pytest.mark.parametrize("name", FORBIDDEN_MT5_NAMES)
def test_case7_forbidden_names_refused_and_never_reach_module(name: str) -> None:
    mod = FakeMt5Module()
    with pytest.raises(ReadinessError) as e:
        getattr(_gate(mod), name)
    assert e.value.code == "mt5_call_not_allowlisted"
    assert name not in mod.accessed


def test_case7_allowlist_contains_no_trade_or_account_name() -> None:
    assert not (set(FORBIDDEN_MT5_NAMES) & ALLOWED_MT5_CALLS)
    banned_words = ("order", "position", "deal", "account", "login", "book")
    assert not [n for n in ALLOWED_MT5_CALLS if any(w in n for w in banned_words)]


def test_case7_full_read_path_touches_only_allowlisted_names() -> None:
    mod = FakeMt5Module(rows=make_fake_rows([(1, 1.1, 1.2, 6)]))

    def body(g: ReadOnlyGate) -> int:
        g.version()
        g.terminal_info()
        g.symbol_info("SYN")
        return len(g.copy_ticks_range("SYN", None, None, g.COPY_TICKS_ALL))

    assert run_with_cleanup(_gate(mod), body) == 1
    assert set(mod.accessed) <= ALLOWED_MT5_CALLS


def test_case7_initialize_called_without_credentials() -> None:
    mod = FakeMt5Module()
    run_with_cleanup(_gate(mod), lambda g: None)
    assert mod.calls[0] == ("initialize", (), ())


def test_case7_shutdown_on_success_exception_and_failed_initialize() -> None:
    ok = FakeMt5Module()
    run_with_cleanup(_gate(ok), lambda g: None)
    assert ok.calls[-1][0] == "shutdown"

    boom = FakeMt5Module()

    def explode(g: ReadOnlyGate) -> None:
        raise RuntimeError("synthetic")

    with pytest.raises(RuntimeError):
        run_with_cleanup(_gate(boom), explode)
    assert boom.calls[-1][0] == "shutdown"

    bad = FakeMt5Module(init_ok=False)
    with pytest.raises(ReadinessError):
        run_with_cleanup(_gate(bad), lambda g: None)
    # failed initialize: documented behaviour is shutdown NOT required; assert no trade call.
    assert set(bad.accessed) <= ALLOWED_MT5_CALLS | {"calls"}


def test_case7_gate_is_immutable_and_exposes_no_raw_module_by_public_name() -> None:
    g = _gate(FakeMt5Module())
    with pytest.raises(ReadinessError):
        g.order_send = lambda *a: None
    with pytest.raises(ReadinessError):
        g.module  # noqa: B018


def test_case7_recording_proxy_turns_read_only_into_an_observed_set() -> None:
    """The same proxy wraps the REAL MetaTrader5 module on the Windows node; the recorded
    names are the evidence attached to the record."""
    mod = FakeMt5Module(rows=[])
    proxy = RecordingModuleProxy(mod)
    gate = ReadOnlyGate(proxy, ALLOWED_MT5_CALLS)
    run_with_cleanup(gate, lambda g: g.copy_ticks_range("SYN", None, None, g.COPY_TICKS_ALL))
    assert set(proxy.names) <= ALLOWED_MT5_CALLS
    assert {"initialize", "copy_ticks_range", "shutdown"} <= set(proxy.names)
    with pytest.raises(AttributeError):
        proxy.names = []


def test_case7_tripwire_attributes_fail_loudly_if_touched_directly() -> None:
    mod = FakeMt5Module()
    with pytest.raises(AssertionError):
        mod.order_send  # noqa: B018


def test_case7_harness_never_imports_the_extractor_or_real_mt5() -> None:
    import sys

    assert "MetaTrader5" not in sys.modules
    assert "nexora.market_history" not in sys.modules
