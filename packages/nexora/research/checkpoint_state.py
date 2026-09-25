"""Explicit, versioned research-runtime checkpoint state (ADR-022).

Each stateful component has an explicit contract here: its state is written as
JSON-compatible primitives, and restored onto a component freshly constructed
from the *current* configuration, after validation. Types come only from this
module's static schema, never from the payload, so decoding cannot instantiate
arbitrary objects or execute code. Decimals and datetimes round-trip exactly
(``str``/``isoformat``), unlike ``canonical_serialize``, which normalizes them
for hashing. Runtime dicts are stored as ordered pairs: their iteration order is
behavior (for example, Experience processes pending episodes in insertion order).

``COVERED_FIELDS`` lists every attribute of every component; a contract test
fails when a component gains state this module does not capture.
"""

from __future__ import annotations

import types
from dataclasses import fields, is_dataclass, replace
from datetime import datetime
from decimal import Decimal
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from nexora.adaptive_box import AdaptiveBoxSizer
from nexora.adaptive_box.models import AdaptiveBoxDecision, AdaptiveBoxSnapshot, AdaptiveBoxState
from nexora.adaptive_box.runner import AdaptivePnfRunner
from nexora.entry_readiness import EntryReadinessSnapshot
from nexora.experience.models import Experience
from nexora.features import ResolvedFeatureConfig, default_features_config
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_regime import MarketRegimeEngine, RegimeSnapshot
from nexora.market_regime.models import RegimeLabel
from nexora.matrix import MatrixEngine, MatrixSnapshot
from nexora.patterns import (
    EMPTY_STATE,
    LEGACY_UNITS,
    PENDING_BOUND,
    WINDOW_BOUND,
    PatternEngine,
    PatternEngineSnapshot,
    PatternEngineState,
    PatternResult,
    pattern_id,
)
from nexora.pnf import PnfColumn, PnfEngine, PnfTransition
from nexora.pnf.models import _MutableSymbolState
from nexora.research.pipeline import PipelineConfig, ResearchPipeline
from nexora.signals import ResearchSignal, SignalDecision, SignalEngine, SignalSnapshot
from nexora.structure import CandidateLevel, ConfirmedPivot, StructureEngine, StructureSnapshot
from nexora.trendline import TrendlineEngine, TrendlineSnapshot
from nexora.trendline.models import TrendlineAnchor, TrendlineLine

STATE_VERSION = 2

# Every attribute of every checkpointed component. Configuration attributes are
# rebuilt from the current config; all others are persisted below, except
# ExperienceService._derived: caches derived from the config and pending Experiences,
# never persisted and rebuilt on demand after restore (ADR-031).
COVERED_FIELDS: dict[str, frozenset[str]] = {
    "ResearchPipeline": frozenset(
        {
            "config",
            "matrix",
            "structure",
            "trendline",
            "pattern_engine",
            "regime",
            "signals",
            "_seen",
            "_last",
            "_output",
        }
    ),
    "MatrixEngine": frozenset(
        {
            "symbol",
            "resolutions",
            "runners",
            "stale_after_events",
            "_latest",
            "_sequence",
            "_watermark",
        }
    ),
    "AdaptivePnfRunner": frozenset({"pnf_engine", "sizer", "_decisions"}),
    "AdaptiveBoxSizer": frozenset({"config", "_states"}),
    "PnfEngine": frozenset({"configs", "_configs_by_symbol", "_states"}),
    "_MutableSymbolState": frozenset(
        {"seed_price", "seed_event_id", "columns", "transitions", "seen_identity_keys"}
    ),
    "StructureEngine": frozenset({"symbol", "_sequence", "_transitions", "_pivots", "_levels"}),
    "TrendlineEngine": frozenset(
        {
            "symbol",
            "_sequence",
            "_column_by_transition",
            "_known_pivot_count",
            "_lows",
            "_highs",
            "_active_bullish",
            "_active_bearish",
            "_history",
        }
    ),
    "MarketRegimeEngine": frozenset({"config", "_sequence", "_previous_label"}),
    "SignalEngine": frozenset(
        {"config", "_sequence", "_history", "_last_signal_sequence", "_last_decision"}
    ),
    "PatternEngine": frozenset(
        {"symbol", "resolution", "price_tolerance", "features", "_units", "_state"}
    ),
    "ExperienceService": frozenset(
        {
            "repository",
            "journal",
            "config",
            "runtime_stream",
            "_last",
            "_pending",
            "_states",
            "_samples",
            "_completed",
            "_seen",
            "_derived",
        }
    ),
}

