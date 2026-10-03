"""Broker-independent reconciliation contract (ADR-034 section 7).

Classification only — this module never mutates broker or local account
state, never calls a broker, and never decides what to *do* about an
unsynchronized/unknown finding beyond exposing the fail-closed
``blocks_new_trade`` rule. Resolution/remediation remain future work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ReconciliationStatus(StrEnum):
    """ADR-034 section 7. Critical invariant: only SYNCHRONIZED permits a new
    trade from a reconciliation standpoint. UNSYNCHRONIZED or UNKNOWN => NO
    NEW TRADE until resolved (see ``blocks_new_trade``).
    """

    SYNCHRONIZED = "SYNCHRONIZED"
    UNSYNCHRONIZED = "UNSYNCHRONIZED"
    UNKNOWN = "UNKNOWN"


class ReconciliationFinding(StrEnum):
    """The specific condition observed. ``status`` is derived from this, never
    set independently, so a caller cannot report a mismatch finding while
    claiming SYNCHRONIZED.
    """

    MATCH = "MATCH"
    LOCAL_OPEN_BROKER_MISSING = "LOCAL_OPEN_BROKER_MISSING"
    BROKER_POSITION_LOCAL_MISSING = "BROKER_POSITION_LOCAL_MISSING"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PROTECTION_MISMATCH = "PROTECTION_MISMATCH"
    EXECUTION_RESULT_UNKNOWN = "EXECUTION_RESULT_UNKNOWN"
    RESTART_RECOVERY_PENDING = "RESTART_RECOVERY_PENDING"


_FINDING_STATUS: dict[ReconciliationFinding, ReconciliationStatus] = {
    ReconciliationFinding.MATCH: ReconciliationStatus.SYNCHRONIZED,
    ReconciliationFinding.LOCAL_OPEN_BROKER_MISSING: ReconciliationStatus.UNSYNCHRONIZED,
    ReconciliationFinding.BROKER_POSITION_LOCAL_MISSING: ReconciliationStatus.UNSYNCHRONIZED,
    ReconciliationFinding.QUANTITY_MISMATCH: ReconciliationStatus.UNSYNCHRONIZED,
    ReconciliationFinding.PROTECTION_MISMATCH: ReconciliationStatus.UNSYNCHRONIZED,
    ReconciliationFinding.EXECUTION_RESULT_UNKNOWN: ReconciliationStatus.UNKNOWN,
    ReconciliationFinding.RESTART_RECOVERY_PENDING: ReconciliationStatus.UNKNOWN,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciliationRecord:
    """ADR-034 section 7. ``status`` is always derived from ``finding`` — it is
    not an independently settable field, so SYNCHRONIZED can never be claimed
    alongside a mismatch/missing/unknown finding.
    """

    position_ref: str | None
    finding: ReconciliationFinding
    local_quantity: Decimal | None = None
    broker_quantity: Decimal | None = None
    details_ref: str | None = None
    observed_at: datetime
    status: ReconciliationStatus = field(init=False)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("reconciliation_observed_at_requires_timezone")
        object.__setattr__(self, "status", _FINDING_STATUS[self.finding])

        if self.finding is ReconciliationFinding.QUANTITY_MISMATCH and (
            self.local_quantity is None or self.broker_quantity is None
        ):
            raise ValueError("quantity_mismatch_requires_both_quantities")
        if (
            self.finding
            in (
                ReconciliationFinding.LOCAL_OPEN_BROKER_MISSING,
                ReconciliationFinding.BROKER_POSITION_LOCAL_MISSING,
            )
            and self.position_ref is None
        ):
            raise ValueError("missing_finding_requires_position_ref")
        if self.finding is ReconciliationFinding.MATCH and (
            self.local_quantity is not None
            and self.broker_quantity is not None
            and self.local_quantity != self.broker_quantity
        ):
            raise ValueError("match_finding_requires_equal_quantities")


def blocks_new_trade(status: ReconciliationStatus) -> bool:
    """ADR-034 section 7 critical invariant: UNSYNCHRONIZED or UNKNOWN => NO
    NEW TRADE until resolved. Other gates (SystemHealthGate, RiskDecision,
    AuthorityDecision, ...) still apply independently — this is one input
    among several, not a replacement for any of them.
    """

    return status is not ReconciliationStatus.SYNCHRONIZED
