"""Replay validation contracts (ADR-030). Hashed records carry event-derived times only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.research import PipelineConfig

FRAMEWORK_VERSION = "valid1-v1"
RUN_ID_PREFIX = "valid1-"

GenerationMode = Literal["RECOMPUTED", "RECORDED"]
Direction = Literal["long", "short", "neutral"]
KnowledgeClock = Literal["event_time", "received_at"]
SubjectKind = Literal[
    "pnf.transition",
    "structure.pivot_confirmed",
    "structure.level_invalidated",
    "pattern.legacy",
    "trendline.state",
    "signal.decision",
    "signal.issued",
    "entry_readiness.state",
    "baseline.every_nth_event",
]
SUBJECT_KINDS: tuple[SubjectKind, ...] = (
    "pnf.transition",
    "structure.pivot_confirmed",
    "structure.level_invalidated",
    "pattern.legacy",
    "trendline.state",
    "signal.decision",
    "signal.issued",
    "entry_readiness.state",
    "baseline.every_nth_event",
)
WindowKind = Literal["time", "events"]
ReferenceKind = Literal["anchor_price"]
BoxUnit = Literal["none", "fixed_price_unit", "observed_effective_box_size"]
BarrierUnit = Literal["price", "boxes"]
GapPolicy = Literal["label_censored", "measure_through"]
OutcomeStatus = Literal["COMPLETE", "CENSORED_END_OF_DATA", "CENSORED_GAP", "NO_SAMPLES"]
FirstBarrier = Literal["favorable", "adverse", "none", "not_applicable"]
BoxUnitStatus = Literal["not_requested", "available", "unavailable"]
LabelStatus = Literal["evaluated", "not_applicable", "not_captured"]
RunStatus = Literal["VALID", "INVALID"]
CausalStatus = Literal["PASSED", "FAILED"]
Qualification = Literal["DEVELOPMENT_ONLY", "REVISION_QUALIFIED"]


class ValidationError(ValueError):
    """Sanitized fail-closed validation error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ValidationInputError(ValidationError):
    """The dataset, configuration or specification cannot be validated."""


class LookAheadViolation(ValidationError):
    """Decision-time evidence was known only after its decision point (ADR-030 E2)."""