# Typed parts of ResearchPipeline._output, in its original key order.
_OUTPUT_SCHEMA: tuple[tuple[str, Any], ...] = (
    ("config_version", str),
    ("event", NormalizedPriceEvent),
    ("matrix", MatrixSnapshot),
    ("structure", StructureSnapshot),
    ("trendline", TrendlineSnapshot),
    ("regime", RegimeSnapshot),
    ("signals", SignalSnapshot),
    ("entry_readiness", EntryReadinessSnapshot),
    ("columns", tuple[PnfColumn, ...]),
    ("transitions", tuple[PnfTransition, ...]),
    ("pattern_engine", PatternEngineSnapshot),
)
_LIFECYCLE_KEYS = frozenset({"state", "entered_at", "hits"})
_HIT_NAMES = ("TP1", "TP2", "invalidation")
_HIT_KEYS = frozenset({"event_id", "event_time", "price"})
_SAMPLE_KEYS = frozenset({"event", "completeness", "metadata"})


class StateInvalid(ValueError):
    """The persisted state does not match this module's schema."""


class FeatureConfigMismatch(StateInvalid):
    """Valid feature hash belongs to a different startup configuration."""


# --- Exact, schema-driven primitive codec ------------------------------------------------


def _enc(value: Any) -> Any:
    if value is None or type(value) in (str, int, bool, float):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise StateInvalid("non_finite_decimal")
        return str(value)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise StateInvalid("naive_datetime")
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {name: _enc(getattr(value, name)) for name in _field_names(type(value))}
    if isinstance(value, (tuple, list)):
        return [_enc(item) for item in value]
    raise StateInvalid("unsupported_type")


@cache
def _field_names(model: type) -> tuple[str, ...]:
    return tuple(f.name for f in fields(model))


@cache
def _hints(model: type) -> dict[str, Any]:
    return get_type_hints(model)


def _dec(model: Any, value: Any) -> Any:
    origin, args = get_origin(model), get_args(model)
    if origin in (Union, types.UnionType):
        options = [arg for arg in args if arg is not type(None)]
        if value is None and len(options) != len(args):
            return None
        if len(options) != 1:
            raise StateInvalid("unsupported_union")
        return _dec(options[0], value)
    if origin is Literal:
        if not any(value == arg and type(value) is type(arg) for arg in args):
            raise StateInvalid("invalid_literal")
        return value
    if origin is tuple:
        if type(value) is not list:
            raise StateInvalid("invalid_tuple")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_dec(args[0], item) for item in value)
        if len(args) != len(value):
            raise StateInvalid("invalid_tuple")
        return tuple(_dec(model, item) for model, item in zip(args, value, strict=True))
    if model is Decimal:
        if type(value) is not str:
            raise StateInvalid("invalid_decimal")
        try:
            decimal = Decimal(value)
        except ArithmeticError:
            raise StateInvalid("invalid_decimal") from None
        if not decimal.is_finite():
            raise StateInvalid("invalid_decimal")
        return decimal
    if model is datetime:
        if type(value) is not str:
            raise StateInvalid("invalid_datetime")
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            raise StateInvalid("invalid_datetime") from None
        if moment.utcoffset() is None:
            raise StateInvalid("invalid_datetime")
        return moment
    if isinstance(model, type) and is_dataclass(model):
        names = _field_names(model)
        if type(value) is not dict or set(value) != set(names):
            raise StateInvalid("invalid_fields")
        hints = _hints(model)
        try:
            return model(**{name: _dec(hints[name], value[name]) for name in names})
        except StateInvalid:
            raise
        except (TypeError, ValueError):
            # The component's own validation rejected the values.
            raise StateInvalid("invalid_value") from None
    if model in (str, int, bool):
        if type(value) is not model:
            raise StateInvalid("invalid_scalar")
        return value
    if model is float:
        if type(value) not in (float, int) or type(value) is bool:
            raise StateInvalid("invalid_scalar")
        return float(value)
    raise StateInvalid("unsupported_schema")


