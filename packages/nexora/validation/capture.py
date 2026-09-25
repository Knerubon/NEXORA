"""Stage A: decision-time capture over the unmodified research pipeline (ADR-030 Decision 2).

The driver consumes events lazily, one at a time, from an iterator. Capturers read
public engine snapshots immediately after `e_k` is processed and before `e_{k+1}` is
pulled, so no later event exists anywhere reachable from Stage A (G1). This module
never imports labeling or metrics; outcomes cannot flow back into engine input (G2).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from nexora.artifacts import canonical_hash
from nexora.entry_readiness import EntryReadinessSnapshot, evaluate_entry_readiness
from nexora.market_data.models import NormalizedPriceEvent
from nexora.pnf import PnfTransition
from nexora.research import PipelineConfig, ResearchPipeline
from nexora.signals import SignalDecision
from nexora.signals.models import PatternEvidence, SignalAction
from nexora.structure import StructureSnapshot
from nexora.trendline.models import TrendlineLine, TrendlineSnapshot
from nexora.validation.models import (
    BaselineRule,
    Direction,
    EvidenceItem,
    GenerationMode,
    KnowledgeClock,
    LookAheadViolation,
    ObservationRecord,
    SubjectKind,
    ValidationError,
)
from nexora.validation.sealing import GENESIS_CHAIN, as_of_json, chain_hash, record_hash

_PATTERN_DIRECTION: dict[str, Direction] = {
    "bullish": "long",
    "bearish": "short",
    "neutral": "neutral",
}
_ACTION_DIRECTION: dict[SignalAction, Direction] = {
    "BUY": "long",
    "SELL": "short",
    "WAIT": "neutral",
}


@dataclass(frozen=True, slots=True)
class CaptureResult:
    observations: tuple[ObservationRecord, ...]
    chain_hash: str
    event_count: int


@dataclass(slots=True)
class _Step:
    """Everything known at decision point k. Built only from `e_1..e_k`."""

    index: int
    event: NormalizedPriceEvent
    new_transitions: tuple[PnfTransition, ...]
    box_size: Decimal | None
    structure: StructureSnapshot | None
    trendline: TrendlineSnapshot | None
    decision: SignalDecision
    signals_history_len: int
    entry_readiness: EntryReadinessSnapshot


@dataclass(slots=True)
class _CaptureState:
    kinds: frozenset[SubjectKind]
    baseline: BaselineRule | None
    mode: GenerationMode
    records: list[ObservationRecord] = field(default_factory=list)
    chain: str = GENESIS_CHAIN
    pivot_count: int = 0
    level_seen: set[tuple[str, str, str]] = field(default_factory=set)
    pattern_seen: set[str] = field(default_factory=set)
    line_seen: set[tuple[str, str]] = field(default_factory=set)
    last_action: SignalAction | None = None
    signal_seen: set[str] = field(default_factory=set)
    last_readiness: str | None = None


def capture(
    events: Iterable[NormalizedPriceEvent],
    *,
    pipeline_config: PipelineConfig,
    subject_kinds: tuple[SubjectKind, ...],
    baseline: BaselineRule | None = None,
) -> CaptureResult:
    """Replay `events` in order through a fresh pipeline and seal observations (RECOMPUTED)."""
    pipeline = ResearchPipeline(pipeline_config)
    resolution = pipeline_config.structure_resolution
    runner = pipeline.matrix.runners[resolution]
    symbol = pipeline_config.signals.symbol
    state = _CaptureState(frozenset(subject_kinds), baseline, "RECOMPUTED")
    transition_count = 0
    box_size: Decimal | None = None
    structure: StructureSnapshot | None = None
    trendline: TrendlineSnapshot | None = None
    index = 0
    for event in events:
        index += 1
        try:
            pipeline.replay(event)
        except ValueError as exc:
            raise ValidationError(f"replay_failed:{exc}") from None
        transitions = runner.pnf_engine.state_for(symbol).transitions
        new = transitions[transition_count:]
        transition_count = len(transitions)
        if new:
            box_size = new[-1].effective_box_size
            structure = pipeline.structure.snapshot()
            trendline = pipeline.trendline.snapshot()
        signals = pipeline.signals.snapshot()
        readiness_trendline = trendline or pipeline.trendline.snapshot()
        # Same pure function and arguments as ResearchPipeline._process (contract-tested).
        readiness = evaluate_entry_readiness(
            decision=signals.decision,
            trendline=readiness_trendline,
            config_version=pipeline_config.version,
        )
        step = _Step(
            index=index,
            event=event,
            new_transitions=new,
            box_size=box_size,
            structure=structure,
            trendline=trendline,
            decision=signals.decision,
            signals_history_len=len(signals.history),
            entry_readiness=readiness,
        )
        _capture_step(state, step, signals.history, resolution)
    return CaptureResult(tuple(state.records), state.chain, index)


def _capture_step(
    state: _CaptureState, step: _Step, history: tuple[Any, ...], resolution: str
) -> None:
    kinds = state.kinds
    if "pnf.transition" in kinds:
        for transition in step.new_transitions:
            _pnf_transition(state, step, transition, resolution)
    if step.new_transitions and step.structure is not None:
        if "structure.pivot_confirmed" in kinds:
            for pivot in step.structure.pivots[state.pivot_count :]:
                _check(pivot.occurrence_time <= pivot.confirmation_time, "pivot_order")
                item = _item(
                    "structure",
                    pivot.source_transition_id,
                    pivot.confirmation_time,
                    "event_time",
                    pivot.config_version,
                    pivot,
                )
                _emit(
                    state,
                    step,
                    "structure.pivot_confirmed",
                    f"structure.pivot_confirmed:{pivot.kind}",
                    "neutral",
                    {"pivot": pivot},
                    (item,),
                )
        state.pivot_count = len(step.structure.pivots)
        if "structure.level_invalidated" in kinds:
            for level in step.structure.levels:
                key = (level.side, str(level.source_pivot_id), f"{level.price}")
                if level.status != "invalidated" or key in state.level_seen:
                    continue
                state.level_seen.add(key)
                item = _item(
                    "structure",
                    str(level.source_pivot_id),
                    level.updated_at,
                    "event_time",
                    "structure",
                    level,
                )
                _emit(
                    state,
                    step,
                    "structure.level_invalidated",
                    f"structure.level_invalidated:{level.side}",
                    "neutral",
                    {"level": level},
                    (item,),
                )
    if "pattern.legacy" in kinds:
        for pattern in step.decision.patterns:
            _pattern(state, step, pattern)
    if "trendline.state" in kinds and step.new_transitions and step.trendline is not None:
        lines = (step.trendline.active_bullish, step.trendline.active_bearish)
        for line in (*step.trendline.history, *lines):
            if line is not None:
                _trendline(state, step, line)
    action = step.decision.action
    if "signal.decision" in kinds and action != state.last_action:
        items = tuple(
            _item(
                "pattern_legacy",
                p.evidence_code,
                p.confirmation_time,
                "event_time",
                p.algorithm_version,
                p,
            )
            for p in step.decision.patterns
        )
        _emit(
            state,
            step,
            "signal.decision",
            f"signal.decision:{action}",
            _ACTION_DIRECTION[action],
            {"decision": step.decision},
            items,
            invalidation=step.decision.invalidation_price,
        )
    state.last_action = action
    if "signal.issued" in kinds:
        for signal in history:
            if signal.signal_id in state.signal_seen:
                continue
            state.signal_seen.add(signal.signal_id)
            # Only the decision-time artifact counts; a later expiry never rewrites it.
            if signal.status != "active" or signal.decision_time != step.event.received_at:
                continue
            _check(signal.confirmation_time <= step.event.event_time, "signal_confirmation")
            item = _item(
                "signal",
                signal.signal_id,
                signal.decision_time,
                "received_at",
                signal.config_version,
                signal,
            )
            direction: Direction = "long" if signal.side == "long" else "short"
            invalidation = signal.decision.invalidation_price if signal.decision else None
            _emit(
                state,
                step,
                "signal.issued",
                f"signal.issued:{signal.side}",
                direction,
                {"signal": signal},
                (item,),
                invalidation=invalidation,
            )
    readiness = step.entry_readiness
    readiness_key = as_of_json((readiness.state, readiness.signal_action, readiness.blockers))
    if "entry_readiness.state" in kinds and readiness_key != state.last_readiness:
        items = tuple(
            _item(
                "entry_readiness",
                b.line_id,
                step.event.event_time,
                "event_time",
                readiness.config_version,
                b,
            )
            for b in readiness.blockers
        )
        _emit(
            state,
            step,
            "entry_readiness.state",
            f"entry_readiness.state:{readiness.signal_action}:{readiness.state}",
            _ACTION_DIRECTION[readiness.signal_action],
            {"entry_readiness": readiness},
            items,
            invalidation=step.decision.invalidation_price,
        )
    state.last_readiness = readiness_key
    rule = state.baseline
    if (
        "baseline.every_nth_event" in kinds
        and rule is not None
        and step.index > rule.offset
        and (step.index - rule.offset) % rule.every_n_events == 0
    ):
        _emit(
            state, step, "baseline.every_nth_event", "baseline.every_nth_event", "neutral", {}, ()
        )


def _pnf_transition(
    state: _CaptureState, step: _Step, transition: PnfTransition, resolution: str
) -> None:
    item = _item(
        "pnf",
        transition.identity_key,
        transition.event_time,
        "event_time",
        f"{transition.config_version}/{transition.sizing_rule_version}",
        transition,
    )
    direction: Direction = "long" if transition.direction == "X" else "short"
    _emit(
        state,
        step,
        "pnf.transition",
        f"pnf.transition:{transition.type}:{transition.direction}",
        direction,
        {"transition": transition, "resolution": resolution},
        (item,),
    )


def _pattern(state: _CaptureState, step: _Step, pattern: PatternEvidence) -> None:
    identity = canonical_hash(
        (
            pattern.evidence_code,
            pattern.source_data_reference,
            pattern.algorithm_version,
            pattern.confirmation_time,
        )
    )
    if identity in state.pattern_seen:
        return
    state.pattern_seen.add(identity)
    # Anchored at confirmation, never at the pattern's start (ADR-030 E3).
    _check(pattern.start_time <= pattern.confirmation_time, "pattern_order")
    item = _item(
        "pattern_legacy",
        identity,
        pattern.confirmation_time,
        "event_time",
        pattern.algorithm_version,
        pattern,
    )
    direction = _PATTERN_DIRECTION[pattern.direction]
    _emit(
        state,
        step,
        "pattern.legacy",
        f"pattern.legacy:{pattern.evidence_code}@{pattern.algorithm_version}",
        direction,
        {"pattern": pattern, "decision_action": step.decision.action},
        (item,),
    )


def _trendline(state: _CaptureState, step: _Step, line: TrendlineLine) -> None:
    key = (line.line_id, line.state)
    if key in state.line_seen:
        return
    state.line_seen.add(key)
    item = _item(
        "trendline", line.line_id, line.anchor_b.event_time, "event_time", line.config_version, line
    )
    _emit(
        state,
        step,
        "trendline.state",
        f"trendline.state:{line.kind}:{line.state}",
        "neutral",
        {"line": line},
        (item,),
    )


def _item(
    component: str,
    ref: str,
    knowledge_time: datetime,
    clock: KnowledgeClock,
    version: str,
    payload: Any,
) -> EvidenceItem:
    return EvidenceItem(component, ref, knowledge_time, clock, version, canonical_hash(payload))


def _emit(
    state: _CaptureState,
    step: _Step,
    kind: SubjectKind,
    group: str,
    direction: Direction,
    as_of: dict[str, Any],
    evidence: tuple[EvidenceItem, ...],
    *,
    invalidation: Decimal | None = None,
) -> None:
    event = step.event
    for item in evidence:
        limit = event.event_time if item.knowledge_clock == "event_time" else event.received_at
        # Evidence known only after its decision point invalidates the run (ADR-030 E2).
        if item.knowledge_time > limit:
            raise LookAheadViolation("evidence_after_decision_point")
    unsealed = ObservationRecord(
        schema_version=1,
        generation_mode=state.mode,
        subject_kind=kind,
        subject_group=group,
        subject_id=canonical_hash((kind, group, event.identity_key, [e.ref for e in evidence])),
        anchor_index=step.index,
        anchor_event_id=event.identity_key,
        anchor_event_time=event.event_time,
        t0=event.received_at,
        direction=direction,
        reference_price=event.price,
        observed_effective_box_size=step.box_size,
        invalidation_price=invalidation if direction != "neutral" else None,
        gap_at_anchor=event.is_gap,
        as_of=as_of_json(as_of),
        evidence=evidence,
        record_hash="",
    )
    sealed = replace(unsealed, record_hash=record_hash(unsealed))
    state.records.append(sealed)
    state.chain = chain_hash(state.chain, sealed.record_hash)


def _check(condition: bool, code: str) -> None:
    if not condition:
        raise LookAheadViolation(f"evidence_order_invalid:{code}")
