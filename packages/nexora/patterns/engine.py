"""Causal P&F Pattern Engine V1 (ADR-024), pure core.

Consumes ``StructureEngine``'s confirmed pivots and the P&F transitions that produced
them; it never rediscovers pivots. The engine produces evidence only: no BUY/SELL/WAIT,
score, relation, entry, stop, target, readiness or risk.

State is an immutable ``PatternEngineState`` replaced atomically per transition, so a
rejected or failing step never leaves partial state. Everything is a pure function of
the inputs and the startup configuration: no wall clock, environment, filesystem,
network or database access.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.features import (
    PATTERN_ENGINE_FEATURE_ID,
    FeatureHealth,
    FeatureLifecycle,
    FeatureStatus,
    FeatureUnitStatus,
    ResolvedFeatureConfig,
    default_features_config,
)
from nexora.patterns.legacy import LEGACY_UNITS, WINDOW_BOUND, LegacyUnit
from nexora.patterns.models import (
    EMPTY_STATE,
    PatternAlgorithmDescriptor,
    PatternAnchor,
    PatternEngineSnapshot,
    PatternEngineState,
    PatternResult,
    PatternStep,
    WindowPivot,
)
from nexora.pnf.models import PnfTransition
from nexora.structure.models import StructureSnapshot

ENGINE_VERSION = "pattern-engine-v1"
# StructureEngine confirms the centre of its last three transitions (ADR-009), so only
# the previous and the current transition can still become a pivot source.
PENDING_BOUND = 2

_log = logging.getLogger(__name__)


class _StepRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _UnitConfig:
    unit: LegacyUnit
    configured: FeatureLifecycle
    effective: FeatureLifecycle
    descriptor: PatternAlgorithmDescriptor


@dataclass(frozen=True, slots=True)
class _EventOutcome:
    changed: tuple[PatternResult, ...]
    engine_reasons: tuple[str, ...]
    unit_failures: tuple[tuple[str, str], ...]


_NO_EVENT = _EventOutcome(changed=(), engine_reasons=(), unit_failures=())


def parameters_hash(parameters: tuple[tuple[str, str], ...]) -> str:
    """Order-independent hash of canonical parameter values."""
    return canonical_hash(dict(parameters))


def pattern_id(
    *,
    symbol: str,
    resolution: str,
    algorithm_id: str,
    algorithm_version: str,
    parameters_hash: str,
    anchor_ids: tuple[str, ...],
) -> str:
    """Stable identity (ADR-024 Decision 5): no time, lifecycle or host input."""
    return canonical_hash(
        (symbol, resolution, algorithm_id, algorithm_version, parameters_hash, anchor_ids)
    )


class PatternEngine:
    __slots__ = ("symbol", "resolution", "price_tolerance", "features", "_units", "_state")

    def __init__(
        self,
        *,
        symbol: str,
        resolution: str,
        price_tolerance: Decimal,
        features: ResolvedFeatureConfig | None = None,
    ) -> None:
        if not symbol or not resolution:
            raise ValueError("invalid_pattern_engine_config")
        if not price_tolerance.is_finite() or price_tolerance < 0:
            raise ValueError("invalid_pattern_parameters")
        self.symbol = symbol
        self.resolution = resolution
        self.price_tolerance = price_tolerance
        self.features = features if features is not None else default_features_config()
        parameters = (("price_tolerance", str(canonical_serialize(price_tolerance))),)
        digest = parameters_hash(parameters)
        self._units = tuple(
            _UnitConfig(
                unit=unit,
                configured=self.features.configured_unit(
                    PATTERN_ENGINE_FEATURE_ID, unit.algorithm_id
                ),
                effective=self.features.effective_unit(
                    PATTERN_ENGINE_FEATURE_ID, unit.algorithm_id
                ),
                descriptor=PatternAlgorithmDescriptor(
                    algorithm_id=unit.algorithm_id,
                    algorithm_version=unit.algorithm_version,
                    parameters=parameters,
                    parameters_hash=digest,
                ),
            )
            for unit in LEGACY_UNITS
        )
        self._state = EMPTY_STATE

    @property
    def effective_lifecycle(self) -> FeatureLifecycle:
        return self.features.effective_feature(PATTERN_ENGINE_FEATURE_ID)

    @property
    def state(self) -> PatternEngineState:
        return self._state

    def process_event(self, steps: Sequence[PatternStep]) -> PatternEngineSnapshot:
        """Advance through one event's transitions and publish its snapshot."""
        return self._snapshot(self._advance(steps))

    def replay_event(self, steps: Sequence[PatternStep]) -> None:
        """Advance exactly like ``process_event`` without building a snapshot."""
        self._advance(steps)

    def snapshot(self) -> PatternEngineSnapshot:
        """Snapshot of the current state outside an event (no ``changed`` results)."""
        return self._snapshot(_NO_EVENT)

    # --- processing -------------------------------------------------------------------

    def _advance(self, steps: Sequence[PatternStep]) -> _EventOutcome:
        if self.effective_lifecycle == "DISABLED":
            # DISABLED skips computation and keeps no state (ADR-023 Decision 2).
            return _NO_EVENT
        changed: list[PatternResult] = []
        engine_reasons: list[str] = []
        failures: dict[str, str] = {}
        for transition, structure in steps:
            try:
                self._state = self._step(self._state, transition, structure, changed, failures)
            except _StepRejected as rejected:
                engine_reasons.append(rejected.code)
            except Exception:  # noqa: BLE001 - a SHADOW feature must never fail the pipeline
                _log.exception("Pattern engine step failed; state unchanged")
                engine_reasons.append("engine_exception")
        return _EventOutcome(
            changed=tuple(changed),
            engine_reasons=tuple(dict.fromkeys(engine_reasons)),
            unit_failures=tuple(sorted(failures.items())),
        )

    def _step(
        self,
        state: PatternEngineState,
        transition: PnfTransition,
        structure: StructureSnapshot,
        changed: list[PatternResult],
        failures: dict[str, str],
    ) -> PatternEngineState:
        if transition.symbol != self.symbol or structure.symbol != self.symbol:
            raise _StepRejected("symbol_mismatch")
        if any(key == transition.identity_key for key, _ in state.pending):
            raise _StepRejected("duplicate_transition")
        sequence = state.sequence + 1
        pivots = structure.pivots
        if structure.sequence != sequence or len(pivots) < state.pivot_count:
            raise _StepRejected("structure_inconsistent")
        if (
            state.pivot_count
            and pivots[state.pivot_count - 1].source_transition_id != state.last_pivot_id
        ):
            raise _StepRejected("structure_inconsistent")
        new_pivots = pivots[state.pivot_count :]
        if any(pivot.confirmation_time > transition.event_time for pivot in new_pivots):
            raise _StepRejected("future_inputs")

        pending = (*state.pending, (transition.identity_key, transition.column_id))
        columns = dict(pending)
        window = state.window
        current = {result.algorithm_id: result for result in state.current}
        step_changed: list[PatternResult] = []
        step_failures: dict[str, str] = {}
        for pivot in new_pivots:
            window = (*window, WindowPivot(pivot, columns.get(pivot.source_transition_id)))
            window = window[-WINDOW_BOUND:]
            self._evaluate(window, transition, sequence, current, step_changed, step_failures)

        changed.extend(step_changed)
        failures.update(step_failures)
        return PatternEngineState(
            state_version=1,
            sequence=sequence,
            pivot_count=len(pivots),
            last_pivot_id=pivots[-1].source_transition_id if pivots else None,
            window=window,
            pending=pending[-PENDING_BOUND:],
            current=tuple(
                sorted(current.values(), key=lambda r: (r.confirmed_sequence, r.algorithm_id))
            ),
        )

    def _evaluate(
        self,
        window: tuple[WindowPivot, ...],
        transition: PnfTransition,
        sequence: int,
        current: dict[str, PatternResult],
        changed: list[PatternResult],
        failures: dict[str, str],
    ) -> None:
        """A newly confirmed pivot shifts every window: expire, then re-detect."""
        for config in self._units:
            if config.effective == "DISABLED":
                continue
            unit = config.unit
            previous = current.pop(unit.algorithm_id, None)
            if previous is not None:
                changed.append(
                    replace(
                        previous,
                        status="expired",
                        status_reason="window_superseded",
                        status_time=transition.event_time,
                        status_sequence=sequence,
                    )
                )
            if len(window) < unit.window_size:
                continue
            members = window[-unit.window_size :]
            pivots = tuple(member.pivot for member in members)
            try:
                matched = unit.matches(pivots, self.price_tolerance)
            except Exception:  # noqa: BLE001 - isolate one failing unit
                _log.exception("Pattern unit %s failed", unit.algorithm_id)
                failures[unit.algorithm_id] = "algorithm_exception"
                continue
            if not matched:
                continue
            if any(member.column_id is None for member in members):
                failures[unit.algorithm_id] = "anchor_column_unresolved"
                continue
            result = self._result(config, members, transition, sequence)
            current[unit.algorithm_id] = result
            changed.append(result)

    def _result(
        self,
        config: _UnitConfig,
        members: tuple[WindowPivot, ...],
        transition: PnfTransition,
        sequence: int,
    ) -> PatternResult:
        unit = config.unit
        anchors = tuple(
            PatternAnchor(
                role=role,
                pivot_kind=member.pivot.kind,
                price=member.pivot.price,
                column_id=_column(member),
                source_transition_id=member.pivot.source_transition_id,
                occurrence_time=member.pivot.occurrence_time,
                confirmation_time=member.pivot.confirmation_time,
            )
            for role, member in zip(unit.roles, members, strict=True)
        )
        pivots = tuple(member.pivot for member in members)
        anchor_ids = tuple(anchor.source_transition_id for anchor in anchors)
        price_low, price_high = unit.bounds(pivots)
        return PatternResult(
            pattern_id=pattern_id(
                symbol=self.symbol,
                resolution=self.resolution,
                algorithm_id=unit.algorithm_id,
                algorithm_version=unit.algorithm_version,
                parameters_hash=config.descriptor.parameters_hash,
                anchor_ids=anchor_ids,
            ),
            algorithm_id=unit.algorithm_id,
            family="legacy_pivot",
            pattern_type=unit.pattern_type,
            direction=unit.direction,
            status="confirmed",
            status_reason="detected",
            symbol=self.symbol,
            resolution=self.resolution,
            anchors=anchors,
            start_column=anchors[0].column_id,
            end_column=anchors[-1].column_id,
            confirmation_column=transition.column_id,
            confirmation_transition_id=transition.identity_key,
            price_low=price_low,
            price_high=price_high,
            start_time=anchors[0].occurrence_time,
            confirmation_time=unit.confirmation(pivots),
            status_time=transition.event_time,
            confirmed_sequence=sequence,
            status_sequence=sequence,
            evidence_code=unit.evidence_code,
            evidence=unit.facts(pivots, self.price_tolerance),
            algorithm_version=unit.algorithm_version,
            parameters_hash=config.descriptor.parameters_hash,
            lifecycle=config.effective,
            source_refs=(*anchor_ids, transition.identity_key),
            config_version=transition.config_version,
        )

    # --- snapshot / health ------------------------------------------------------------

    def _snapshot(self, outcome: _EventOutcome) -> PatternEngineSnapshot:
        units = self._unit_statuses(outcome)
        health, reasons = self._engine_health(outcome, units)
        feature = self.features.feature(PATTERN_ENGINE_FEATURE_ID)
        status = FeatureStatus(
            schema_version=1,
            feature_id=PATTERN_ENGINE_FEATURE_ID,
            feature_class=feature.feature_class,
            configured_lifecycle=feature.lifecycle,
            effective_lifecycle=self.effective_lifecycle,
            health=health,
            reason_codes=reasons,
            engine_version=ENGINE_VERSION,
            feature_config_version=self.features.version,
            feature_config_hash=self.features.feature_config_hash,
            units=units,
        )
        # Fail closed: a step rejected in this event publishes no current evidence.
        current = () if outcome.engine_reasons else self._state.current
        return PatternEngineSnapshot(
            schema_version=1,
            symbol=self.symbol,
            sequence=self._state.sequence,
            status=status,
            algorithms=tuple(config.descriptor for config in self._units),
            current=current,
            changed=outcome.changed,
        )

    def _unit_statuses(self, outcome: _EventOutcome) -> tuple[FeatureUnitStatus, ...]:
        failures = dict(outcome.unit_failures)
        statuses: list[FeatureUnitStatus] = []
        for config in self._units:
            unit = config.unit
            health: FeatureHealth
            reasons: tuple[str, ...]
            if config.effective == "DISABLED":
                health, reasons = "not_evaluated", ("unit_disabled",)
                if self.effective_lifecycle == "DISABLED":
                    reasons = ("feature_disabled",)
            elif outcome.engine_reasons:
                health, reasons = "unavailable", outcome.engine_reasons
            elif unit.algorithm_id in failures:
                health, reasons = "unavailable", (failures[unit.algorithm_id],)
            elif len(self._state.window) < unit.window_size:
                health, reasons = "warmup", ("insufficient_pivots",)
            elif any(m.column_id is None for m in self._state.window[-unit.window_size :]):
                health, reasons = "unavailable", ("anchor_column_unresolved",)
            else:
                health, reasons = "ready", ()
            statuses.append(
                FeatureUnitStatus(
                    unit_id=unit.algorithm_id,
                    configured_lifecycle=config.configured,
                    effective_lifecycle=config.effective,
                    health=health,
                    reason_codes=reasons,
                    algorithm_version=unit.algorithm_version,
                )
            )
        return tuple(statuses)

    def _engine_health(
        self, outcome: _EventOutcome, units: tuple[FeatureUnitStatus, ...]
    ) -> tuple[FeatureHealth, tuple[str, ...]]:
        if self.effective_lifecycle == "DISABLED":
            return "not_evaluated", ("feature_disabled",)
        if outcome.engine_reasons:
            return "unavailable", outcome.engine_reasons
        enabled = [unit for unit in units if unit.effective_lifecycle != "DISABLED"]
        if not enabled:
            return "not_evaluated", ("no_enabled_units",)
        unavailable = [unit for unit in enabled if unit.health == "unavailable"]
        if len(unavailable) == len(enabled):
            return "unavailable", ("all_units_unavailable",)
        if unavailable:
            return "degraded", ("unit_unavailable",)
        if any(unit.health == "ready" for unit in enabled):
            return "ready", ()
        return "warmup", ("insufficient_pivots",)


def _column(member: WindowPivot) -> int:
    if member.column_id is None:  # guarded by the caller; never guess a column
        raise ValueError("anchor_column_unresolved")
    return member.column_id
