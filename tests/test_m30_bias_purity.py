"""ADR-026 Phase 2A: the pure core performs no MT5, filesystem, network or wall-clock access."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "packages" / "nexora" / "m30_bias"

ALLOWED_IMPORTS = {
    "__future__",
    "collections",
    "collections.abc",
    "dataclasses",
    "datetime",
    "decimal",
    "hashlib",
    "json",
    "typing",
    "nexora.m30_bias.candles",
    "nexora.m30_bias.evaluate",
    "nexora.m30_bias.freeze",
    "nexora.m30_bias.models",
}
TYPE_CHECKING_ONLY = {"nexora.market_data.models"}
FORBIDDEN_ATTRIBUTES = {
    "now",
    "utcnow",
    "today",
    "time",
    "time_ns",
    "monotonic",
    "perf_counter",
    "sleep",
    "environ",
    "getenv",
    "urandom",
    "random",
    "uuid4",
    "write_text",
    "write_bytes",
    "connect",
}
FORBIDDEN_NAMES = {"open", "print", "input", "exec", "eval", "__import__"}


def _modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _type_checking_blocks(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "TYPE_CHECKING":
            for child in node.body:
                lines.update(range(child.lineno, (child.end_lineno or child.lineno) + 1))
    return lines


def test_pure_core_imports_only_allowed_modules() -> None:
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = _type_checking_blocks(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if node.lineno in guarded:
                    assert name in TYPE_CHECKING_ONLY, (path.name, name)
                else:
                    assert name in ALLOWED_IMPORTS, (path.name, name)


def test_pure_core_has_no_clock_environment_io_or_network_calls() -> None:
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_ATTRIBUTES, (path.name, node.lineno, node.attr)
            # `open` is also an ADR OHLC field name; only calls to builtins are forbidden.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_NAMES, (path.name, node.lineno, node.func.id)


_PROBE = r"""
import os, sys, time

def _trap(*_a, **_k):
    raise AssertionError("wall_clock_read")

for _name in ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns"):
    setattr(time, _name, _trap)

_writes, _net = [], []
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

def _hook(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS
        ):
            _writes.append(str(args[0]))
    elif event.startswith(("socket.", "subprocess.", "os.system", "sqlite3.connect", "urllib")):
        _net.append(event)

sys.addaudithook(_hook)

import nexora.m30_bias as m30
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

loaded = sorted(
    m for m in sys.modules
    if m.split(".")[0] in {"MetaTrader5", "socket", "sqlite3", "ssl", "urllib", "http"}
    or m.startswith("nexora.market_data")
)
assert not loaded, loaded


class Algorithm:
    algorithm_id, algorithm_version, params, required_history = "probe", "1", {}, 1

    def evaluate(self, context):
        return m30.AlgorithmVerdict(bias="NO_EDGE", evidence=())


core = m30.M30BiasCore(
    time_contract="legacy-adr019",
    algorithm=Algorithm(),
    threshold_policy=None,
    freeze_lead_seconds=0,
    eligibility_policy="structural-v1",
    evidence_source="research:probe",
)
start = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
emitted = []
for i, minutes in enumerate((0, 20, 30, 45, 60)):
    t = start + timedelta(minutes=minutes)
    event = SimpleNamespace(
        source="MT5-quote-observation:time-offset=0", symbol="XAUUSD", price_source="bid",
        units="USD", event_time=t, received_at=t + timedelta(milliseconds=5),
        price=Decimal("100") + i, identity_key=f"p{i}", is_gap=False,
    )
    emitted.extend(core.process(m30.CommittedRow(event=event, output={"i": i},
                                                 completeness="unknown")))
payload = b"".join(m30.record_bytes(e) for e in emitted)
assert len(emitted) == 3, emitted
assert not _writes, _writes
assert not _net, _net
print("PURE", len(payload))
"""


def test_import_and_use_perform_no_mt5_io_network_or_clock_reads() -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-B", "-c", _PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PURE")
