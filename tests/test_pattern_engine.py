"""P&F Pattern Engine V1 pure core (ADR-024 Phase 2A): legacy parity in SHADOW."""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import random
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.features import PATTERN_ENGINE_UNITS, ResolvedFeatureConfig, resolve_features_config
from nexora.patterns import (
    EMPTY_STATE,
    LEGACY_UNITS,
    PENDING_BOUND,
    WINDOW_BOUND,
    PatternEngine,
    PatternEngineSnapshot,
    PatternResult,
    PatternStep,
    legacy_parity_key,
    parameters_hash,
    pattern_id,
)
from nexora.pnf import PnfTransition
from nexora.signals.engine import SignalEngine
from nexora.signals.models import PatternEvidence, SignalConfig
from nexora.structure import ConfirmedPivot, StructureEngine, StructureSnapshot

SYMBOL = "XAUUSD"
RESOLUTION = "medium"
TOLERANCE = Decimal("0.8")
BASE = datetime(2026, 1, 5, tzinfo=UTC)
ALL_TYPES = frozenset(unit.pattern_type for unit in LEGACY_UNITS)
ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = (
    ROOT / "packages/nexora/features.py",
    *sorted((ROOT / "packages/nexora/patterns").glob("*.py")),
)


def _shadow(units: dict[str, str] | None = None) -> ResolvedFeatureConfig:
    feature: dict[str, Any] = {"lifecycle": "SHADOW"}
    if units:
        feature["units"] = units
    return resolve_features_config(
        {"schema_version": 1, "version": "test-1", "features": {"pattern_engine": feature}}
    )


def _engine(features: ResolvedFeatureConfig | None = None) -> PatternEngine:
    return PatternEngine(
        symbol=SYMBOL,
        resolution=RESOLUTION,
        price_tolerance=TOLERANCE,
        features=_shadow() if features is None else features,
    )


def _transitions(
    prices: Sequence[str | Decimal], tz: timezone = UTC, prefix: str = "t"
) -> list[PnfTransition]:
    """Synthetic P&F transitions: a column per direction change, one minute apart."""
    result: list[PnfTransition] = []
    column, direction = 1, "X"
    previous: Decimal | None = None
    for index, raw in enumerate(prices):
        price = Decimal(raw)
        if previous is not None:
            moved = "X" if price > previous else "O" if price < previous else direction
            if moved != direction:
                column, direction = column + 1, moved
        result.append(
            PnfTransition(
                type="seed" if previous is None else "extension",
                reason="seed_confirmed" if previous is None else "extended",
                symbol=SYMBOL,
                column_id=column,
                direction="X" if direction == "X" else "O",
                from_price=previous if previous is not None else price,
                to_price=price,
                boxes_moved=1,
                event_time=(BASE + timedelta(minutes=index)).astimezone(tz),
                source_event_id=f"e{index}",
                identity_key=f"{SYMBOL}:{prefix}{index}",
                config_version="pnf-test-v1",
                effective_box_size=Decimal("0.5"),
                sizing_rule_version="fixed-test",
            )
        )
        previous = price
    return result


def _events(
    transitions: Sequence[PnfTransition], sizes: Sequence[int]
) -> list[list[PnfTransition]]:
    events: list[list[PnfTransition]] = []
    index = 0
    for size in sizes:
        if index >= len(transitions):
            break
        events.append(list(transitions[index : index + size]))
        index += size
    if index < len(transitions):
        events.append(list(transitions[index:]))
    return events


def _drive(
    engine: PatternEngine, events: Sequence[Sequence[PnfTransition]]
) -> Iterator[tuple[PatternEngineSnapshot, StructureSnapshot]]:
    structure = StructureEngine(SYMBOL)
    for event in events:
        steps: list[PatternStep] = [(t, structure.process(t)) for t in event]
        yield engine.process_event(steps), structure.snapshot()


def _legacy(snapshot: StructureSnapshot) -> tuple[PatternEvidence, ...]:
    config = SignalConfig(symbol=SYMBOL, cooldown_events=3, expiry_events=10, version="t")
    return SignalEngine(config)._patterns(snapshot)


def _legacy_key(evidence: PatternEvidence) -> tuple[Any, ...]:
    return (
        evidence.pattern_type,
        evidence.direction,
        evidence.start_time,
        evidence.confirmation_time,
        evidence.price_low,
        evidence.price_high,
        evidence.evidence_code,
        evidence.algorithm_version,
        evidence.source_data_reference,
    )