def _record(value: Any, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise StateInvalid("invalid_fields")
    return value


def _pairs(value: Any) -> list[list[Any]]:
    if type(value) is not list or any(type(p) is not list or len(p) != 2 for p in value):
        raise StateInvalid("invalid_pairs")
    return value


def _unique(keys: list[Any]) -> None:
    if len(set(keys)) != len(keys):
        raise StateInvalid("duplicate_key")


def _json(value: Any) -> Any:
    """Opaque observation metadata: plain JSON only, validated both ways."""
    if value is None or type(value) in (str, int, bool, float):
        return value
    if type(value) in (list, tuple):
        return [_json(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _json(item) for key, item in value.items()}
    raise StateInvalid("invalid_metadata")


# --- Component contracts ------------------------------------------------------------------


def _encode_pnf(engine: PnfEngine) -> list[dict[str, Any]]:
    return [
        {
            "symbol": config.symbol,
            "seed_price": _enc(state.seed_price),
            "seed_event_id": state.seed_event_id,
            "columns": _enc(state.columns),
            "transitions": _enc(state.transitions),
            "seen_identity_keys": sorted(state.seen_identity_keys),
        }
        for config in engine.configs
        for state in (engine._states[config.symbol],)
    ]


_PNF_KEYS = frozenset(
    {"symbol", "seed_price", "seed_event_id", "columns", "transitions", "seen_identity_keys"}
)


def _restore_pnf(engine: PnfEngine, value: Any) -> None:
    if type(value) is not list or [_record(v, _PNF_KEYS)["symbol"] for v in value] != [
        config.symbol for config in engine.configs
    ]:
        raise StateInvalid("pnf_symbols_mismatch")
    for item in value:
        seen = _dec(tuple[str, ...], item["seen_identity_keys"])
        _unique(list(seen))
        engine._states[item["symbol"]] = _MutableSymbolState(
            seed_price=_dec(Decimal | None, item["seed_price"]),
            seed_event_id=_dec(str | None, item["seed_event_id"]),
            columns=list(_dec(tuple[PnfColumn, ...], item["columns"])),
            transitions=list(_dec(tuple[PnfTransition, ...], item["transitions"])),
            seen_identity_keys=set(seen),
        )


def _encode_runner(runner: AdaptivePnfRunner) -> dict[str, Any]:
    return {
        "decisions": _enc(runner._decisions),
        # Reuses the sizer's existing public snapshot/restore contract.
        "sizer_states": _enc(runner.sizer.snapshot().states),
        "pnf": _encode_pnf(runner.pnf_engine),
    }


def _restore_runner(runner: AdaptivePnfRunner, value: Any) -> None:
    value = _record(value, frozenset({"decisions", "sizer_states", "pnf"}))
    states = _dec(tuple[AdaptiveBoxState, ...], value["sizer_states"])
    _unique([state.symbol for state in states])
    runner.sizer = AdaptiveBoxSizer.from_snapshot(
        AdaptiveBoxSnapshot(config=runner.sizer.config, states=states)
    )
    runner._decisions = list(_dec(tuple[AdaptiveBoxDecision, ...], value["decisions"]))
    _restore_pnf(runner.pnf_engine, value["pnf"])


def _encode_matrix(matrix: MatrixEngine) -> dict[str, Any]:
    return {
        "sequence": matrix._sequence,
        "watermark": matrix._watermark,
        "latest": [[name, [seq, _enc(t)]] for name, (seq, t) in matrix._latest.items()],
        "runners": [[c.name, _encode_runner(matrix.runners[c.name])] for c in matrix.resolutions],
    }


def _restore_matrix(matrix: MatrixEngine, value: Any) -> None:
    value = _record(value, frozenset({"sequence", "watermark", "latest", "runners"}))
    names = [config.name for config in matrix.resolutions]
    runners = _pairs(value["runners"])
    if [name for name, _ in runners] != names:
        raise StateInvalid("matrix_resolutions_mismatch")
    for name, runner in runners:
        _restore_runner(matrix.runners[name], runner)
    latest = _pairs(value["latest"])
    _unique([name for name, _ in latest])
    matrix._latest = {}
    for name, entry in latest:
        if name not in names or type(entry) is not list or len(entry) != 2:
            raise StateInvalid("matrix_latest_invalid")
        matrix._latest[name] = (_dec(int, entry[0]), _dec(PnfTransition, entry[1]))
    matrix._sequence = _dec(int, value["sequence"])
    matrix._watermark = _dec(int, value["watermark"])


def _encode_structure(engine: StructureEngine) -> dict[str, Any]:
    return {
        "sequence": engine._sequence,
        "transitions": _enc(engine._transitions),
        "pivots": _enc(engine._pivots),
        "levels": _enc(engine._levels),
    }


def _restore_structure(engine: StructureEngine, value: Any) -> None:
    value = _record(value, frozenset({"sequence", "transitions", "pivots", "levels"}))
    engine._sequence = _dec(int, value["sequence"])
    engine._transitions = list(_dec(tuple[PnfTransition, ...], value["transitions"]))
    engine._pivots = list(_dec(tuple[ConfirmedPivot, ...], value["pivots"]))
    engine._levels = list(_dec(tuple[CandidateLevel, ...], value["levels"]))


_TRENDLINE_KEYS = frozenset(
    {
        "sequence",
        "column_by_transition",
        "known_pivot_count",
        "lows",
        "highs",
        "active_bullish",
        "active_bearish",
        "history",
    }
)


def _encode_trendline(engine: TrendlineEngine) -> dict[str, Any]:
    return {
        "sequence": engine._sequence,
        "column_by_transition": [[k, v] for k, v in engine._column_by_transition.items()],
        "known_pivot_count": engine._known_pivot_count,
        "lows": _enc(engine._lows),
        "highs": _enc(engine._highs),
        "active_bullish": _enc(engine._active_bullish),
        "active_bearish": _enc(engine._active_bearish),
        "history": _enc(engine._history),
    }


def _restore_trendline(engine: TrendlineEngine, value: Any) -> None:
    value = _record(value, _TRENDLINE_KEYS)
    columns = _pairs(value["column_by_transition"])
    _unique([key for key, _ in columns])
    engine._sequence = _dec(int, value["sequence"])
    engine._column_by_transition = {_dec(str, k): _dec(int, v) for k, v in columns}
    engine._known_pivot_count = _dec(int, value["known_pivot_count"])
    engine._lows = list(_dec(tuple[TrendlineAnchor, ...], value["lows"]))
    engine._highs = list(_dec(tuple[TrendlineAnchor, ...], value["highs"]))
    engine._active_bullish = _dec(TrendlineLine | None, value["active_bullish"])
    engine._active_bearish = _dec(TrendlineLine | None, value["active_bearish"])
    engine._history = list(_dec(tuple[TrendlineLine, ...], value["history"]))


def _encode_regime(engine: MarketRegimeEngine) -> dict[str, Any]:
    return {"sequence": engine._sequence, "previous_label": engine._previous_label}


def _restore_regime(engine: MarketRegimeEngine, value: Any) -> None:
    value = _record(value, frozenset({"sequence", "previous_label"}))
    engine._sequence = _dec(int, value["sequence"])
    engine._previous_label = _dec(RegimeLabel, value["previous_label"])


def _encode_signals(engine: SignalEngine) -> dict[str, Any]:
    return {
        "sequence": engine._sequence,
        "history": _enc(engine._history),
        "last_signal_sequence": engine._last_signal_sequence,
        "last_decision": _enc(engine._last_decision),
    }


def _restore_signals(engine: SignalEngine, value: Any) -> None:
    value = _record(
        value, frozenset({"sequence", "history", "last_signal_sequence", "last_decision"})
    )
    engine._sequence = _dec(int, value["sequence"])
    engine._history = list(_dec(tuple[ResearchSignal, ...], value["history"]))
    engine._last_signal_sequence = _dec(int | None, value["last_signal_sequence"])
    engine._last_decision = _dec(SignalDecision, value["last_decision"])


def _validate_pattern_result(
    result: PatternResult, engine: PatternEngine, structure: StructureEngine
) -> None:
    """Validate references/identity, without detecting patterns or changing evidence."""
    units = {unit.algorithm_id: unit for unit in LEGACY_UNITS}
    descriptors = {d.algorithm_id: d for d in engine.snapshot().algorithms}
    unit = units.get(result.algorithm_id)
    descriptor = descriptors.get(result.algorithm_id)
    if unit is None or descriptor is None:
        raise StateInvalid("pattern_algorithm_invalid")
    if (
        engine.features.effective_unit("pattern_engine", result.algorithm_id) != "SHADOW"
        or result.lifecycle != "SHADOW"
        or result.symbol != engine.symbol
        or result.resolution != engine.resolution
        or result.pattern_type != unit.pattern_type
        or result.direction != unit.direction
        or result.evidence_code != unit.evidence_code
        or result.algorithm_version != descriptor.algorithm_version
        or result.parameters_hash != descriptor.parameters_hash
        or len(result.anchors) != unit.window_size
        or tuple(a.role for a in result.anchors) != unit.roles
        or result.pattern_id
        != pattern_id(
            symbol=result.symbol,
            resolution=result.resolution,
            algorithm_id=result.algorithm_id,
            algorithm_version=result.algorithm_version,
            parameters_hash=result.parameters_hash,
            anchor_ids=tuple(a.source_transition_id for a in result.anchors),
        )
    ):
        raise StateInvalid("pattern_result_invalid")
    pivots = {p.source_transition_id: p for p in structure._pivots}
    transitions = {t.identity_key: t for t in structure._transitions}
    for anchor in result.anchors:
        pivot = pivots.get(anchor.source_transition_id)
        transition = transitions.get(anchor.source_transition_id)
        if (
            pivot is None
            or transition is None
            or (anchor.pivot_kind, anchor.price, anchor.occurrence_time, anchor.confirmation_time)
            != (pivot.kind, pivot.price, pivot.occurrence_time, pivot.confirmation_time)
            or anchor.column_id != transition.column_id
        ):
            raise StateInvalid("pattern_anchor_invalid")
    if not 1 <= result.confirmed_sequence <= result.status_sequence <= structure._sequence:
        raise StateInvalid("pattern_sequence_invalid")
    confirmation = structure._transitions[result.confirmed_sequence - 1]
    status_transition = structure._transitions[result.status_sequence - 1]
    if (
        result.confirmation_transition_id != confirmation.identity_key
        or result.confirmation_column != confirmation.column_id
        or result.config_version != confirmation.config_version
        or result.start_column != result.anchors[0].column_id
        or result.end_column != result.anchors[-1].column_id
        or result.start_time != result.anchors[0].occurrence_time
        or result.confirmation_time != max(a.confirmation_time for a in result.anchors)
        or result.confirmation_time > confirmation.event_time
        or result.status_time != status_transition.event_time
        or result.price_low > result.price_high
        or result.source_refs
        != (*(a.source_transition_id for a in result.anchors), confirmation.identity_key)
        or (
            result.status == "confirmed"
            and (
                result.status_reason != "detected"
                or result.status_sequence != result.confirmed_sequence
            )
        )
        or (
            result.status == "expired"
            and result.status_reason not in ("window_superseded", "input_rejected")
        )
    ):
        raise StateInvalid("pattern_result_reference_invalid")


def _restore_pattern(pipeline: ResearchPipeline, value: Any) -> None:
    engine, structure = pipeline.pattern_engine, pipeline.structure
    state = _dec(PatternEngineState, value)
    if engine.effective_lifecycle == "DISABLED":
        if state != EMPTY_STATE:
            raise StateInvalid("pattern_disabled_state")
    else:
        if (
            state.sequence != structure._sequence
            or state.sequence != len(structure._transitions)
            or state.pivot_count != len(structure._pivots)
            or state.last_pivot_id
            != (structure._pivots[-1].source_transition_id if structure._pivots else None)
            or len(state.window) > WINDOW_BOUND
            or len(state.pending) > PENDING_BOUND
            or len(state.current) > len(LEGACY_UNITS)
        ):
            raise StateInvalid("pattern_state_inconsistent")
        if state.window and tuple(w.pivot for w in state.window) != tuple(
            structure._pivots[-len(state.window) :]
        ):
            raise StateInvalid("pattern_window_invalid")
        transitions = {t.identity_key: t for t in structure._transitions}
        for member in state.window:
            transition = transitions.get(member.pivot.source_transition_id)
            if transition is None or (
                member.column_id is not None and member.column_id != transition.column_id
            ):
                raise StateInvalid("pattern_window_column_invalid")
        if state.pending and state.pending != tuple(
            (t.identity_key, t.column_id) for t in structure._transitions[-len(state.pending) :]
        ):
            raise StateInvalid("pattern_pending_invalid")
        _unique([r.algorithm_id for r in state.current])
        if state.current != tuple(
            sorted(state.current, key=lambda r: (r.confirmed_sequence, r.algorithm_id))
        ):
            raise StateInvalid("pattern_order_invalid")
        for result in state.current:
            _validate_pattern_result(result, engine, structure)
            members = state.window[-len(result.anchors) :]
            if result.status != "confirmed" or tuple(
                (a.source_transition_id, a.column_id) for a in result.anchors
            ) != tuple((w.pivot.source_transition_id, w.column_id) for w in members):
                raise StateInvalid("pattern_current_invalid")
    # State belongs to a fresh, unadopted pipeline. Event-local health/changed are
    # restored from output, not recomputed by snapshot().
    engine._state = state
    if pipeline._output:
        snapshot = pipeline._output["pattern_engine"]
        expected = engine.snapshot()
        status = snapshot.status
        normalized_units = (
            tuple(
                replace(unit, health=base.health, reason_codes=base.reason_codes)
                for unit, base in zip(status.units, expected.status.units, strict=True)
            )
            if len(status.units) == len(expected.status.units)
            else ()
        )
        if (
            snapshot.symbol != engine.symbol
            or snapshot.sequence != state.sequence
            or snapshot.algorithms != expected.algorithms
            or replace(
                status,
                health=expected.status.health,
                reason_codes=expected.status.reason_codes,
                units=normalized_units,
            )
            != expected.status
        ):
            raise StateInvalid("pattern_output_config_invalid")
        if engine.effective_lifecycle == "DISABLED":
            if snapshot != expected:
                raise StateInvalid("pattern_disabled_output")
        elif snapshot.current != state.current and not (
            not snapshot.current
            and status.health == "unavailable"
            and set(status.reason_codes)
            & {
                "symbol_mismatch",
                "duplicate_transition",
                "structure_inconsistent",
                "future_inputs",
                "engine_exception",
            }
        ):
            raise StateInvalid("pattern_output_current_invalid")
        for result in snapshot.changed:
            _validate_pattern_result(result, engine, structure)


def _encode_pipeline(pipeline: ResearchPipeline) -> dict[str, Any]:
    output = pipeline._output
    if output and list(output) != [key for key, _ in _OUTPUT_SCHEMA]:
        raise StateInvalid("output_schema_changed")
    return {
        "seen": [[k, v] for k, v in pipeline._seen.items()],
        "last": _enc(pipeline._last),
        "output": [[key, _enc(output[key])] for key, _ in _OUTPUT_SCHEMA] if output else None,
        "matrix": _encode_matrix(pipeline.matrix),
        "structure": _encode_structure(pipeline.structure),
        "trendline": _encode_trendline(pipeline.trendline),
        "regime": _encode_regime(pipeline.regime),
        "signals": _encode_signals(pipeline.signals),
        "pattern_engine": _enc(pipeline.pattern_engine.state),
    }


_PIPELINE_KEYS = frozenset(
    {
        "seen",
        "last",
        "output",
        "matrix",
        "structure",
        "trendline",
        "regime",
        "signals",
        "pattern_engine",
    }
)


def _restore_pipeline(
    config: PipelineConfig, value: Any, features: ResolvedFeatureConfig
) -> ResearchPipeline:
    value = _record(value, _PIPELINE_KEYS)
    # Construction from the current config rebuilds every configuration attribute.
    pipeline = ResearchPipeline(config, features=features)
    seen = _pairs(value["seen"])
    _unique([key for key, _ in seen])
    pipeline._seen = {_dec(str, k): _dec(str, v) for k, v in seen}
    pipeline._last = _dec(NormalizedPriceEvent | None, value["last"])
    output = value["output"]
    if output is None:
        pipeline._output = {}
    else:
        pairs = _pairs(output)
        if [key for key, _ in pairs] != [key for key, _ in _OUTPUT_SCHEMA]:
            raise StateInvalid("output_schema_mismatch")
        pipeline._output = {
            key: _dec(model, item)
            for (key, model), (_, item) in zip(_OUTPUT_SCHEMA, pairs, strict=True)
        }
    _restore_matrix(pipeline.matrix, value["matrix"])
    _restore_structure(pipeline.structure, value["structure"])
    _restore_trendline(pipeline.trendline, value["trendline"])
    _restore_regime(pipeline.regime, value["regime"])
    _restore_signals(pipeline.signals, value["signals"])
    _restore_pattern(pipeline, value["pattern_engine"])
    return pipeline


def _encode_lifecycle(state: dict[str, Any]) -> dict[str, Any]:
    if set(state) != _LIFECYCLE_KEYS or set(state["hits"]) != set(_HIT_NAMES):
        raise StateInvalid("lifecycle_schema_changed")
    hits: dict[str, Any] = {}
    for name in _HIT_NAMES:
        hit = state["hits"][name]
        if hit is not None and set(hit) != _HIT_KEYS:
            raise StateInvalid("lifecycle_schema_changed")
        hits[name] = (
            None
            if hit is None
            else {
                "event_id": hit["event_id"],
                "event_time": _enc(hit["event_time"]),
                "price": _enc(hit["price"]),
            }
        )
    return {"state": state["state"], "entered_at": _enc(state["entered_at"]), "hits": hits}


def _restore_lifecycle(value: Any) -> dict[str, Any]:
    value = _record(value, _LIFECYCLE_KEYS)
    hits = _record(value["hits"], frozenset(_HIT_NAMES))
    restored: dict[str, Any] = {}
    for name in _HIT_NAMES:
        hit = hits[name]
        restored[name] = (
            None
            if hit is None
            else {
                "event_id": _dec(str, _record(hit, _HIT_KEYS)["event_id"]),
                "event_time": _dec(datetime, hit["event_time"]),
                "price": _dec(Decimal, hit["price"]),
            }
        )
    return {
        "state": _dec(str, value["state"]),
        "entered_at": _dec(datetime | None, value["entered_at"]),
        "hits": restored,
    }


def _encode_sample(sample: dict[str, Any]) -> dict[str, Any]:
    if set(sample) != _SAMPLE_KEYS:
        raise StateInvalid("sample_schema_changed")
    return {
        "event": _enc(sample["event"]),
        "completeness": sample["completeness"],
        "metadata": _json(sample["metadata"]),
    }


def _restore_sample(value: Any) -> dict[str, Any]:
    value = _record(value, _SAMPLE_KEYS)
    return {
        "event": _dec(NormalizedPriceEvent, value["event"]),
        "completeness": _dec(str, value["completeness"]),
        "metadata": _json(value["metadata"]),
    }


def _encode_experience(state: dict[str, Any]) -> dict[str, Any]:
    # observe() appends one shared sample object per event to every eligible pending
    # episode. Store each shared object once and reference it by index, restoring the
    # same sharing; per-episode copies would multiply size by the pending count.
    table: list[dict[str, Any]] = []
    index: dict[int, int] = {}
    references: list[list[Any]] = []
    for eid, rows in state["samples"].items():
        positions = []
        for row in rows:
            if id(row) not in index:
                index[id(row)] = len(table)
                table.append(_encode_sample(row))
            positions.append(index[id(row)])
        references.append([eid, positions])
    return {
        "last": [[scope, _enc(e)] for scope, e in state["last"].items()],
        "pending": [[eid, _enc(e)] for eid, e in state["pending"].items()],
        "states": [[eid, _encode_lifecycle(s)] for eid, s in state["states"].items()],
        "sample_rows": table,
        "samples": references,
        "completed": [[eid, sorted(minutes)] for eid, minutes in state["completed"].items()],
        "seen": [[[scope, key], digest] for (scope, key), digest in state["seen"].items()],
    }


_EXPERIENCE_KEYS = frozenset(
    {"last", "pending", "states", "sample_rows", "samples", "completed", "seen"}
)


def _mapping(value: Any, decode_value: Any) -> dict[str, Any]:
    pairs = _pairs(value)
    _unique([key for key, _ in pairs])
    return {_dec(str, key): decode_value(item) for key, item in pairs}


def _restore_experience(value: Any) -> dict[str, Any]:
    value = _record(value, _EXPERIENCE_KEYS)

    if type(value["sample_rows"]) is not list:
        raise StateInvalid("invalid_samples")
    table = [_restore_sample(row) for row in value["sample_rows"]]

    def samples(positions: Any) -> list[dict[str, Any]]:
        indices = _dec(tuple[int, ...], positions)
        if any(not 0 <= i < len(table) for i in indices):
            raise StateInvalid("invalid_sample_reference")
        return [table[i] for i in indices]

    def minutes(items: Any) -> set[int]:
        values = list(_dec(tuple[int, ...], items))
        _unique(values)
        return set(values)

    seen_pairs = _pairs(value["seen"])
    seen: dict[tuple[str, str], str] = {}
    for key, digest in seen_pairs:
        if type(key) is not list or len(key) != 2:
            raise StateInvalid("invalid_experience_seen")
        identity = (_dec(str, key[0]), _dec(str, key[1]))
        if identity in seen:
            raise StateInvalid("duplicate_key")
        seen[identity] = _dec(str, digest)
    return {
        "last": _mapping(value["last"], lambda item: _dec(Experience, item)),
        "pending": _mapping(value["pending"], lambda item: _dec(Experience, item)),
        "states": _mapping(value["states"], _restore_lifecycle),
        "samples": _mapping(value["samples"], samples),
        "completed": _mapping(value["completed"], minutes),
        "seen": seen,
    }


# --- Whole runtime ------------------------------------------------------------------------


def encode_state(
    pipeline: ResearchPipeline,
    events: list[NormalizedPriceEvent],
    experience: dict[str, Any],
) -> dict[str, Any]:
    """JSON-compatible state of every checkpointed component."""
    return {
        "state_version": STATE_VERSION,
        "feature_config_hash": pipeline.pattern_engine.features.feature_config_hash,
        "events": _enc(events),
        "pipeline": _encode_pipeline(pipeline),
        "experience": _encode_experience(experience),
    }


def restore_state(
    value: Any, config: PipelineConfig, *, features: ResolvedFeatureConfig | None = None
) -> tuple[ResearchPipeline, list[NormalizedPriceEvent], dict[str, Any]]:
    """Rebuild components from `encode_state` output; raises StateInvalid on any mismatch.

    Nothing here touches the running runtime: the caller adopts the result only
    after every further check has passed.
    """
    value = _record(
        value,
        frozenset({"state_version", "feature_config_hash", "events", "pipeline", "experience"}),
    )
    if type(value["state_version"]) is not int or value["state_version"] != STATE_VERSION:
        raise StateInvalid("state_version_mismatch")
    features = features if features is not None else default_features_config()
    digest = _dec(str, value["feature_config_hash"])
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise StateInvalid("invalid_feature_config_hash")
    if digest != features.feature_config_hash:
        raise FeatureConfigMismatch("feature_config_mismatch")
    events = list(_dec(tuple[NormalizedPriceEvent, ...], value["events"]))
    pipeline = _restore_pipeline(config, value["pipeline"], features)
    experience = _restore_experience(value["experience"])
    keys = [event.identity_key for event in events]
    _unique(keys)
    # Cross-component consistency: every component observed exactly these events.
    if list(pipeline._seen) != keys or (events and pipeline._last != events[-1]):
        raise StateInvalid("pipeline_events_mismatch")
    for runner in pipeline.matrix.runners.values():
        for state in runner.pnf_engine._states.values():
            if state.seen_identity_keys != set(keys):
                raise StateInvalid("pnf_events_mismatch")
    return pipeline, events, experience
