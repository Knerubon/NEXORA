"""Verification that a quality verdict is bound to the evaluated event, and record invariants.

Threat model (ADR-036 D2b). The audit never treats a caller-built ``QualityVerdict`` as proof:
the gate obtains the verdict from a ``DataQualityGuard`` it owns, then this module re-verifies
that the verdict describes *exactly* the event and snapshot being decided. That defeats
forged, mismatched, reused and hash-less verdicts and Guard-shaped doubles. It does not, and
cannot, defend against arbitrary code already running in the same process (which could also
patch the gate); that is an accepted limit of an in-process pure-domain library.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexora.artifacts import canonical_hash
from nexora.data_quality import DataQualityGuard, MarketDataSnapshot, QualityVerdict
from nexora.decision_audit.models import AuditError, DecisionAuditRecord
from nexora.market_data.models import NormalizedPriceEvent

_HEX = frozenset("0123456789abcdef")


def snapshot_hash(snapshot: MarketDataSnapshot) -> str | None:
    """Independently recomputed canonical hash; ``None`` if the events cannot be hashed."""
    try:
        return canonical_hash(snapshot.events)
    except Exception:
        return None


def _check_binding(
    *,
    quality: object,
    snapshot: object,
    event: object,
    guard: object,
    evaluated_at: object,
) -> QualityVerdict:
    """Static checks: the verdict is well-formed and bound to this event/snapshot/evaluation."""
    if not isinstance(guard, DataQualityGuard):
        raise AuditError("invalid_guard")
    if not isinstance(event, NormalizedPriceEvent) or not isinstance(snapshot, MarketDataSnapshot):
        raise AuditError("invalid_quality_input")
    if not isinstance(quality, QualityVerdict):
        raise AuditError("quality_invalid")
    try:
        quality.__post_init__()  # re-assert state/permission invariants on this exact object
    except Exception:
        raise AuditError("quality_invalid") from None
    if quality.config_version != guard.config_version:
        raise AuditError("quality_config_mismatch")

    events = snapshot.events
    # The decision is about the latest event; the evidence must end in exactly that event.
    if not events or events[-1] != event or events[-1].identity_key != event.identity_key:
        raise AuditError("quality_event_mismatch")
    if quality.event_keys != tuple(e.identity_key for e in events):
        raise AuditError("quality_event_mismatch")

    expected = snapshot_hash(snapshot)
    if quality.state == "ok":
        if expected is None or quality.snapshot_hash != expected:
            raise AuditError("quality_snapshot_mismatch")
    elif quality.snapshot_hash is not None and quality.snapshot_hash != expected:
        raise AuditError("quality_snapshot_mismatch")

    aware = (
        isinstance(evaluated_at, datetime)
        and evaluated_at.tzinfo is not None
        and evaluated_at.utcoffset() is not None
    )
    if aware:
        assert isinstance(evaluated_at, datetime)
        if quality.evaluated_at != evaluated_at.astimezone(UTC):
            raise AuditError("quality_evaluation_time_mismatch")
    elif quality.evaluated_at is not None:
        # The Guard reports a missing/naive clock as unknown with no time; anything else lies.
        raise AuditError("quality_evaluation_time_mismatch")
    return quality


def verify_quality_binding(
    *,
    quality: object,
    snapshot: object,
    event: object,
    guard: object,
    evaluated_at: datetime | None,
) -> QualityVerdict:
    """Return ``quality`` only if it provably is what the Guard produces for this evaluation.

    Checks well-formedness and the binding to the exact event, snapshot hash and evaluation
    time, then re-derives the verdict through the (final) Guard and requires equality. A
    self-consistent but invented verdict (e.g. a blocked verdict relabelled ``ok`` with its
    findings removed) therefore cannot pass. Raises ``AuditError`` with a stable code.
    """
    checked = _check_binding(
        quality=quality, snapshot=snapshot, event=event, guard=guard, evaluated_at=evaluated_at
    )
    assert isinstance(guard, DataQualityGuard) and isinstance(snapshot, MarketDataSnapshot)
    try:
        reproduced = guard.evaluate(snapshot, evaluated_at=evaluated_at)
    except Exception:
        raise AuditError("quality_evaluation_failed") from None
    if reproduced != checked:
        raise AuditError("quality_not_reproducible")
    return checked


def obtain_verified_quality(
    *, guard: object, snapshot: object, event: object, evaluated_at: datetime | None
) -> QualityVerdict:
    """The only way the audit gets a verdict: ask the Guard, then verify what came back."""
    if not isinstance(guard, DataQualityGuard):
        raise AuditError("invalid_guard")
    if not isinstance(snapshot, MarketDataSnapshot):
        raise AuditError("invalid_quality_input")
    try:
        quality = guard.evaluate(snapshot, evaluated_at=evaluated_at)
    except Exception:
        raise AuditError("quality_evaluation_failed") from None
    return _check_binding(
        quality=quality,
        snapshot=snapshot,
        event=event,
        guard=guard,
        evaluated_at=evaluated_at,
    )


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def check_record_invariants(record: DecisionAuditRecord) -> None:
    """Reject a record whose action, signal, quality and eligibility disagree.

    Runs when a record is built and again inside the store, so a hand-constructed record
    (``eligible`` with no signal, a permissive verdict reference with findings, ...) can never
    be persisted as authority-bearing.
    """
    action, signal_id = record.action, record.signal_id
    dq, md = record.data_quality, record.market_data
    if record.action not in ("BUY", "SELL", "WAIT"):
        raise AuditError("invalid_action")
    if type(record.signal_emitted) is not bool or type(record.score) is not int:
        raise AuditError("invalid_record")
    if (signal_id is not None) != record.signal_emitted:
        raise AuditError("signal_inconsistent")
    if type(dq.new_trade_permitted) is not bool or dq.new_trade_permitted != (dq.state == "ok"):
        raise AuditError("quality_inconsistent")
    if dq.state not in ("ok", "blocked", "unknown"):
        raise AuditError("quality_inconsistent")
    if md.snapshot_hash != dq.snapshot_hash:
        raise AuditError("quality_snapshot_mismatch")
    if not record.environment or not md.event_identity_key:
        raise AuditError("invalid_record")

    eligibility = record.trade_eligibility
    if action == "WAIT":
        if signal_id is not None or record.signal_emitted or eligibility != "not_applicable":
            raise AuditError("wait_inconsistent")
        return
    if eligibility == "not_applicable":
        raise AuditError("eligibility_inconsistent")
    if eligibility == "eligible_for_downstream_gates":
        if (
            not record.signal_emitted
            or not signal_id
            or dq.state != "ok"
            or dq.finding_codes
            or not _is_hash(dq.snapshot_hash)
            or dq.evaluated_at is None
            or any(gap.startswith(("signal:", "evidence:")) for gap in record.evidence_gaps)
        ):
            raise AuditError("eligibility_inconsistent")
    elif eligibility == "denied_data_quality":
        if dq.state == "ok":
            raise AuditError("eligibility_inconsistent")
    elif eligibility == "denied_no_emitted_signal":
        if record.signal_emitted:
            raise AuditError("eligibility_inconsistent")
    elif eligibility != "denied_inconsistent_output":
        raise AuditError("eligibility_inconsistent")
