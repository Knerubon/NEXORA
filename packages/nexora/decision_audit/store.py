"""Append-only decision audit persistence on the existing Journal (ADR-036).

There is deliberately no update, delete, disable or "best effort" path. Streams are
environment-qualified so a DEV store can never read or write a PROD audit stream.
"""

from __future__ import annotations

from nexora.artifacts import decode
from nexora.decision_audit.models import AUDIT_SCHEMA_VERSION, AuditError, DecisionAuditRecord
from nexora.storage import Journal


class DecisionAuditStore:
    def __init__(self, journal: Journal, *, environment: str) -> None:
        if not environment:
            raise AuditError("missing_environment")
        self._journal = journal
        self._environment = environment

    @property
    def environment(self) -> str:
        return self._environment

    def stream(self, symbol: str) -> str:
        if not symbol:
            raise AuditError("missing_symbol")
        return f"audit:v{AUDIT_SCHEMA_VERSION}:{self._environment}:{symbol}"

    def append(self, record: DecisionAuditRecord) -> bool:
        """Persist once. Returns False for an identical replay; raises ``AuditError`` otherwise.

        Journal.append is transactional and hash-verified: it raises on any identity
        conflict (same decision_id, different content), so a record can never be overwritten.
        """
        if record.environment != self._environment:
            raise AuditError("audit_environment_mismatch")
        if record.schema_version != AUDIT_SCHEMA_VERSION:
            raise AuditError("unsupported_audit_schema")
        try:
            return self._journal.append(
                self.stream(record.instrument.symbol), record.decision_id, record
            )
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

    def read(self, symbol: str) -> tuple[DecisionAuditRecord, ...]:
        try:
            rows = self._journal.read(self.stream(symbol))
            return tuple(decode(DecisionAuditRecord, row) for row in rows)
        except AuditError:
            raise
        except Exception:
            raise AuditError("audit_read_failed") from None