def _assert_parity(
    snapshot: PatternEngineSnapshot,
    structure: StructureSnapshot,
    enabled: frozenset[str] = ALL_TYPES,
) -> None:
    expected = sorted(
        (_legacy_key(e) for e in _legacy(structure) if e.pattern_type in enabled), key=repr
    )
    actual = sorted((legacy_parity_key(r) for r in snapshot.current), key=repr)
    assert actual == expected


def _shape(pivots: Sequence[str], start: str, end: str) -> list[str]:
    return [start, *pivots, end]


# Hand-crafted pivot sequences (every interior price is a confirmed pivot).
CASES: dict[str, list[str]] = {
    "double_bottom": _shape(["99.8", "104.0", "100.0"], "102", "103"),
    "double_top": _shape(["111.0", "106.0", "110.7"], "108", "107"),
    "head_and_shoulders": _shape(["110", "105", "115", "105.5", "110.3", "104"], "107", "106"),
    "inverse_head_and_shoulders": _shape(
        ["100", "105", "95", "104.5", "100.4", "106"], "103", "104"
    ),
    "triangle_breakdown": _shape(["112", "104", "110", "106", "108", "103"], "108", "105"),
    "triangle_breakout": _shape(["100", "110", "102", "108", "104", "111"], "103", "109"),
    "failed_breakout": _shape(["110", "105", "112", "104"], "107", "106"),
    "failed_breakdown": _shape(["100", "105", "98", "106"], "102", "103"),
}


def _random_prices(seed: int, count: int = 400) -> list[str]:
    rng = random.Random(seed)
    price = Decimal("100")
    prices: list[str] = []
    steps = [Decimal(s) for s in ("-2", "-1.5", "-1", "-0.5", "0", "0.5", "1", "1.5", "2")]
    for _ in range(count):
        price += rng.choice(steps) * (3 if rng.random() < 0.1 else 1)
        prices.append(str(price))
    return prices


def _random_sizes(seed: int) -> list[int]:
    rng = random.Random(seed + 10_000)
    return [rng.choice((0, 1, 1, 1, 2, 3)) for _ in range(1000)]


# --- legacy equivalence ----------------------------------------------------------------


@pytest.mark.parametrize("pattern_type", sorted(CASES))
def test_every_legacy_unit_matches_signal_engine(pattern_type: str) -> None:
    engine = _engine()
    seen: set[str] = set()
    for snapshot, structure in _drive(engine, _events(_transitions(CASES[pattern_type]), [1] * 20)):
        _assert_parity(snapshot, structure)
        seen.update(r.pattern_type for r in snapshot.current)
    assert pattern_type in seen
    assert pattern_type in {e.pattern_type for e in _legacy(structure)}


@pytest.mark.parametrize("seed", range(40))
def test_seeded_streams_match_signal_engine_at_every_event_end(seed: int) -> None:
    engine = _engine()
    events = _events(_transitions(_random_prices(seed)), _random_sizes(seed))
    for snapshot, structure in _drive(engine, events):
        _assert_parity(snapshot, structure)


def test_seeded_streams_cover_every_legacy_unit() -> None:
    covered: set[str] = set()
    for seed in range(40):
        engine = _engine()
        for snapshot, _ in _drive(engine, _events(_transitions(_random_prices(seed)), [1] * 500)):
            covered.update(r.pattern_type for r in snapshot.current)
    assert covered == {unit.pattern_type for unit in LEGACY_UNITS}


def test_unit_disable_keeps_other_units_and_triangle_exclusion_parity() -> None:
    disabled = {
        "legacy_pivot.head_and_shoulders": "DISABLED",
        "legacy_pivot.inverse_head_and_shoulders": "DISABLED",
        "legacy_pivot.double_top": "DISABLED",
    }
    enabled = frozenset(u.pattern_type for u in LEGACY_UNITS if u.algorithm_id not in disabled)
    for seed in range(20):
        engine = _engine(_shadow(disabled))
        events = _events(_transitions(_random_prices(seed)), _random_sizes(seed))
        for snapshot, structure in _drive(engine, events):
            _assert_parity(snapshot, structure, enabled)
            assert not {r.algorithm_id for r in snapshot.current} & set(disabled)
    statuses = {u.unit_id: u for u in snapshot.status.units}
    assert statuses["legacy_pivot.double_top"].health == "not_evaluated"
    assert statuses["legacy_pivot.double_top"].effective_lifecycle == "DISABLED"


