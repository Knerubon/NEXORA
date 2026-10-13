"""Builds a DecisionAuditRecord from existing pipeline output (no new decision logic).

The builder never accepts a caller-supplied ``QualityVerdict``. It takes the market-data
snapshot and a ``DataQualityGuard``, obtains the verdict itself and re-verifies that the
verdict is bound to the exact evaluated event and snapshot hash (see ``invariants``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from nexora.artifacts import canonical_hash
from nexora.data_quality import DataQualityGuard, MarketDataSnapshot, QualityVerdict
from nexora.decision_audit.identity import correlation_id as make_correlation_id
from nexora.decision_audit.identity import validate_component
from nexora.decision_audit.invariants import check_record_invariants, obtain_verified_quality
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
from nexora.decision_audit.payload_policy import scan_input_for_sensitive_keys, validate_payload
from nexora.market_data.models import NormalizedPriceEvent

_SIDE_FOR_ACTION = {"BUY": "long", "SELL": "short"}
_READ_SECTIONS = ("entry_readiness", "regime", "structure", "trendline")


def build_decision_audit_record(
    *,
    output: Mapping[str, Any],
    event: NormalizedPriceEvent,
    snapshot: MarketDataSnapshot,
    guard: DataQualityGuard,
    evaluated_at: datetime | None,
    environment: str,
    instrument_id: str | None = None,
    risk_authority_outcome: str | None = None,
    lifecycle_ref: str | None = None,
) -> DecisionAuditRecord:
    """Raise ``AuditError`` when the decision cannot be reconstructed faithfully.

    ``snapshot`` must end in ``event``; the Guard is evaluated here, never trusted from the
    caller. ``evaluated_at`` is the caller's explicit clock (``None`` yields an unknown,
    denying verdict).
    """
    environment = validate_component(environment, "missing_environment")
    if not isinstance(output, Mapping):
        raise AuditError("missing_signals")
    if not isinstance(event, NormalizedPriceEvent):
        raise AuditError("invalid_quality_input")
    quality = obtain_verified_quality(
        guard=guard, snapshot=snapshot, event=event, evaluated_at=evaluated_at
    )
    for stamp in (event.event_time, event.received_at):
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise AuditError("invalid_event_time")

    _reject_sensitive_input(output)
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
    # No fabricated score: a missing or non-integer score is an unreconstructable decision.
    score = decision.get("score")
    if type(score) is not int:
        raise AuditError("invalid_score")

    gaps: list[str] = []
    evidence: list[EvidenceRef] = []
    blockers: list[AuditBlocker] = []
    positive: list[EvidenceRef] = []

    for item in decision.get("positive_evidence", ()):
        ref = _evidence(item)
        evidence.append(ref)
        positive.append(ref)
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

    decided_at = event.received_at.astimezone(UTC)
    link = _resolve_signal(
        signals=signals,
        decision=decision,
        action=action,
        sequence=sequence,
        event=event,
        decided_at=decided_at,
        positive_codes=tuple(ref.code for ref in positive),
        gaps=gaps,
    )
    reasons: tuple[str, ...] = link.reasons
    reason_codes: tuple[str, ...] = link.reason_codes
    if action == "WAIT" or not link.emitted:
        positives = decision.get("positive_evidence", ())
        reasons = reasons or tuple(str(i.get("reason")) for i in positives)
        reason_codes = reason_codes or tuple(ref.code for ref in positive)
        if action == "WAIT" and not reason_codes:
            gaps.append("reason_codes:absent")

    eligibility = _eligibility(action, quality, link)

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

    record = DecisionAuditRecord(
        schema_version=AUDIT_SCHEMA_VERSION,
        decision_id=decision_id,
        correlation_id=make_correlation_id(event.symbol, event.identity_key),
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
        score=score,
        signal_id=link.signal_id,
        signal_emitted=link.emitted,
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
        data_quality=quality_reference(quality),
        trade_eligibility=eligibility,
        risk_authority_outcome=risk_authority_outcome,
        lifecycle_ref=lifecycle_ref,
    )
    validate_payload(record)
    check_record_invariants(record)
    return record


def quality_reference(quality: QualityVerdict) -> DataQualityRef:
    return DataQualityRef(
        state=quality.state,
        new_trade_permitted=quality.new_trade_permitted,
        finding_codes=tuple(f.code for f in quality.findings),
        config_version=quality.config_version,
        snapshot_hash=quality.snapshot_hash,
        evaluated_at=quality.evaluated_at,
    )


class _SignalLink:
    """What the engine output proves about the signal for one BUY/SELL/WAIT decision."""

    __slots__ = ("emitted", "inconsistent", "reason_codes", "reasons", "signal_id")

    def __init__(
        self,
        *,
        signal_id: str | None = None,
        emitted: bool = False,
        inconsistent: bool = False,
        reasons: tuple[str, ...] = (),
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        self.signal_id = signal_id
        self.emitted = emitted
        self.inconsistent = inconsistent
        self.reasons = reasons
        self.reason_codes = reason_codes


def _resolve_signal(
    *,
    signals: Mapping[str, Any],
    decision: Mapping[str, Any],
    action: str,
    sequence: int,
    event: NormalizedPriceEvent,
    decided_at: datetime,
    positive_codes: tuple[str, ...],
    gaps: list[str],
) -> _SignalLink:
    """A BUY/SELL carries a signal id only if one was genuinely emitted for *this* decision.

    WAIT never reads ``latest`` and never gets an id. For BUY/SELL, ``latest`` persists across
    events and the engine suppresses duplicates without appending, so ``latest`` belongs to this
    decision only if it was decided at this instant *and* every identity field agrees with the
    action, symbol, sequence and evidence. Anything else is not an emitted signal.
    """
    if action == "WAIT":
        return _SignalLink()
    latest = signals.get("latest")
    if not isinstance(latest, Mapping):
        gaps.append("signal:absent")
        return _SignalLink()
    if not _same_instant(latest.get("decision_time"), decided_at):
        gaps.append("signal:not_emitted")  # suppressed duplicate, or an earlier decision's signal
        return _SignalLink()

    problems: list[str] = []
    signal_id = latest.get("signal_id")
    if not isinstance(signal_id, str) or not signal_id or len(signal_id) > 128:
        problems.append("signal_id")
    if latest.get("symbol") != event.symbol:
        problems.append("symbol")
    latest_sequence = latest.get("sequence")
    if type(latest_sequence) is not int or latest_sequence != sequence:
        problems.append("sequence")
    if latest.get("side") != _SIDE_FOR_ACTION[action]:
        problems.append("side")
    if latest.get("status") != "active":
        problems.append("status")
    embedded = latest.get("decision")
    if isinstance(embedded, Mapping) and embedded.get("action") != action:
        problems.append("action")
    codes = tuple(str(c) for c in latest.get("reason_codes", ()))
    if not positive_codes:
        problems.append("evidence_empty")  # an actionable decision must have positive evidence
    elif codes != positive_codes:
        problems.append("evidence")
    if problems:
        gaps.extend(f"signal:inconsistent:{name}" for name in problems)
        return _SignalLink(inconsistent=True)
    assert isinstance(signal_id, str)
    return _SignalLink(
        signal_id=signal_id,
        emitted=True,
        reasons=tuple(str(r) for r in latest.get("reasons", ())),
        reason_codes=codes,
    )


def _eligibility(action: str, quality: QualityVerdict, link: _SignalLink) -> TradeEligibility:
    if action == "WAIT":
        return "not_applicable"
    if not quality.new_trade_permitted:
        return "denied_data_quality"
    if link.inconsistent:
        return "denied_inconsistent_output"
    if not link.emitted:
        return "denied_no_emitted_signal"
    return "eligible_for_downstream_gates"


def _reject_sensitive_input(output: Mapping[str, Any]) -> None:
    """Hard-fail on credential-like keys in every engine-output section the audit reads."""
    scan_input_for_sensitive_keys({key: None for key in output})
    scan_input_for_sensitive_keys(output.get("signals"), skip=frozenset({"history"}))
    for name in _READ_SECTIONS:
        scan_input_for_sensitive_keys(output.get(name))


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
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    return parsed.astimezone(UTC) == moment
