"""EMERGENCY recovery PLANNING (ADR-035 section 6) - authorization DISABLED.

Pure, deterministic, broker-agnostic. This module only COMPUTES a
``RecoveryPlan`` (evidence-derived target + audit fields). It never returns a
recovered ``PositionRecord``, never mutates anything, performs no I/O, reads no
clock, and exposes no apply/enable step.

OPEN-20 (recovery authorization model) is UNRESOLVED (ADR-035 R1): recovery
fails closed at the authorization boundary. ``RecoveryPlan.authorized`` is a
constant ``False`` property; there is no flag, parameter or default that can
change it. ``operator_ref`` is audit identity only and has no effect on any
outcome. No timeout/age input exists: elapsed time is never evidence. The
target is derived from verified reconciliation evidence by the PR-1b
``derive_recovery_target`` (not duplicated here); it is never chosen by a
caller. Freshness (OPEN-1) has no approved bound and is not evaluated; the
plan lists it as an unresolved gate.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nexora.autonomous_contracts import TradeState
from nexora.execution.reconciler import derive_recovery_target
from nexora.execution.reconciliation import ReconciliationRecord
from nexora.position.models import PositionRecord

RECOVERY_AUTHORIZATION_NOT_RESOLVED = "recovery_authorization_not_resolved"
UNRESOLVED_RECOVERY_GATES: tuple[str, ...] = ("OPEN-20", "OPEN-1")


class RecoveryRefusalReason(StrEnum):
    """Why a plan carries no target (position stays EMERGENCY)."""

    POSITION_NOT_EMERGENCY = "position_not_emergency"
    EVIDENCE_POSITION_MISMATCH = "evidence_position_mismatch"
    EVIDENCE_NOT_VERIFIED = "evidence_not_verified"
    EVIDENCE_STALE_VS_POSITION = "evidence_stale_vs_position"


class RecoveryPlanOutcome(StrEnum):
    """Only non-applying outcomes exist: no APPLIED/AUTHORIZED member."""

    REFUSED = "REFUSED"  # no verified target; remains EMERGENCY
    DENIED = "DENIED"  # target derived, authorization boundary denies


@dataclass(frozen=True, slots=True, kw_only=True)
class EmergencyRecoveryEvidence:
    """Verified-evidence input (ADR-035 section 6). Records of ONE run."""

    position_ref: str
    records: tuple[ReconciliationRecord, ...]
    snapshot_complete: bool
    evidence_ref: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.position_ref.strip() or not self.evidence_ref.strip():
            raise ValueError("missing_recovery_evidence_identity")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("recovery_evidence_observed_at_requires_timezone")


@dataclass(frozen=True, slots=True, kw_only=True)
class EmergencyRecoveryAudit:
    """Audit-record SHAPE of a plan (not a recovery record; nothing applied)."""

    position_ref: str
    from_state: TradeState
    derived_target: TradeState | None
    evidence_ref: str
    operator_ref: str  # audit identity only; NOT authorization
    planned_at: datetime
    outcome: RecoveryPlanOutcome
    reason: str

    @property
    def authorized(self) -> bool:
        """Constant ``False``; not a settable field."""

        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlan:
    """Result of planning. Never an applied recovery; never authorized."""

    position_ref: str
    from_state: TradeState
    derived_target: TradeState | None
    outcome: RecoveryPlanOutcome
    reason: str
    evidence_ref: str
    operator_ref: str
    planned_at: datetime
    unresolved_gates: tuple[str, ...] = UNRESOLVED_RECOVERY_GATES

    @property
    def authorized(self) -> bool:
        """Constant ``False`` (OPEN-20 unresolved). Not settable or derivable."""

        return False

    @property
    def audit(self) -> EmergencyRecoveryAudit:
        return EmergencyRecoveryAudit(
            position_ref=self.position_ref,
            from_state=self.from_state,
            derived_target=self.derived_target,
            evidence_ref=self.evidence_ref,
            operator_ref=self.operator_ref,
            planned_at=self.planned_at,
            outcome=self.outcome,
            reason=self.reason,
        )


def _refused(
    position: PositionRecord,
    evidence: EmergencyRecoveryEvidence,
    operator_ref: str,
    planned_at: datetime,
    reason: RecoveryRefusalReason,
) -> RecoveryPlan:
    return RecoveryPlan(
        position_ref=position.position_id,
        from_state=position.state,
        derived_target=None,
        outcome=RecoveryPlanOutcome.REFUSED,
        reason=reason.value,
        evidence_ref=evidence.evidence_ref,
        operator_ref=operator_ref,
        planned_at=planned_at,
    )


def _stale_vs_position(
    position: PositionRecord, records: Iterable[ReconciliationRecord], observed_at: datetime
) -> bool:
    for record in records:
        if record.observed_at != observed_at:
            return True
        if (
            record.position_ref == position.position_id
            and record.local_quantity is not None
            and record.local_quantity != position.quantity
        ):
            return True
    return False


def plan_emergency_recovery(
    position: PositionRecord,
    evidence: EmergencyRecoveryEvidence,
    *,
    operator_ref: str,
    planned_at: datetime,
) -> RecoveryPlan:
    """Plan (never apply) an EMERGENCY recovery. Pure; always unauthorized.

    ``operator_ref`` is recorded for audit only and never changes the outcome;
    ``planned_at`` is a caller-supplied audit timestamp, never evidence of age.
    A derived target yields ``DENIED`` with ``recovery_authorization_not_resolved``
    (OPEN-20); anything else yields ``REFUSED`` and the position stays EMERGENCY.
    """

    if position.state is not TradeState.EMERGENCY:
        return _refused(
            position,
            evidence,
            operator_ref,
            planned_at,
            RecoveryRefusalReason.POSITION_NOT_EMERGENCY,
        )
    if evidence.position_ref != position.position_id:
        return _refused(
            position,
            evidence,
            operator_ref,
            planned_at,
            RecoveryRefusalReason.EVIDENCE_POSITION_MISMATCH,
        )
    if _stale_vs_position(position, evidence.records, evidence.observed_at):
        return _refused(
            position,
            evidence,
            operator_ref,
            planned_at,
            RecoveryRefusalReason.EVIDENCE_STALE_VS_POSITION,
        )
    target = derive_recovery_target(
        evidence.records, position.position_id, snapshot_complete=evidence.snapshot_complete
    )
    if target is None:
        return _refused(
            position,
            evidence,
            operator_ref,
            planned_at,
            RecoveryRefusalReason.EVIDENCE_NOT_VERIFIED,
        )
    return RecoveryPlan(
        position_ref=position.position_id,
        from_state=position.state,
        derived_target=target,
        outcome=RecoveryPlanOutcome.DENIED,
        reason=RECOVERY_AUTHORIZATION_NOT_RESOLVED,
        evidence_ref=evidence.evidence_ref,
        operator_ref=operator_ref,
        planned_at=planned_at,
    )