def test_legacy_signal_patterns_unchanged_by_running_the_engine() -> None:
    transitions = _transitions(_random_prices(3))
    structure = StructureEngine(SYMBOL)
    snapshots = [structure.process(t) for t in transitions]
    before = [_legacy(s) for s in snapshots]
    engine = _engine()
    engine.process_event(list(zip(transitions, snapshots, strict=True)))
    assert [_legacy(s) for s in snapshots] == before


# --- result contract and identity --------------------------------------------------------


def test_result_contract_for_double_bottom() -> None:
    engine = _engine()
    snapshots = [
        s for s, _ in _drive(engine, _events(_transitions(CASES["double_bottom"]), [1] * 5))
    ]
    (result,) = snapshots[-1].current
    assert result.algorithm_id == "legacy_pivot.double_bottom"
    assert result.family == "legacy_pivot"
    assert result.status == "confirmed" and result.status_reason == "detected"
    assert result.lifecycle == "SHADOW"
    assert [a.role for a in result.anchors] == ["first", "middle", "second"]
    assert [a.source_transition_id for a in result.anchors] == [
        f"{SYMBOL}:t1",
        f"{SYMBOL}:t2",
        f"{SYMBOL}:t3",
    ]
    assert [a.column_id for a in result.anchors] == [2, 3, 4]
    assert (result.start_column, result.end_column, result.confirmation_column) == (2, 4, 5)
    assert result.confirmation_transition_id == f"{SYMBOL}:t4"
    assert result.source_refs == (f"{SYMBOL}:t1", f"{SYMBOL}:t2", f"{SYMBOL}:t3", f"{SYMBOL}:t4")
    assert (result.price_low, result.price_high) == (Decimal("99.8"), Decimal("104.0"))
    assert result.evidence == ("abs_p1_p3=0.2<=tol=0.8",)
    assert result.confirmed_sequence == result.status_sequence == 5
    assert result.config_version == "pnf-test-v1"
    assert snapshots[-1].changed == (result,)
    for field in ("relation", "score", "action", "side", "entry", "stop", "target"):
        assert not hasattr(result, field)


