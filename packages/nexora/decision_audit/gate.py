"""Mandatory audit gate: Market Data -> Quality -> SignalEngine -> **Audit** -> downstream.

The gate never submits orders, never calls execution/risk code and never authorizes
anything. It only guarantees that a decision is durably audited before it may be
considered "eligible" by the existing downstream Risk/Authority/Execution gates.

Controlled boundary (F1). The gate owns the ``DataQualityGuard`` and obtains the verdict
itself from the market-data snapshot; callers cannot hand it a verdict. The verdict is then
re-verified against the exact evaluated event and the recomputed snapshot hash.

Replay (P1-3). Eligibility is granted exactly once, to the call that durably *created* the
audit record. Persisting an identical record again is an idempotent no-op reported as
``RECORDED_REPLAY`` with ``new_trade_eligible=False``: a replay, crash-recovery re-run or retry
can never present an already-seen decision as a newly authorized trade.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from nexora.data_quality import DataQualityGuard, MarketDataSnapshot
from nexora.decision_audit.builder import build_decision_audit_record
from nexora.decision_audit.models import AppendOutcome, AuditError, DecisionAuditRecord
from nexora.decision_audit.store import DecisionAuditStore
from nexora.market_data.models import NormalizedPriceEvent


class AuditGateOutcome(StrEnum):
    RECORDED_ELIGIBLE = "recorded_eligible"
    RECORDED_DENIED_DATA_QUALITY = "recorded_denied_data_quality"
    RECORDED_DENIED_NO_SIGNAL = "recorded_denied_no_signal"
    RECORDED_DENIED_INCONSISTENT_OUTPUT = "recorded_denied_inconsistent_output"
    RECORDED_NOT_APPLICABLE = "recorded_not_applicable"  # WAIT: audited, nothing to open
    RECORDED_REPLAY = "recorded_replay"  # identical record already stored: never eligible
    DENIED_AUDIT_FAILURE = "denied_audit_failure"


_OUTCOME_FOR_ELIGIBILITY = {
    "eligible_for_downstream_gates": AuditGateOutcome.RECORDED_ELIGIBLE,
    "denied_data_quality": AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY,
    "denied_no_emitted_signal": AuditGateOutcome.RECORDED_DENIED_NO_SIGNAL,
    "denied_inconsistent_output": AuditGateOutcome.RECORDED_DENIED_INCONSISTENT_OUTPUT,
    "not_applicable": AuditGateOutcome.RECORDED_NOT_APPLICABLE,
}


@dataclass(frozen=True, slots=True)
class AuditGateResult:
    outcome: AuditGateOutcome
    # True only when this call durably created the audit record AND data quality is ok AND the
    # decision is BUY/SELL AND a valid signal was genuinely emitted. This is eligibility for the
    # existing downstream gates, never an execution permit, and never true for a replay.
    new_trade_eligible: bool
    record: DecisionAuditRecord | None
    failure_code: str | None


class DecisionAuditGate:
    """No enable flag, no environment switch, no skip path: auditing is mandatory."""

    def __init__(self, store: DecisionAuditStore, guard: DataQualityGuard) -> None:
        if not isinstance(store, DecisionAuditStore):
            raise TypeError("invalid_store")
        if not isinstance(guard, DataQualityGuard):
            raise TypeError("invalid_guard")
        self._store = store
        self._guard = guard

    def record_decision(
        self,
        *,
        output: Mapping[str, Any],
        event: NormalizedPriceEvent,
        snapshot: MarketDataSnapshot,
        evaluated_at: datetime | None,
        instrument_id: str | None = None,
        risk_authority_outcome: str | None = None,
        lifecycle_ref: str | None = None,
    ) -> AuditGateResult:
        try:
            record = build_decision_audit_record(
                output=output,
                event=event,
                snapshot=snapshot,
                guard=self._guard,
                evaluated_at=evaluated_at,
                environment=self._store.environment,
                instrument_id=instrument_id,
                risk_authority_outcome=risk_authority_outcome,
                lifecycle_ref=lifecycle_ref,
            )
            appended = self._store.append(record)
        except AuditError as exc:
            return _denied(exc.code)
        except Exception:
            return _denied("audit_unexpected_failure")

        if appended is AppendOutcome.REPLAYED:
            return AuditGateResult(AuditGateOutcome.RECORDED_REPLAY, False, record, None)
        outcome = _OUTCOME_FOR_ELIGIBILITY[record.trade_eligibility]
        eligible = outcome is AuditGateOutcome.RECORDED_ELIGIBLE
        return AuditGateResult(outcome, eligible, record, None)


def _denied(code: str) -> AuditGateResult:
    return AuditGateResult(AuditGateOutcome.DENIED_AUDIT_FAILURE, False, None, code)