class SealBroken(ValidationError):
    """A sealed Stage A record no longer matches its hash (ADR-030 G2)."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    component: str
    ref: str
    knowledge_time: datetime
    knowledge_clock: KnowledgeClock
    version: str
    payload_hash: str


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """Sealed decision-time observation. `record_hash` covers every other field."""

    schema_version: Literal[1]
    generation_mode: GenerationMode
    subject_kind: SubjectKind
    subject_group: str
    subject_id: str
    anchor_index: int
    anchor_event_id: str
    anchor_event_time: datetime
    t0: datetime
    direction: Direction
    reference_price: Decimal
    observed_effective_box_size: Decimal | None
    invalidation_price: Decimal | None
    gap_at_anchor: bool
    as_of: str
    evidence: tuple[EvidenceItem, ...]
    record_hash: str


@dataclass(frozen=True, slots=True)
class BaselineRule:
    """Explicit, pre-registered unconditional anchors; the rule itself is Quant-owned (Q-V2)."""

    every_n_events: int
    offset: int = 0

    def __post_init__(self) -> None:
        if self.every_n_events < 1 or self.offset < 0:
            raise ValidationInputError("invalid_baseline_rule")


@dataclass(frozen=True, slots=True)
class OutcomeDefinition:
    """A pre-registered outcome definition. No field has a trading default (Q-V2–Q-V4)."""

    definition_id: str
    version: str
    window_kind: WindowKind
    window_length: int
    reference: ReferenceKind
    box_unit: BoxUnit
    gap_policy: GapPolicy
    fixed_price_unit: Decimal | None = None
    barrier_unit: BarrierUnit | None = None
    favorable_barrier: Decimal | None = None
    adverse_barrier: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.definition_id or not self.version or self.window_length < 1:
            raise ValidationInputError("invalid_outcome_definition")
        if (self.box_unit == "fixed_price_unit") != (self.fixed_price_unit is not None):
            raise ValidationInputError("invalid_fixed_price_unit")
        if self.fixed_price_unit is not None and not _positive(self.fixed_price_unit):
            raise ValidationInputError("invalid_fixed_price_unit")
        barriers = (self.favorable_barrier, self.adverse_barrier)
        if self.barrier_unit is None and any(b is not None for b in barriers):
            raise ValidationInputError("barrier_unit_required")
        if self.barrier_unit is not None and all(b is None for b in barriers):
            raise ValidationInputError("barrier_value_required")
        if any(b is not None and not _positive(b) for b in barriers):
            raise ValidationInputError("invalid_barrier")
        if self.barrier_unit == "boxes" and self.box_unit == "none":
            raise ValidationInputError("barrier_boxes_require_box_unit")


@dataclass(frozen=True, slots=True)
class ValidationSpec:
    """Pre-registered run specification (ADR-030 G9); fixed before Stage A starts."""

    subject_kinds: tuple[SubjectKind, ...]
    outcome_definitions: tuple[OutcomeDefinition, ...]
    causal_cut_points: tuple[int, ...]
    baseline: BaselineRule | None = None

    def __post_init__(self) -> None:
        if not self.subject_kinds or len(set(self.subject_kinds)) != len(self.subject_kinds):
            raise ValidationInputError("invalid_subject_kinds")
        if any(kind not in SUBJECT_KINDS for kind in self.subject_kinds):
            raise ValidationInputError("unknown_subject_kind")
        if ("baseline.every_nth_event" in self.subject_kinds) != (self.baseline is not None):
            raise ValidationInputError("baseline_rule_mismatch")
        ids = [d.definition_id for d in self.outcome_definitions]
        if not ids or len(set(ids)) != len(ids):
            raise ValidationInputError("invalid_outcome_definitions")
        cuts = self.causal_cut_points
        # The causal properties are acceptance requirements, never optional (Rin 2026-09-25).
        if not cuts or any(c < 1 for c in cuts) or tuple(sorted(set(cuts))) != cuts:
            raise ValidationInputError("causal_cut_points_required")


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    """Future-price label for one sealed observation under one definition."""

    schema_version: Literal[1]
    outcome_id: str
    observation_hash: str
    definition_id: str
    definition_hash: str
    subject_group: str
    direction: Direction
    status: OutcomeStatus
    anchor_index: int
    window_end_index: int | None
    sample_count: int
    sample_first_index: int | None
    sample_last_index: int | None
    gap_observed: bool
    max_sample_gap_seconds: Decimal | None
    reference_price: Decimal
    box_unit_status: BoxUnitStatus
    box_unit_price: Decimal | None
    up_excursion: Decimal | None
    down_excursion: Decimal | None
    mfe: Decimal | None
    mae: Decimal | None
    mfe_boxes: Decimal | None
    mae_boxes: Decimal | None
    time_to_mfe_seconds: Decimal | None
    time_to_mae_seconds: Decimal | None
    endpoint_index: int | None
    endpoint_price: Decimal | None
    endpoint_change: Decimal | None
    endpoint_change_boxes: Decimal | None
    first_barrier: FirstBarrier
    first_barrier_index: int | None
    time_to_first_barrier_seconds: Decimal | None
    barrier_overshoot: Decimal | None
    invalidation_status: LabelStatus
    invalidation_index: int | None
    adverse_reversal_status: LabelStatus
    adverse_reversal_index: int | None
    sampled_only: bool


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    field: str
    count: int
    minimum: Decimal
    p25: Decimal
    median: Decimal
    p75: Decimal
    maximum: Decimal
    mean: Decimal


@dataclass(frozen=True, slots=True)
class CountItem:
    key: str
    count: int


@dataclass(frozen=True, slots=True)
class GroupMetrics:
    subject_group: str
    definition_id: str
    definition_hash: str
    n_observations: int
    status_counts: tuple[CountItem, ...]
    complete_count: int
    distributions: tuple[DistributionSummary, ...]
    first_barrier_counts: tuple[CountItem, ...]
    invalidation_evaluated: int
    invalidation_hits: int
    adverse_reversal_evaluated: int
    adverse_reversals: int
    overlapping_pairs: int
    max_concurrent_windows: int
    baseline_group: str | None


@dataclass(frozen=True, slots=True)
class MetricsReport:
    """Descriptive only: counts and denominators, no rates, scores or probabilities."""

    schema_version: Literal[1]
    quantile_method: Literal["nearest_rank"]
    baseline_status: Literal["configured", "not_configured"]
    groups: tuple[GroupMetrics, ...]
    groups_evaluated: int
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetProvenance:
    dataset_id: str
    dataset_hash: str
    parent_dataset_id: str | None
    source: str
    symbol: str
    price_source: str
    units: str
    timezone: str
    range_start: datetime
    range_end: datetime
    schema_version: int
    normalizer_version: str
    quality_status: str
    event_count: int
    first_event_id: str
    last_event_id: str


@dataclass(frozen=True, slots=True)
class ResolutionProvenance:
    name: str
    pnf_version: str
    box_size: Decimal
    reversal_boxes: int
    sizing_mode: str
    sizing_rule_version: str


@dataclass(frozen=True, slots=True)
class EngineProvenance:
    pipeline_config_hash: str
    pipeline_version: str
    structure_resolution: str
    resolutions: tuple[ResolutionProvenance, ...]
    signal_config_version: str
    regime_config_version: str
    code_fingerprint: str


@dataclass(frozen=True, slots=True)
class SourceRevision:
    """Committed source identity; `None` means unknown (never official evidence, Q-V7)."""

    commit: str | None
    dirty: bool | None

    @property
    def qualified(self) -> bool:
        commit = self.commit or ""
        return (
            self.dirty is False
            and len(commit) == 40
            and all(c in "0123456789abcdef" for c in commit)
        )


@dataclass(frozen=True, slots=True)
class DefinitionHash:
    definition_id: str
    definition_hash: str


@dataclass(frozen=True, slots=True)
class ValidationRunManifest:
    """Hashed run identity: `run_id = RUN_ID_PREFIX + canonical_hash(manifest)`."""

    schema_version: Literal[1]
    framework_version: str
    generation_mode: GenerationMode
    time_contract: str
    dataset: DatasetProvenance
    engines: EngineProvenance
    pipeline: PipelineConfig
    spec: ValidationSpec
    definition_hashes: tuple[DefinitionHash, ...]
    source_revision: SourceRevision


@dataclass(frozen=True, slots=True)
class CutCheck:
    cut_index: int
    property: Literal["prefix_invariance", "future_mutation_invariance"]
    mutation: str
    observations_compared: int
    passed: bool
    first_mismatch_index: int | None
    mutation_changed_tail: bool


@dataclass(frozen=True, slots=True)
class CausalReport:
    status: CausalStatus
    checks: tuple[CutCheck, ...]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    run_id: str
    manifest: ValidationRunManifest
    status: RunStatus
    invalid_reason: str | None
    qualification: Qualification
    causal: CausalReport | None
    observations: tuple[ObservationRecord, ...]
    observation_chain_hash: str
    outcomes: tuple[OutcomeRecord, ...]
    metrics: MetricsReport | None
    notes: tuple[str, ...]


def _positive(value: Decimal) -> bool:
    return value.is_finite() and value > 0
