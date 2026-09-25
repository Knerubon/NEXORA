"""Validation orchestration: input -> capture -> causal checks -> labeling -> metrics.

Pure and offline: reads no wall clock, environment or randomness, and writes nothing.
Persisting a result is a separate, explicit step (`nexora.validation.artifact`).
"""

from __future__ import annotations

from nexora.artifacts import canonical_hash
from nexora.backtest.models import DatasetManifest
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import PipelineConfig
from nexora.research.checkpoint import code_fingerprint
from nexora.validation.capture import capture
from nexora.validation.causal import verify_causality
from nexora.validation.inputs import verify_input
from nexora.validation.labeling import definition_hash, label
from nexora.validation.metrics import summarize
from nexora.validation.models import (
    FRAMEWORK_VERSION,
    RUN_ID_PREFIX,
    CausalReport,
    DatasetProvenance,
    DefinitionHash,
    EngineProvenance,
    LookAheadViolation,
    ObservationRecord,
    ResolutionProvenance,
    SourceRevision,
    ValidationInputError,
    ValidationResult,
    ValidationRunManifest,
    ValidationSpec,
)
from nexora.validation.sealing import GENESIS_CHAIN

# ADR-014 datasets carry recorded UTC event_time/received_at only; any ADR-019/ADR-025
# offset in force when they were recorded is not re-derived or re-applied here.
TIME_CONTRACT = "dataset_recorded_utc_event_time_and_received_at"
BASE_NOTES = (
    "research_only_evidence_not_configuration",
    "generation_mode=RECOMPUTED",
    "citation_also_requires_quant_approved_preregistered_definitions",
)


def build_manifest(
    dataset: DatasetManifest,
    events: tuple[NormalizedPriceEvent, ...],
    pipeline: PipelineConfig,
    spec: ValidationSpec,
    source_revision: SourceRevision,
) -> ValidationRunManifest:
    return ValidationRunManifest(
        schema_version=1,
        framework_version=FRAMEWORK_VERSION,
        generation_mode="RECOMPUTED",
        time_contract=TIME_CONTRACT,
        dataset=DatasetProvenance(
            dataset_id=dataset.dataset_id,
            dataset_hash=canonical_hash(dataset),
            parent_dataset_id=dataset.parent_dataset_id,
            source=dataset.source,
            symbol=dataset.symbol,
            price_source=dataset.price_source,
            units=dataset.units,
            timezone=dataset.timezone,
            range_start=dataset.range_start,
            range_end=dataset.range_end,
            schema_version=dataset.schema_version,
            normalizer_version=dataset.normalizer_version,
            quality_status=dataset.quality_status,
            event_count=len(events),
            first_event_id=events[0].identity_key,
            last_event_id=events[-1].identity_key,
        ),
        engines=EngineProvenance(
            pipeline_config_hash=canonical_hash(pipeline),
            pipeline_version=pipeline.version,
            structure_resolution=pipeline.structure_resolution,
            resolutions=tuple(
                ResolutionProvenance(
                    name=r.name,
                    pnf_version=r.pnf.version,
                    box_size=r.pnf.box_size,
                    reversal_boxes=r.pnf.reversal_boxes,
                    sizing_mode=r.sizing.mode,
                    sizing_rule_version=r.sizing.rule_version,
                )
                for r in pipeline.resolutions
            ),
            signal_config_version=pipeline.signals.version,
            regime_config_version=pipeline.regime.version,
            code_fingerprint=code_fingerprint(),
        ),
        pipeline=pipeline,
        spec=spec,
        definition_hashes=tuple(
            DefinitionHash(d.definition_id, definition_hash(d)) for d in spec.outcome_definitions
        ),
        source_revision=source_revision,
    )


def run_id_for(manifest: ValidationRunManifest) -> str:
    return RUN_ID_PREFIX + canonical_hash(manifest)


def run_validation(
    *,
    dataset: DatasetManifest,
    events: tuple[NormalizedPriceEvent, ...],
    expected_dataset_hash: str,
    pipeline: PipelineConfig,
    spec: ValidationSpec,
    source_revision: SourceRevision,
) -> ValidationResult:
    verify_input(dataset, events, expected_dataset_hash=expected_dataset_hash, pipeline=pipeline)
    if any(cut >= len(events) for cut in spec.causal_cut_points):
        raise ValidationInputError("causal_cut_point_out_of_range")
    manifest = build_manifest(dataset, events, pipeline, spec, source_revision)
    run_id = run_id_for(manifest)
    try:
        captured = capture(
            iter(events),
            pipeline_config=pipeline,
            subject_kinds=spec.subject_kinds,
            baseline=spec.baseline,
        )
    except LookAheadViolation as violation:
        return _invalid(run_id, manifest, violation.code, None, (), GENESIS_CHAIN)
    try:
        causal = verify_causality(
            events,
            captured.observations,
            cut_points=spec.causal_cut_points,
            pipeline_config=pipeline,
            subject_kinds=spec.subject_kinds,
            baseline=spec.baseline,
        )
    except LookAheadViolation as violation:
        return _invalid(
            run_id, manifest, violation.code, None, captured.observations, captured.chain_hash
        )
    if causal.status != "PASSED":
        return _invalid(
            run_id,
            manifest,
            "causal_acceptance_failed",
            causal,
            captured.observations,
            captured.chain_hash,
        )
    outcomes = label(
        captured.observations,
        events,
        spec.outcome_definitions,
        pnf_captured="pnf.transition" in spec.subject_kinds,
    )
    metrics = summarize(
        captured.observations, outcomes, spec.outcome_definitions, baseline=spec.baseline
    )
    return ValidationResult(
        run_id=run_id,
        manifest=manifest,
        status="VALID",
        invalid_reason=None,
        qualification="REVISION_QUALIFIED" if source_revision.qualified else "DEVELOPMENT_ONLY",
        causal=causal,
        observations=captured.observations,
        observation_chain_hash=captured.chain_hash,
        outcomes=outcomes,
        metrics=metrics,
        notes=BASE_NOTES,
    )


def _invalid(
    run_id: str,
    manifest: ValidationRunManifest,
    reason: str,
    causal: CausalReport | None,
    observations: tuple[ObservationRecord, ...],
    chain: str,
) -> ValidationResult:
    """An invalid replay is kept with its reason, but it never carries outcomes or metrics."""
    return ValidationResult(
        run_id=run_id,
        manifest=manifest,
        status="INVALID",
        invalid_reason=reason,
        qualification="DEVELOPMENT_ONLY",
        causal=causal,
        observations=observations,
        observation_chain_hash=chain,
        outcomes=(),
        metrics=None,
        notes=(*BASE_NOTES, "invalid_replay_no_outcomes_or_metrics"),
    )