def test_pattern_id_and_parameters_hash_golden_vectors() -> None:
    digest = parameters_hash((("price_tolerance", "0.8"),))
    assert digest == "617781ac5cb017b3fd6cc624b0510f67f8ce55796d7c860cf97d68259459cb20"
    assert parameters_hash((("price_tolerance", "0.8"),)) == canonical_hash(
        {"price_tolerance": "0.8"}
    )
    identity = pattern_id(
        symbol=SYMBOL,
        resolution=RESOLUTION,
        algorithm_id="legacy_pivot.double_bottom",
        algorithm_version="p8a-pattern-v1",
        parameters_hash=digest,
        anchor_ids=(f"{SYMBOL}:t1", f"{SYMBOL}:t2", f"{SYMBOL}:t3"),
    )
    assert identity == "f46669b829e2aa799dd6807d0e50721cc180ceadef0513268605800c6a69be97"
    # Independent recomputation of ADR-024 Decision 5 over the canonical JSON encoding.
    payload = [
        SYMBOL,
        RESOLUTION,
        "legacy_pivot.double_bottom",
        "p8a-pattern-v1",
        digest,
        [f"{SYMBOL}:t1", f"{SYMBOL}:t2", f"{SYMBOL}:t3"],
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert identity == hashlib.sha256(encoded).hexdigest()
    engine = _engine()
    *_, (snapshot, _) = _drive(engine, _events(_transitions(CASES["double_bottom"]), [1] * 5))
    assert snapshot.current[0].pattern_id == identity
    assert {d.parameters_hash for d in snapshot.algorithms} == {digest}


def test_identity_is_independent_of_parameter_spelling_lifecycle_and_timezone() -> None:
    prices = CASES["head_and_shoulders"]
    reference = _engine()
    *_, (expected, _) = _drive(reference, _events(_transitions(prices), [1] * 10))
    spelled = PatternEngine(
        symbol=SYMBOL, resolution=RESOLUTION, price_tolerance=Decimal("0.80"), features=_shadow()
    )
    *_, (same, _) = _drive(spelled, _events(_transitions(prices), [1] * 10))
    bangkok = _engine()
    tz = timezone(timedelta(hours=7))
    *_, (shifted, _) = _drive(bangkok, _events(_transitions(prices, tz=tz), [1] * 10))
    ids = [r.pattern_id for r in expected.current]
    assert ids and ids == [r.pattern_id for r in same.current]
    assert ids == [r.pattern_id for r in shifted.current]
    assert canonical_hash(expected) == canonical_hash(same) == canonical_hash(shifted)
    # Lifecycle is not identity: the same detection under another config keeps its id.
    other = _engine(_shadow({"legacy_pivot.double_top": "DISABLED"}))
    *_, (other_snapshot, _) = _drive(other, _events(_transitions(prices), [1] * 10))
    assert ids == [r.pattern_id for r in other_snapshot.current]


def test_identity_changes_with_algorithm_parameters() -> None:
    prices = CASES["double_bottom"]
    loose = PatternEngine(
        symbol=SYMBOL, resolution=RESOLUTION, price_tolerance=Decimal("0.9"), features=_shadow()
    )
    *_, (a, _) = _drive(_engine(), _events(_transitions(prices), [1] * 5))
    *_, (b, _) = _drive(loose, _events(_transitions(prices), [1] * 5))
    assert a.current[0].parameters_hash != b.current[0].parameters_hash
    assert a.current[0].pattern_id != b.current[0].pattern_id


def test_same_input_gives_byte_identical_snapshots() -> None:
    def run() -> list[str]:
        engine = _engine()
        events = _events(_transitions(_random_prices(7)), _random_sizes(7))
        return [
            json.dumps(canonical_serialize(s), sort_keys=True, separators=(",", ":"))
            for s, _ in _drive(engine, events)
        ]

    assert run() == run()


# --- lifecycle of results, incremental/replay, prefix invariance -------------------------


def test_results_expire_when_the_window_shifts() -> None:
    engine = _engine()
    prices = [*CASES["double_bottom"], "101"]  # one more pivot supersedes the window
    snapshots = [s for s, _ in _drive(engine, _events(_transitions(prices), [1] * 10))]
    (confirmed,) = snapshots[4].current
    expired = [r for r in snapshots[5].changed if r.status == "expired"]
    assert expired == [
        replace(
            confirmed,
            status="expired",
            status_reason="window_superseded",
            status_time=expired[0].status_time,
            status_sequence=6,
        )
    ]
    assert all(r.pattern_id != confirmed.pattern_id for r in snapshots[5].current)


def test_incremental_processing_equals_replay() -> None:
    for seed in range(10):
        events = _events(_transitions(_random_prices(seed)), _random_sizes(seed))
        incremental = _engine()
        *_, (last, _) = _drive(incremental, events)
        replayed = _engine()
        structure = StructureEngine(SYMBOL)
        for event in events[:-1]:
            replayed.replay_event([(t, structure.process(t)) for t in event])
        final = replayed.process_event([(t, structure.process(t)) for t in events[-1]])
        assert replayed.state == incremental.state
        assert canonical_hash(final) == canonical_hash(last)
        # Event grouping never changes state: one transition per event reaches the same state.
        single = _engine()
        for _ in _drive(single, _events(_transitions(_random_prices(seed)), [1] * 1000)):
            pass
        assert single.state == incremental.state


def test_prefix_invariance_and_emitted_identities_are_immutable() -> None:
    transitions = _transitions(_random_prices(11))
    events = _events(transitions, _random_sizes(11))
    full: list[PatternEngineSnapshot] = [s for s, _ in _drive(_engine(), events)]
    emitted: dict[str, PatternResult] = {}
    for snapshot in full:
        for result in snapshot.changed:
            if result.status == "confirmed":
                assert result.pattern_id not in emitted
                emitted[result.pattern_id] = result
            else:
                original = emitted[result.pattern_id]
                assert (
                    replace(
                        result,
                        status="confirmed",
                        status_reason="detected",
                        status_time=original.status_time,
                        status_sequence=original.status_sequence,
                    )
                    == original
                )
    for cut in (1, len(events) // 3, len(events) // 2, len(events) - 1):
        prefix = [s for s, _ in _drive(_engine(), events[:cut])]
        assert [canonical_hash(s) for s in prefix] == [canonical_hash(s) for s in full[:cut]]


def test_state_stays_bounded() -> None:
    for seed in range(10):
        engine = _engine()
        for _ in _drive(
            engine, _events(_transitions(_random_prices(seed, 800)), _random_sizes(seed))
        ):
            state = engine.state
            assert len(state.window) <= WINDOW_BOUND == 6
            assert len(state.pending) <= PENDING_BOUND == 2
            ids = [r.algorithm_id for r in state.current]
            assert len(ids) == len(set(ids)) <= len(LEGACY_UNITS)
            assert all(r.status == "confirmed" for r in state.current)
        assert state.pivot_count > WINDOW_BOUND


# --- DISABLED / SHADOW -------------------------------------------------------------------


def test_disabled_engine_keeps_no_state_and_publishes_explicit_empty_block() -> None:
    engine = PatternEngine(symbol=SYMBOL, resolution=RESOLUTION, price_tolerance=TOLERANCE)
    for snapshot, _ in _drive(engine, _events(_transitions(_random_prices(1)), [1] * 500)):
        assert snapshot.current == snapshot.changed == ()
        assert snapshot.sequence == 0
    assert engine.state == EMPTY_STATE
    status = snapshot.status
    assert status.effective_lifecycle == status.configured_lifecycle == "DISABLED"
    assert status.health == "not_evaluated"
    assert {u.health for u in status.units} == {"not_evaluated"}
    assert [u.unit_id for u in status.units] == list(PATTERN_ENGINE_UNITS)


def test_disabled_engine_runs_no_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = PatternEngine(symbol=SYMBOL, resolution=RESOLUTION, price_tolerance=TOLERANCE)
    monkeypatch.setattr(PatternEngine, "_evaluate", _fail_if_called)
    for _ in _drive(engine, _events(_transitions(CASES["double_top"]), [1] * 10)):
        pass


def _fail_if_called(*_: object) -> None:
    raise AssertionError("DISABLED engine evaluated a unit")


def test_shadow_status_block_and_health() -> None:
    engine = _engine()
    snapshots = [
        s for s, _ in _drive(engine, _events(_transitions(CASES["head_and_shoulders"]), [1] * 10))
    ]
    first = snapshots[0].status
    assert first.effective_lifecycle == "SHADOW" and first.feature_class == "analytical"
    assert first.health == "warmup"
    assert first.feature_config_version == "test-1"
    assert first.feature_config_hash == _shadow().feature_config_hash
    last = snapshots[-1].status
    assert last.health == "ready"
    assert {u.health for u in last.units} == {"ready"}


def test_pattern_core_has_no_decision_or_runtime_dependencies() -> None:
    forbidden_prefixes = (
        "nexora.signals",
        "nexora.entry_readiness",
        "nexora.risk",
        "nexora.paper",
        "nexora.experience",
        "nexora.matrix",
        "nexora.research",
        "nexora.market_regime",
        "nexora.backtest",
        "nexora.storage",
        "nexora_api",
    )
    forbidden_modules = {
        "os",
        "time",
        "socket",
        "sqlite3",
        "pathlib",
        "subprocess",
        "urllib",
        "http",
        "random",
        "uuid",
        "threading",
        "asyncio",
        "MetaTrader5",
        "psycopg",
        "fastapi",
    }
    for path in CORE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(forbidden_prefixes), (path.name, name)
                assert name.split(".")[0] not in forbidden_modules, (path.name, name)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"now", "utcnow", "today", "environ", "getenv"}, (
                    path.name,
                    node.attr,
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", path.name
    # No production module consumes the engine yet (no wiring in Phase 2A).
    for path in (ROOT / "packages").rglob("*.py"):
        if path.parent.name == "patterns" or path.name == "features.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "nexora.patterns" not in text and "nexora.features" not in text, path


# --- fail-closed inputs ------------------------------------------------------------------


def _steps(prices: Sequence[str]) -> tuple[list[PnfTransition], list[StructureSnapshot]]:
    transitions = _transitions(prices)
    structure = StructureEngine(SYMBOL)
    return transitions, [structure.process(t) for t in transitions]


def test_symbol_mismatch_is_rejected_for_the_event_without_state_change() -> None:
    transitions, snapshots = _steps(CASES["double_bottom"])
    engine = _engine()
    engine.process_event(list(zip(transitions, snapshots, strict=True)))
    before = engine.state
    foreign = replace(transitions[-1], symbol="EURUSD", identity_key="EURUSD:x")
    snapshot = engine.process_event([(foreign, snapshots[-1])])
    assert engine.state == before
    assert snapshot.current == ()
    assert snapshot.status.health == "unavailable"
    assert snapshot.status.reason_codes == ("symbol_mismatch",)
    assert engine.snapshot().current == before.current  # the next clean read recovers


def test_duplicate_and_inconsistent_steps_are_rejected() -> None:
    transitions, snapshots = _steps(CASES["double_bottom"])
    engine = _engine()
    engine.process_event([(transitions[0], snapshots[0])])
    before = engine.state
    duplicate = engine.process_event([(transitions[0], snapshots[0])])
    assert duplicate.status.reason_codes == ("duplicate_transition",)
    skipped = engine.process_event([(transitions[2], snapshots[2])])
    assert skipped.status.reason_codes == ("structure_inconsistent",)
    assert engine.state == before
    assert duplicate.current == skipped.current == ()


def test_future_pivot_confirmation_is_rejected() -> None:
    transitions, snapshots = _steps(CASES["double_bottom"])
    engine = _engine()
    engine.process_event(list(zip(transitions[:2], snapshots[:2], strict=True)))
    before = engine.state
    step = snapshots[2]
    (pivot,) = step.pivots
    future = replace(step, pivots=(replace(pivot, confirmation_time=BASE + timedelta(days=1)),))
    snapshot = engine.process_event([(transitions[2], future)])
    assert snapshot.status.reason_codes == ("future_inputs",)
    assert engine.state == before


def test_unresolved_anchor_column_is_never_guessed() -> None:
    transitions, snapshots = _steps(CASES["double_bottom"])
    engine = _engine()
    engine.process_event(list(zip(transitions[:4], snapshots[:4], strict=True)))
    last = snapshots[4]
    ghost = replace(last.pivots[-1], source_transition_id="unknown:transition")
    patched: StructureSnapshot = replace(last, pivots=(*last.pivots[:-1], ghost))
    snapshot = engine.process_event([(transitions[4], patched)])
    assert snapshot.current == ()
    unit = {u.unit_id: u for u in snapshot.status.units}["legacy_pivot.double_bottom"]
    assert unit.health == "unavailable"
    assert unit.reason_codes == ("anchor_column_unresolved",)
    assert engine.state.window[-1].column_id is None


def test_unit_exception_is_isolated() -> None:
    engine = _engine()

    def boom(*_: object) -> bool:
        raise RuntimeError("unit bug")

    units = list(engine._units)
    units[0] = replace(units[0], unit=replace(units[0].unit, matches=boom))
    engine._units = tuple(units)
    events = _events(_transitions(CASES["double_top"]), [1] * 10)
    *_, (snapshot, structure) = _drive(engine, events)
    assert [r.pattern_type for r in snapshot.current] == ["double_top"]
    _assert_parity(snapshot, structure, frozenset({"double_top"}))


def test_invalid_engine_parameters_fail() -> None:
    for tolerance in (Decimal("-0.1"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="invalid_pattern_parameters"):
            PatternEngine(symbol=SYMBOL, resolution=RESOLUTION, price_tolerance=tolerance)


# --- purity ------------------------------------------------------------------------------


def test_processing_touches_no_clock_filesystem_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transitions, snapshots = _steps(_random_prices(5))
    engine = _engine()

    def forbidden(*_: object, **__: object) -> Any:
        raise AssertionError("impure call from the pattern core")

    for target, name in (
        (time, "time"),
        (time, "time_ns"),
        (time, "monotonic"),
        (time, "perf_counter"),
        (builtins, "open"),
        (socket, "socket"),
        (socket, "create_connection"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    for transition, snapshot in zip(transitions, snapshots, strict=True):
        engine.process_event([(transition, snapshot)])
    engine.snapshot()


def test_importing_the_core_loads_no_broker_network_or_api_module(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "import nexora.features, nexora.patterns\n"
        "bad = [m for m in ('MetaTrader5', 'socket', 'ssl', 'http.client', 'urllib.request',"
        " 'psycopg', 'fastapi', 'nexora_api', 'nexora.research', 'nexora.signals')"
        " if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == ""
    assert list(tmp_path.iterdir()) == []


def test_pivot_helper_types() -> None:
    # Guard the test fixture itself: every interior price in CASES is a confirmed pivot.
    for name, prices in CASES.items():
        _, snapshots = _steps(prices)
        pivots: tuple[ConfirmedPivot, ...] = snapshots[-1].pivots
        assert len(pivots) == len(prices) - 2, name
