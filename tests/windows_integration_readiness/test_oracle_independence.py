"""Finding B-M4: expectations that do not come from restating the extractor.

* The DST fold range is re-derived by brute-force simulation of a physical clock (UTC instants
  mapped to raw broker time), not from the `[boundary - drop, boundary + drop)` formula that the
  reference model and the extractor share.
* ORACLE-SPEC.md must classify every public reference-model function as RESTATED or SPECIFIED.
* The harness gate exposes no raw module (minor F8).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from tests.windows_integration_readiness import reference_model
from tests.windows_integration_readiness.fakes import FakeMt5Module
from tests.windows_integration_readiness.reference_model import (
    ReadinessError,
    ReadOnlyGate,
    derived_fold_ranges,
)
from tests.windows_integration_readiness.vectors import (
    ALLOWED_MT5_CALLS,
    EXPECTED_FOLD_FALLBACK,
    SEGMENTS_FALLBACK,
    T0,
    H,
)

SPEC = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "research"
    / "market-history-windows-readiness"
    / "ORACLE-SPEC.md"
)


def _repeated_raw(transition_utc: int, old_s: int, new_s: int, step: int) -> set[int]:
    """Simulate the broker clock around one transition: raw = utc + offset(utc). Return every raw
    value (on a `step` grid) produced by an instant before AND an instant after the transition."""
    span = 3 * H
    before = {u + old_s * 1000 for u in range(transition_utc - span, transition_utc, step)}
    after = {u + new_s * 1000 for u in range(transition_utc, transition_utc + span, step)}
    return before & after


@pytest.mark.parametrize("step", [60_000, 1_000])
def test_fold_range_equals_brute_force_union_over_both_boundary_readings(step: int) -> None:
    """A raw-keyed boundary B cannot say whether B is read on the old or the new clock, so the
    transition instant is either B - old or B - new. Simulate both and take the union."""
    (b0, _, old, _), (b1, _, new, _) = SEGMENTS_FALLBACK
    boundary = b1
    stretches = []
    for transition in (boundary - old * 1000, boundary - new * 1000):
        raw = _repeated_raw(transition, old, new, step)
        assert raw, "a falling offset must repeat some raw time"
        stretches.append((min(raw), max(raw) + step))
    lo, hi = min(a for a, _ in stretches), max(b for _, b in stretches)
    assert ((lo, hi),) == EXPECTED_FOLD_FALLBACK == derived_fold_ranges(SEGMENTS_FALLBACK)


def test_rising_offset_repeats_nothing_by_simulation() -> None:
    assert _repeated_raw(T0 + 100 * H, 7200, 10800, 60_000) == set()


def test_oracle_spec_classifies_every_public_reference_function() -> None:
    text = SPEC.read_text(encoding="utf-8")
    public = sorted(
        name
        for name, obj in vars(reference_model).items()
        if inspect.isfunction(obj) and not name.startswith("_") and obj.__module__.endswith("model")
    )
    for name in public:
        row = re.search(rf"^\| `{name}` \| (RESTATED|SPECIFIED|HARNESS) \|", text, flags=re.M)
        assert row, name


def test_gate_exposes_no_raw_module_through_any_attribute() -> None:
    mod = FakeMt5Module()
    gate = ReadOnlyGate(mod, ALLOWED_MT5_CALLS)
    for name in ("_m", "_target", "module", "__wrapped__", "_allowed", "__dict__"):
        with pytest.raises(ReadinessError):
            getattr(gate, name)
    fetch = object.__getattribute__(gate, "_fetch")
    with pytest.raises(ReadinessError):
        fetch("order_send")
    assert "order_send" not in mod.accessed
