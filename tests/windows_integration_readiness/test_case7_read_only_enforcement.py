"""MATRIX CASE 7 - read-only API enforcement, terminal cleanup, provenance gating."""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness.fakes import (
    TRIPWIRE_LOG,
    FakeMt5Module,
    RecordingModuleProxy,
    TripwireTouched,
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


@pytest.mark.parametrize("name", FORBIDDEN_MT5_NAMES)
def test_case7_every_forbidden_name_is_a_tripwire(name: str, expect_tripwires: list[str]) -> None:
    mod = FakeMt5Module()
    with pytest.raises(TripwireTouched):
        getattr(mod, name, None)  # a default does not hide it: not an AttributeError
    assert expect_tripwires == [name]


def test_case7_tripwire_is_not_swallowed_by_except_exception(expect_tripwires: list[str]) -> None:
    mod = FakeMt5Module()

    def careless() -> str:
        try:
            mod.order_send  # noqa: B018
        except Exception:  # what sloppy code under test might do
            return "swallowed"
        return "not reached"

    with pytest.raises(TripwireTouched):
        careless()
    assert expect_tripwires == ["order_send"]


def test_case7_tripwire_swallowed_by_base_exception_is_still_recorded(
    expect_tripwires: list[str],
) -> None:
    mod = FakeMt5Module()
    try:
        mod.positions_total  # noqa: B018
    except BaseException:  # noqa: S110 - deliberately hostile code under test
        pass
    # the conftest teardown would fail this test if the log were not consumed here
    assert TRIPWIRE_LOG == ["positions_total"] and expect_tripwires == ["positions_total"]


def test_case7_recording_proxy_records_call_arguments_without_credential_values() -> None:
    mod = FakeMt5Module()
    proxy = RecordingModuleProxy(mod)
    proxy.initialize("C:/terminal64.exe", login=1, password="synthetic-secret")
    assert proxy.calls == [("initialize", ("str",), ("login", "password"))]
    assert proxy.credential_kwargs_seen == ["login", "password"]
    assert "synthetic-secret" not in repr(proxy.calls) + repr(proxy.names)
    clean = RecordingModuleProxy(FakeMt5Module())
    clean.initialize()
    assert clean.calls == [("initialize", (), ())] and clean.credential_kwargs_seen == []


def test_case7_fake_copy_ticks_range_honours_requested_window() -> None:
    rows = make_fake_rows([(10, 1.1, 1.2, 6), (20, 1.1, 1.2, 6), (30, 1.1, 1.2, 6)])
    mod = FakeMt5Module(rows=rows)
    got = mod.copy_ticks_range("SYN", 15, 30, mod.COPY_TICKS_ALL)
    assert got is not None and [r["time_msc"] for r in got] == [20]


def test_case7_fake_models_chunk_failures_and_they_are_not_detected_by_hashes() -> None:
    """F10: per-chunk None, truncation, repeated pages and overlap. Truncation still yields a
    perfectly valid-looking stream: completeness stays NOT VERIFIED, only a count could hint."""
    rows = make_fake_rows([(t, 1.1, 1.2, 6) for t in range(0, 30)])
    mod = FakeMt5Module(rows=rows, script=["ok", "none", "truncate:2", "repeat_prev", "overlap:5"])
    chunks = [mod.copy_ticks_range("SYN", a, a + 10, -1) for a in (0, 10, 20, 0, 10)]
    assert chunks[1] is None
    assert chunks[2] is not None and [r["time_msc"] for r in chunks[2]] == [20, 21]
    assert chunks[3] == chunks[2]  # repeated page
    assert chunks[4] is not None and len(chunks[4]) == 20  # widened window
    assert [c[1][2] for c in mod.calls[-5:]] == [10, 20, 30, 10, 20]  # bounds recorded


def test_case7_harness_never_imports_the_extractor_or_real_mt5() -> None:
    import sys

    assert "MetaTrader5" not in sys.modules
    assert "nexora.market_history" not in sys.modules
