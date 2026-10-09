"""Mandatory audit gate: Market Data -> Quality -> SignalEngine -> **Audit** -> downstream.

The gate never submits orders, never calls execution/risk code and never authorizes
anything. It only guarantees that a decision is durably audited before it may be
considered "eligible" by the existing downstream Risk/Authority/Execution gates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nexora.data_quality import QualityVerdict
from nexora.decision_audit.builder import build_decision_audit_record
from nexora.decision_audit.models import AuditError, DecisionAuditRecord
from nexora.decision_audit.store import DecisionAuditStore
from nexora.market_data.models import NormalizedPriceEvent


class AuditGateOutcome(StrEnum):
    RECORDED_ELIGIBLE = "recorded_eligible"
    RECORDED_DENIED_DATA_QUALITY = "recorded_denied_data_quality"
    RECORDED_NOT_APPLICABLE = "recorded_not_applicable"  # WAIT: audited, nothing to open
    DENIED_AUDIT_FAILURE = "denied_audit_failure"


@dataclass(frozen=True, slots=True)
class AuditGateResult:
    outcome: AuditGateOutcome
    # True only when audit persisted AND data quality is ok AND the decision is BUY/SELL.
    # This is eligibility for the existing downstream gates, never an execution permit.
    new_trade_eligible: bool
    record: DecisionAuditRecord | None
    failure_code: str | None


class DecisionAuditGate:
    """No enable flag, no environment switch, no skip path: auditing is mandatory."""

    def __init__(self, store: DecisionAuditStore) -> None:
        self._store = store

    def record_decision(
        self,
        *,
        output: Mapping[str, Any],
        event: NormalizedPriceEvent,
        quality: QualityVerdict,
        instrument_id: str | None = None,
        risk_authority_outcome: str | None = None,
        lifecycle_ref: str | None = None,
    ) -> AuditGateResult:
        try:
            record = build_decision_audit_record(
                output=output,
                event=event,
                quality=quality,
                environment=self._store.environment,
                instrument_id=instrument_id,
                risk_authority_outcome=risk_authority_outcome,
                lifecycle_ref=lifecycle_ref,
            )
            self._store.append(record)
        except AuditError as exc:
            return _denied(exc.code)
        except Exception:
            return _denied("audit_unexpected_failure")

        if record.action == "WAIT":
            return AuditGateResult(AuditGateOutcome.RECORDED_NOT_APPLICABLE, False, record, None)
        if record.trade_eligibility == "eligible_for_downstream_gates":
            return AuditGateResult(AuditGateOutcome.RECORDED_ELIGIBLE, True, record, None)
        return AuditGateResult(AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY, False, record, None)


def _denied(code: str) -> AuditGateResult:
    return AuditGateResult(AuditGateOutcome.DENIED_AUDIT_FAILURE, False, None, code)
