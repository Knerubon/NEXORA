"""Mandatory, append-only, fail-closed Decision Audit Trail V1 (ADR-036)."""

from nexora.decision_audit.builder import build_decision_audit_record
from nexora.decision_audit.gate import AuditGateOutcome, AuditGateResult, DecisionAuditGate
from nexora.decision_audit.models import (
    AUDIT_SCHEMA_VERSION,
    AuditBlocker,
    AuditError,
    AuditVersions,
    DataQualityRef,
    DecisionAuditRecord,
    EvidenceRef,
    InstrumentIdentity,
    MarketDataReference,
)
from nexora.decision_audit.store import DecisionAuditStore

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditBlocker",
    "AuditError",
    "AuditGateOutcome",
    "AuditGateResult",
    "AuditVersions",
    "DataQualityRef",
    "DecisionAuditGate",
    "DecisionAuditRecord",
    "DecisionAuditStore",
    "EvidenceRef",
    "InstrumentIdentity",
    "MarketDataReference",
    "build_decision_audit_record",
]
