"""MATRIX CASE 1 - copy_ticks_range return structure and numpy field dtypes (mock half)."""

from __future__ import annotations

import pytest

from tests.windows_integration_readiness.fakes import FakeMt5Module, make_fake_rows
from tests.windows_integration_readiness.reference_model import check_tick_layout
from tests.windows_integration_readiness.vectors import (
    DOCUMENTED_TICK_DESCR,
    REQUIRED_TICK_FIELDS,
)


def test_case1_documented_layout_is_acceptable() -> None:
    assert check_tick_layout(DOCUMENTED_TICK_DESCR, REQUIRED_TICK_FIELDS) == []


@pytest.mark.parametrize("missing", ["time_msc", "bid", "ask"])
def test_case1_missing_required_field_is_reported(missing: str) -> None:
    descr = [(n, t) for n, t in DOCUMENTED_TICK_DESCR if n != missing]
    assert f"missing_field:{missing}" in check_tick_layout(descr, REQUIRED_TICK_FIELDS)


def test_case1_wrong_dtypes_are_reported() -> None:
    bad = [("time_msc", "<i4"), ("bid", "<f4"), ("ask", "<f8")]
    problems = check_tick_layout(bad, REQUIRED_TICK_FIELDS)
    assert any(p.startswith("time_msc_not_64bit_int") for p in problems)
    assert any(p.startswith("bid_not_float64") for p in problems)


def test_case1_absent_flags_is_noted_not_fatal() -> None:
    descr = [(n, t) for n, t in DOCUMENTED_TICK_DESCR if n != "flags"]
    assert check_tick_layout(descr, REQUIRED_TICK_FIELDS) == ["note_flags_absent"]


def test_case1_fake_rows_expose_only_the_access_pattern_the_extractor_uses() -> None:
    rows = make_fake_rows([(1_700_000_000_123, 1.1, 1.2, 6)])
    assert rows[0]["time_msc"] == 1_700_000_000_123
    assert tuple(rows[0].dtype.names) == tuple(n for n, _ in DOCUMENTED_TICK_DESCR)
    no_flags = make_fake_rows([(1, 1.1, 1.2, 6)], drop_fields=("flags",))
    assert "flags" not in no_flags[0].dtype.names


def test_case1_none_return_models_terminal_failure() -> None:
    mt5 = FakeMt5Module(rows=None)
    assert mt5.copy_ticks_range("SYN", None, None, mt5.COPY_TICKS_ALL) is None


def test_case1_numpy_dtype_descr_matches_documented_layout() -> None:
    np = pytest.importorskip(
        "numpy", reason="numpy is not in the locked cloud env (MetaTrader5 wheel is win32-only)"
    )
    dt = np.dtype([(n, t) for n, t in DOCUMENTED_TICK_DESCR])
    assert [tuple(d) for d in dt.descr] == list(DOCUMENTED_TICK_DESCR)
    arr = np.zeros(2, dtype=dt)
    assert arr.dtype.names is not None and "time_msc" in arr.dtype.names
    assert check_tick_layout(arr.dtype.descr, REQUIRED_TICK_FIELDS) == []
