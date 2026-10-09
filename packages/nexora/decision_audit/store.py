"""Append-only decision audit persistence on the existing Journal (ADR-036).

There is deliberately no update, delete, disable or "best effort" path. Streams are
environment-qualified with an injective encoding (``identity``), so a DEV store can never read
or write a PROD audit stream, even with hostile environment or symbol strings.
"""

from __future__ import annotations

from typing import Any

from nexora.artifacts import decode
from nexora.decision_audit.identity import decision_stream, validate_component
from nexora.decision_audit.invariants import check_record_invariants
from nexora.decision_audit.models import (
    AUDIT_SCHEMA_VERSION,
    AppendOutcome,
    AuditError,
    DecisionAuditRecord,
)
from nexora.decision_audit.payload_policy import validate_payload
from nexora.storage import Journal


def append_once(journal: Journal, stream: str, key: str, payload: Any) -> AppendOutcome:
    """Persist once. Identical replay -> ``REPLAYED``; any conflict or failure -> ``AuditError``.

    ``Journal.append`` is transactional and hash-verified: it raises on any identity conflict
    (same key, different content), so a record can never be overwritten.
    """
    try:
        created = journal.append(stream, key, payload)
    except AuditError:
        raise
    except ValueError as exc:
        code = (
            "audit_identity_conflict"
            if str(exc) == "journal_identity_conflict"
            else "audit_persistence_failed"
        )
        raise AuditError(code) from None
    except Exception:
        raise AuditError("audit_persistence_failed") from None
    return AppendOutcome.CREATED if created else AppendOutcome.REPLAYED


class DecisionAuditStore:
    def __init__(self, journal: Journal, *, environment: str) -> None:
        self._journal = journal
        self._environment = validate_component(environment, "missing_environment")

    @property
    def environment(self) -> str:
        return self._environment

    def stream(self, symbol: str) -> str:
        return decision_stream(self._environment, symbol)

    def append(self, record: DecisionAuditRecord) -> AppendOutcome:
        """Persist once; ``AppendOutcome.REPLAYED`` for an identical replay.

        Every record is re-validated here, whoever built it: exact allowlisted shape, no
        credential-like content, and the action/signal/quality/eligibility invariants. A
        hand-constructed record that claims eligibility it has not earned is rejected.
        """
        if not isinstance(record, DecisionAuditRecord):
            raise AuditError("invalid_record")
        if record.environment != self._environment:
            raise AuditError("audit_environment_mismatch")
        if record.schema_version != AUDIT_SCHEMA_VERSION:
            raise AuditError("unsupported_audit_schema")
        validate_payload(record)
        check_record_invariants(record)
        return append_once(
            self._journal, self.stream(record.instrument.symbol), record.decision_id, record
        )

    def read(self, symbol: str) -> tuple[DecisionAuditRecord, ...]:
        try:
            rows = self._journal.read(self.stream(symbol))
            return tuple(decode(DecisionAuditRecord, row) for row in rows)
        except AuditError:
            raise
        except Exception:
            raise AuditError("audit_read_failed") from None
