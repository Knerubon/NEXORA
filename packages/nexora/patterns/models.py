"""P&F Pattern Engine V1 contracts (ADR-024 Decisions 4, 7 and 13)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.features import FeatureLifecycle, FeatureStatus
from nexora.pnf.models import PnfTransition
from nexora.structure.models import ConfirmedPivot, StructureSnapshot

PatternFamily = Literal["legacy_pivot"]  # "classic_pnf" is added by the Decision 12 ADR
PatternResultStatus = Literal["confirmed", "expired"]
PatternDirection = Literal["bullish", "bearish"]

# One P&F transition and the Structure snapshot it produced, in pipeline order.
PatternStep = tuple[PnfTransition, StructureSnapshot]


@dataclass(frozen=True, slots=True)
class PatternAnchor:
    role: str
    pivot_kind: Literal["high", "low"]
    price: Decimal
    column_id: int
    source_transition_id: str
    occurrence_time: datetime
    confirmation_time: datetime


@dataclass(frozen=True, slots=True)
class PatternResult:
    pattern_id: str
    algorithm_id: str
    family: PatternFamily
    pattern_type: str
    direction: PatternDirection
    status: PatternResultStatus
    status_reason: str
    symbol: str
    resolution: str
    anchors: tuple[PatternAnchor, ...]
    start_column: int
    end_column: int
    confirmation_column: int
    confirmation_transition_id: str
    price_low: Decimal
    price_high: Decimal
    start_time: datetime
    confirmation_time: datetime
    status_time: datetime
    confirmed_sequence: int
    status_sequence: int
    evidence_code: str
    evidence: tuple[str, ...]
    algorithm_version: str
    parameters_hash: str
    lifecycle: FeatureLifecycle
    source_refs: tuple[str, ...]
    config_version: str


@dataclass(frozen=True, slots=True)
class PatternAlgorithmDescriptor:
    algorithm_id: str
    algorithm_version: str
    parameters: tuple[tuple[str, str], ...]
    parameters_hash: str


@dataclass(frozen=True, slots=True)
class PatternEngineSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    status: FeatureStatus
    algorithms: tuple[PatternAlgorithmDescriptor, ...]
    current: tuple[PatternResult, ...]
    changed: tuple[PatternResult, ...]


@dataclass(frozen=True, slots=True)
class WindowPivot:
    """A consumed confirmed pivot with its anchor column (``None`` = unresolved)."""

    pivot: ConfirmedPivot
    column_id: int | None


@dataclass(frozen=True, slots=True)
class PatternEngineState:
    """Bounded replay-derived state needed to resume incremental detection (Decision 13c).

    Configuration (lifecycles, parameters, descriptors, feature_config_hash) is never
    part of this state. It is rebuilt from startup configuration.
    """

    state_version: Literal[1]
    sequence: int
    pivot_count: int
    last_pivot_id: str | None
    window: tuple[WindowPivot, ...]
    pending: tuple[tuple[str, int], ...]
    current: tuple[PatternResult, ...]


EMPTY_STATE = PatternEngineState(
    state_version=1,
    sequence=0,
    pivot_count=0,
    last_pivot_id=None,
    window=(),
    pending=(),
    current=(),
)
