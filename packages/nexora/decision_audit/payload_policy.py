"""Structured allowlist for everything an audit record may persist (ADR-036).

Regex redaction alone is a denylist: it only catches the secret shapes someone thought of.
This module is an *allowlist*:

* every persisted dataclass field must be declared in ``_SCHEMA`` with an explicit kind, and a
  field that is not declared is rejected, so a new field cannot reach the journal unclassified;
* every value must satisfy the strict grammar of its kind (identifier token, opaque reference,
  bounded printable text, hash, bool, int, UTC datetime);
* only fields the builder deliberately selects are ever copied from engine output, so unknown
  keys in engine output are never persisted, and unexpected *sensitive-looking* keys in the
  sections the builder reads are rejected outright (``scan_input_for_sensitive_keys``);
* the secret-value patterns remain as a last, defense-in-depth layer for free text only.

Nothing here echoes rejected content: errors carry a fixed code, never the offending value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any

from nexora.decision_audit.models import AuditError

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+\-]{0,127}")
_REFERENCE = re.compile(r"[A-Za-z0-9_.:;,|/+#\-]{0,512}")
_HASH = re.compile(r"[0-9a-f]{64}")
_OPAQUE_RUN = re.compile(r"[A-Za-z0-9+/_-]{40,}")
_MAX_TEXT = 512
_MAX_ITEMS = 2000

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|credential|dsn|private[_-]?key"
    r"|cookie|session[_-]?id|bearer|signature)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(://[^/\s:@]+:[^/\s@]+@"  # URL with embedded credentials
    r"|bearer\s+[a-z0-9._~+/=-]{8,}"  # bearer token
    r"|\b(password|passwd|secret|token|api[_-]?key|authorization)\b\s*[:=]\s*\S+"  # key=value
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"  # PEM block
    r"|\b[A-Za-z0-9+/_-]{40,}={0,2})",  # long opaque blob (key/token shaped)
    re.IGNORECASE,
)

# kind -> field names, per persisted dataclass. ``record.*`` classes are the audit schema.
_SCHEMA: dict[str, dict[str, str]] = {
    "InstrumentIdentity": {
        "symbol": "token",
        "source": "token",
        "units": "token",
        "instrument_id": "ref?",
    },
    "MarketDataReference": {
        "event_identity_key": "reference+",
        "event_time": "datetime",
        "received_at": "datetime",
        "source_sequence": "int",
        "price_source": "token",
        "snapshot_hash": "hash?",
    },
    "EvidenceRef": {
        "component": "token",
        "code": "token",
        "polarity": "token?",
        "reference": "reference",
    },
    "AuditBlocker": {"source": "token", "code": "token", "reason": "text"},
    "AuditVersions": {
        "pipeline_config": "token",
        "signal_config": "token",
        "signal_engine": "token",
        "regime_config": "token?",
        "entry_readiness_config": "token?",
        "audit_schema": "int",
    },
    "DataQualityRef": {
        "state": "token",
        "new_trade_permitted": "bool",
        "finding_codes": "token[]",
        "config_version": "token",
        "snapshot_hash": "hash?",
        "evaluated_at": "datetime?",
    },
    "DecisionAuditRecord": {
        "schema_version": "int",
        "decision_id": "hash",
        "correlation_id": "token",
        "decided_at": "datetime",
        "environment": "token",
        "instrument": "InstrumentIdentity",
        "market_data": "MarketDataReference",
        "action": "token",
        "score": "int",
        "signal_id": "token?",
        "signal_emitted": "bool",
        "signal_sequence": "int",
        "reasons": "text[]",
        "reason_codes": "token[]",
        "blockers": "AuditBlocker[]",
        "confirmation_requirements": "reference[]",
        "future_conditions": "text[]",
        "entry_readiness_state": "token?",
        "evidence": "EvidenceRef[]",
        "evidence_gaps": "reference[]",
        "versions": "AuditVersions",
        "data_quality": "DataQualityRef",
        "trade_eligibility": "token",
        "risk_authority_outcome": "ref?",
        "lifecycle_ref": "ref?",
    },
    "EvaluationBlockedRecord": {
        "schema_version": "int",
        "record_kind": "token",
        "blocked_id": "token",
        "correlation_id": "token",
        "recorded_at": "datetime",
        "environment": "token",
        "instrument": "InstrumentIdentity",
        "event": "MarketDataReference",
        "quality": "DataQualityRef",
        "reason_code": "token",
    },
}


def _fail(code: str = "audit_redaction_violation") -> AuditError:
    return AuditError(code)


def _check_text(value: Any) -> None:
    if not isinstance(value, str) or len(value) > _MAX_TEXT:
        raise _fail()
    if not all(ch.isprintable() for ch in value) or _SECRET_VALUE.search(value):
        raise _fail()


def _check_scalar(kind: str, value: Any) -> None:
    optional = kind.endswith("?")
    base = kind.rstrip("?+")
    if value is None:
        if optional:
            return
        raise _fail()
    if base == "token":
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise _fail()
    elif base == "ref":
        # Caller-supplied opaque reference (instrument/risk/lifecycle): short token grammar,
        # never credential-shaped, and never a long opaque blob that could be a key or token.
        if (
            not isinstance(value, str)
            or len(value) > 64
            or not _TOKEN.fullmatch(value)
            or _SENSITIVE_KEY.search(value)
            or _OPAQUE_RUN.search(value)
        ):
            raise _fail()
    elif base == "reference":
        # "+" marks a required, non-empty reference; plain "reference" may be empty.
        if (
            not isinstance(value, str)
            or not _REFERENCE.fullmatch(value)
            or (kind.endswith("+") and not value)
        ):
            raise _fail()
    elif base == "hash":
        if not isinstance(value, str) or not _HASH.fullmatch(value):
            raise _fail()
    elif base == "text":
        _check_text(value)
    elif base == "int":
        if type(value) is not int:
            raise _fail()
    elif base == "bool":
        if type(value) is not bool:
            raise _fail()
    elif base == "datetime":
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise _fail()
    else:  # pragma: no cover - schema typo guard
        raise _fail("audit_policy_misconfigured")


def _check(kind: str, value: Any) -> None:
    if kind in _SCHEMA:
        _check_object(value, kind)
        return
    if kind.endswith("[]"):
        element = kind[:-2]
        if not isinstance(value, tuple) or len(value) > _MAX_ITEMS:
            raise _fail()
        for item in value:
            _check(element, item)
        return
    _check_scalar(kind, value)


def _check_object(value: Any, expected: str) -> None:
    if not is_dataclass(value) or isinstance(value, type) or type(value).__name__ != expected:
        raise _fail()
    spec = _SCHEMA[expected]
    present = {f.name for f in fields(value)}
    # Exact field-set equality: an undeclared field is rejected, a missing one is too.
    if present != set(spec):
        raise _fail("audit_unexpected_field")
    for name, kind in spec.items():
        _check(kind, getattr(value, name))


def validate_payload(record: object) -> None:
    """Raise ``AuditError`` unless ``record`` conforms to the declared allowlist exactly."""
    name = type(record).__name__
    if name not in _SCHEMA:
        raise _fail("audit_unexpected_field")
    _check_object(record, name)


def scan_input_for_sensitive_keys(section: object, *, skip: frozenset[str] = frozenset()) -> None:
    """Reject sensitive-looking mapping keys anywhere inside an engine-output section.

    The builder copies only fields it selects, so unknown keys are never persisted; this makes
    an unexpected credential-like key in a section the audit reads a hard failure instead of a
    silently ignored one. ``skip`` names root-level keys holding unbounded history that the
    audit does not read; it is honoured at the root only, never deeper.
    """
    stack: list[Any] = []
    if isinstance(section, Mapping):
        for key, item in section.items():
            if key in skip:
                continue
            if _SENSITIVE_KEY.search(str(key)):
                raise _fail("audit_unexpected_sensitive_field")
            stack.append(item)
    else:
        stack.append(section)
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            for key, item in node.items():
                if _SENSITIVE_KEY.search(str(key)):
                    raise _fail("audit_unexpected_sensitive_field")
                stack.append(item)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
