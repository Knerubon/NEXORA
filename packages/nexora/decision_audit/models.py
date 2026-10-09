"""Decision Audit Trail V1 schema (ADR-036).

Records are frozen, canonical-serializable and append-only. They reference engine output;
they never recompute pattern/structure logic and never invent signal IDs or evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

AUDIT_SCHEMA_VERSION: Literal[1] = 1
AuditAction = Literal["BUY", "SELL", "WAIT"]
# Eligibility is NOT execution authorization: downstream Risk/Authority/Execution gates
# keep full, independent authority. "eligible" only means audit + data quality did not deny.
TradeEligibility = Literal[
    "eligible_for_downstream_gates",
    "denied_data_quality",
    # BUY/SELL decision without a genuinely emitted, valid signal (for example the engine
    # suppressed it as a duplicate of an active signal): nothing new exists to open.
    "denied_no_emitted_signal",
    # BUY/SELL decision whose action, signal identity or evidence disagree with each other.
    "denied_inconsistent_output",
    "not_applicable",
]


class AppendOutcome(StrEnum):
    """Result of a successful append. A replay is never a fresh authorization."""

    CREATED = "created"
    REPLAYED = "replayed"  # byte-identical record already stored; nothing was written


class AuditError(Exception):
    """Sanitized audit failure; the code never carries payload or secret content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InstrumentIdentity:
    symbol: str
    source: str
    units: str
    instrument_id: str | None  # canonical broker-agnostic id (ADR-025) when the caller has one


@dataclass(frozen=True, slots=True)
class MarketDataReference:
    event_identity_key: str
    event_time: datetime
    received_at: datetime
    source_sequence: int
    price_source: str
    snapshot_hash: str | None  # canonical hash of the evaluated market-data snapshot


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    component: str
    code: str
    polarity: str | None
    reference: str


@dataclass(frozen=True, slots=True)
class AuditBlocker:
    source: str  # entry_readiness | negative_evidence | data_quality
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class AuditVersions:
    pipeline_config: str
    signal_config: str
    signal_engine: str
    regime_config: str | None
    entry_readiness_config: str | None
    audit_schema: Literal[1]


@dataclass(frozen=True, slots=True)
class DataQualityRef:
    state: str
    new_trade_permitted: bool
    finding_codes: tuple[str, ...]
    config_version: str
    snapshot_hash: str | None
    evaluated_at: datetime | None


@dataclass(frozen=True, slots=True)
class DecisionAuditRecord:
    schema_version: Literal[1]
    decision_id: str
    correlation_id: str
    decided_at: datetime
    environment: str
    instrument: InstrumentIdentity
    market_data: MarketDataReference
    action: AuditAction
    score: int
    # Only a real ResearchSignal id; None for every WAIT and for suppressed duplicates.
    signal_id: str | None
    signal_emitted: bool
    signal_sequence: int
    reasons: tuple[str, ...]
    reason_codes: tuple[str, ...]
    blockers: tuple[AuditBlocker, ...]
    confirmation_requirements: tuple[str, ...]
    future_conditions: tuple[str, ...]
    entry_readiness_state: str | None
    evidence: tuple[EvidenceRef, ...]
    # Evidence the engines did not provide, recorded explicitly instead of fabricated.
    evidence_gaps: tuple[str, ...]
    versions: AuditVersions
    data_quality: DataQualityRef
    trade_eligibility: TradeEligibility
    # Optional opaque downstream references; V1 never derives these itself.
    risk_authority_outcome: str | None = None
    lifecycle_ref: str | None = None
