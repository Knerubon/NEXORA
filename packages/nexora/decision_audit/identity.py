"""Unambiguous, collision-resistant identity encoding for audit streams and correlation.

Streams were previously built by joining raw strings with ``:``. That is ambiguous:
environment ``a:b`` + symbol ``c`` and environment ``a`` + symbol ``b:c`` both produced
``audit:v1:a:b:c``, so one environment could read or poison another's stream. Every
component is now percent-escaped (``:`` and ``%`` included) before it is joined, which makes
the encoding injective: two different ``(kind, environment, symbol)`` triples can never share
a stream name. Components are also validated, never silently normalized.
"""

from __future__ import annotations

from nexora.artifacts import canonical_hash
from nexora.decision_audit.models import AUDIT_SCHEMA_VERSION, AuditError

_MAX_COMPONENT_LENGTH = 64
_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")

DECISION_STREAM_PREFIX = "audit"
# A different prefix, not just a different suffix: blocked records can never share a stream
# (or a journal key space) with decision records.
BLOCKED_STREAM_PREFIX = "audit-blocked"


def validate_component(value: object, code: str) -> str:
    """Return ``value`` unchanged if it is a clean identity component, else raise.

    Clean = non-empty ``str``, at most 64 characters, no edge whitespace, only printable
    characters. Nothing is trimmed or case-folded: ``"XAUUSD "`` is rejected, not repaired.
    """
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= _MAX_COMPONENT_LENGTH
        or value != value.strip()
        or not all(ch.isprintable() for ch in value)
    ):
        raise AuditError(code)
    return value


def _escape(component: str) -> str:
    out: list[str] = []
    for byte in component.encode("utf-8"):
        char = chr(byte)
        out.append(char if char in _SAFE else f"%{byte:02X}")
    return "".join(out)


def stream_name(prefix: str, environment: str, symbol: str) -> str:
    """``<prefix>:v<schema>:<escaped env>:<escaped symbol>``; injective in its components."""
    env = validate_component(environment, "missing_environment")
    sym = validate_component(symbol, "missing_symbol")
    return f"{prefix}:v{AUDIT_SCHEMA_VERSION}:{_escape(env)}:{_escape(sym)}"


def decision_stream(environment: str, symbol: str) -> str:
    return stream_name(DECISION_STREAM_PREFIX, environment, symbol)


def blocked_stream(environment: str, symbol: str) -> str:
    return stream_name(BLOCKED_STREAM_PREFIX, environment, symbol)


def correlation_id(symbol: str, event_identity_key: str) -> str:
    """Stable across environments and replays; structured, so ``a|b`` / ``a`` + ``|b`` differ."""
    digest = canonical_hash({"kind": "correlation", "symbol": symbol, "event": event_identity_key})
    return "corr:" + digest[:32]
