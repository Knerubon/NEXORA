"""Builds a DecisionAuditRecord from existing pipeline output (no new decision logic)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.data_quality import QualityVerdict
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
    TradeEligibility,
)
from nexora.market_data.models import NormalizedPriceEvent

_SECRET_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|credential|dsn|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"(://[^/\s:@]+:[^/\s@]+@|bearer\s+[a-z0-9._-]{12,})", re.IGNORECASE)


def build_decision_audit_record(
    *,
    output: Mapping[str, Any],
    event: NormalizedPriceEvent,
    quality: QualityVerdict,
    environment: str,
    instrument_id: str | None = None,
    risk_authority_outcome: str | None = None,
    lifecycle_ref: str | None = None,
) -> DecisionAuditRecord:
    """Raise ``AuditError`` when the decision cannot be reconstructed faithfully."""
    if not environment:
        raise AuditError("missing_environment")
    signals = _mapping(output.get("signals"), "missing_signals")
    decision = _mapping(signals.get("decision"), "missing_decision")
    action = decision.get("action")
    if action not in ("BUY", "SELL", "WAIT"):
        raise AuditError("invalid_action")
    pipeline_version = output.get("config_version")
    if not isinstance(pipeline_version, str) or not pipeline_version:
        raise AuditError("missing_config_version")
    sequence = signals.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise AuditError("missing_signal_sequence")
    if str(signals.get("symbol")) != event.symbol:
        raise AuditError("symbol_mismatch")

    gaps: list[str] = []
    evidence: list[EvidenceRef] = []
    blockers: list[AuditBlocker] = []

    for item in decision.get("positive_evidence", ()):
        evidence.append(_evidence(item))
    for item in decision.get("negative_evidence", ()):
        ref = _evidence(item)
        evidence.append(ref)
        blockers.append(AuditBlocker("negative_evidence", ref.code, str(item.get("reason", ""))))
    for pattern in decision.get("patterns", ()):
        evidence.append(
            EvidenceRef(
                "pattern",
                str(pattern.get("evidence_code")),
                str(pattern.get("direction")),
                f"{pattern.get('source_data_reference')}|{pattern.get('algorithm_version')}"
                f"|{pattern.get('relation')}",
            )
        )

    regime_version = _regime(output, evidence, gaps)
    _structure(output, evidence, gaps)
    _trendline(output, evidence, gaps)

    readiness = output.get("entry_readiness")
    readiness_state: str | None = None
    readiness_version: str | None = None
    confirmations: list[str] = []
    if isinstance(readiness, Mapping):
        readiness_state = str(readiness.get("state"))
        readiness_version = str(readiness.get("config_version"))
        for blocker in readiness.get("blockers", ()):
            blockers.append(
                AuditBlocker(
                    "entry_readiness", str(blocker.get("code")), str(blocker.get("reason"))
                )
            )
        for pending in readiness.get("pending_confirmations", ()):
            confirmations.append(f"{pending.get('code')}:{pending.get('line_id')}")
    else:
        gaps.append("entry_readiness:absent")

    for finding in quality.findings:
        blockers.append(AuditBlocker("data_quality", finding.code, finding.severity))

    latest = signals.get("latest")
    decided_at = event.received_at.astimezone(UTC)
    signal_id: str | None = None
    emitted = False
    reasons: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    if action != "WAIT" and isinstance(latest, Mapping):
        # `latest` persists across events; it belongs to this decision only if decided now.
        if _same_instant(latest.get("decision_time"), decided_at):
            signal_id = _opt_str(latest.get("signal_id"))
            emitted = signal_id is not None
            reasons = tuple(str(r) for r in latest.get("reasons", ()))
            reason_codes = tuple(str(c) for c in latest.get("reason_codes", ()))
    if action == "WAIT" or not emitted:
        positives = decision.get("positive_evidence", ())
        reasons = reasons or tuple(str(i.get("reason")) for i in positives)
        reason_codes = reason_codes or tuple(str(i.get("code")) for i in positives)
        if action == "WAIT" and not reason_codes:
            gaps.append("reason_codes:absent")
    if action != "WAIT" and not emitted:
        gaps.append("signal:not_emitted")

    eligibility: TradeEligibility
    if action == "WAIT":
        eligibility = "not_applicable"
    elif quality.new_trade_permitted:
        eligibility = "eligible_for_downstream_gates"
    else:
        eligibility = "denied_data_quality"

    decision_id = canonical_hash(
        {
            "kind": "decision",
            "symbol": event.symbol,
            "environment": environment,
            "signal_sequence": sequence,
            "event": event.identity_key,
            "pipeline": pipeline_version,
            "engine": str(decision.get("engine_version")),
            "schema": AUDIT_SCHEMA_VERSION,
        }
    )
    correlation_id = (
        "corr:" + hashlib.sha256(f"{event.symbol}|{event.identity_key}".encode()).hexdigest()[:32]
    )

    record = DecisionAuditRecord(
        schema_version=AUDIT_SCHEMA_VERSION,
        decision_id=decision_id,
        correlation_id=correlation_id,
        decided_at=decided_at,
        environment=environment,
        instrument=InstrumentIdentity(event.symbol, event.source, event.units, instrument_id),
        market_data=MarketDataReference(
            event_identity_key=event.identity_key,
            event_time=event.event_time,
            received_at=event.received_at,
            source_sequence=event.source_sequence,
            price_source=event.price_source,
            snapshot_hash=quality.snapshot_hash,
        ),
        action=action,
        score=int(decision.get("score", 0)),
        signal_id=signal_id,
        signal_emitted=emitted,
        signal_sequence=sequence,
        reasons=reasons,
        reason_codes=reason_codes,
        blockers=tuple(blockers),
        confirmation_requirements=tuple(confirmations),
        future_conditions=tuple(str(c) for c in decision.get("future_conditions", ())),
        entry_readiness_state=readiness_state,
        evidence=tuple(evidence),
        evidence_gaps=tuple(gaps),
        versions=AuditVersions(
            pipeline_config=pipeline_version,
            signal_config=str(decision.get("config_version")),
            signal_engine=str(decision.get("engine_version")),
            regime_config=regime_version,
            entry_readiness_config=readiness_version,
            audit_schema=AUDIT_SCHEMA_VERSION,
        ),
        data_quality=DataQualityRef(
            state=quality.state,
            new_trade_permitted=quality.new_trade_permitted,
            finding_codes=tuple(f.code for f in quality.findings),
            config_version=quality.config_version,
            snapshot_hash=quality.snapshot_hash,
            evaluated_at=quality.evaluated_at,
        ),
        trade_eligibility=eligibility,
        risk_authority_outcome=risk_authority_outcome,
        lifecycle_ref=lifecycle_ref,
    )
    assert_no_secrets(record)
    return record


def assert_no_secrets(record: DecisionAuditRecord) -> None:
    """Reject records that look like they carry credentials; never echo the content."""

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _SECRET_KEY.search(str(key)):
                    raise AuditError("audit_redaction_violation")
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str) and _SECRET_VALUE.search(value):
            raise AuditError("audit_redaction_violation")

    walk(canonical_serialize(record))


def _evidence(item: Any) -> EvidenceRef:
    if not isinstance(item, Mapping):
        raise AuditError("invalid_evidence")
    return EvidenceRef(
        str(item.get("component")),
        str(item.get("code")),
        _opt_str(item.get("polarity")),
        ",".join(str(r) for r in item.get("source_refs", ())),
    )


def _regime(output: Mapping[str, Any], evidence: list[EvidenceRef], gaps: list[str]) -> str | None:
    regime = output.get("regime")
    state = regime.get("state") if isinstance(regime, Mapping) else None
    if not isinstance(state, Mapping):
        gaps.append("regime:absent")
        return None
    evidence.append(
        EvidenceRef(
            "regime", str(state.get("label")), None, str(state.get("source_ref") or "regime:none")
        )
    )
    return _opt_str(state.get("config_version"))


def _structure(output: Mapping[str, Any], evidence: list[EvidenceRef], gaps: list[str]) -> None:
    structure = output.get("structure")
    if not isinstance(structure, Mapping):
        gaps.append("structure:absent")
        return
    evidence.append(
        EvidenceRef(
            "structure",
            "snapshot",
            None,
            f"sequence:{structure.get('sequence')};pivots:{len(structure.get('pivots', ()))};"
            f"levels:{len(structure.get('levels', ()))}",
        )
    )


def _trendline(output: Mapping[str, Any], evidence: list[EvidenceRef], gaps: list[str]) -> None:
    trendline = output.get("trendline")
    if not isinstance(trendline, Mapping):
        gaps.append("trendline:absent")
        return
    for side in ("active_bullish", "active_bearish"):
        line = trendline.get(side)
        if isinstance(line, Mapping):
            evidence.append(
                EvidenceRef("trendline", str(line.get("state")), None, str(line.get("line_id")))
            )


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuditError(code)
    return value


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _same_instant(value: Any, moment: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).astimezone(UTC) == moment
    except ValueError:
        return False
