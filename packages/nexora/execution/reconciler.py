"""Pure reconciliation classifier (ADR-034 section 7 / section 11 item 4).

Compares the durable local, NEXORA-owned ``PositionRecord`` collection against
an immutable broker-side snapshot and emits ``ReconciliationRecord`` values
using ONLY the frozen findings. Classification only: no broker query, no I/O,
no mutation of local or broker state, no remediation decision. ``status`` is
always derived from the finding by ``ReconciliationRecord`` itself.

Conventions (documented decisions, not new contract):

* Local ``PositionRecord.symbol`` is an ``execution_symbol``; it is converted to the
  canonical ADR-025 ``instrument_id`` EXACTLY ONCE through the REQUIRED explicit
  ``ExecutionInstrumentResolver`` (ADR-035 s5.1/s5.2, INV-19; PR-7). There is NO identity
  default (``symbol == instrument_id`` is no longer assumed). A local position whose
  symbol cannot be resolved (no binding, legacy, raising or mismatched resolver) can
  never pair with a broker position and can never be confirmed flat: it falls to
  ``LOCAL_OPEN_BROKER_MISSING`` (fail closed). Ownership is never inferred from
  symbol/side/quantity; a broker position without a declared attribution stays broker-only.
* A local position is expected on the broker unless its state is ``CLOSED``
  (``EXIT_PENDING``/``EMERGENCY`` included: fail closed rather than guess).
* Matching is by ``nexora_position_ref`` on the broker snapshot position. A
  broker position without it, or whose ref names no local position, is
  broker-only: it is reported (``BROKER_POSITION_LOCAL_MISSING``, keyed by its
  opaque broker ref) and is never claimed as NEXORA-owned.
* Same ref but different instrument or side is an identity conflict, reported
  as ``LOCAL_OPEN_BROKER_MISSING`` plus ``BROKER_POSITION_LOCAL_MISSING``.
* A missing broker snapshot yields no comparison records; with nothing
  classified, ``aggregate_reconciliation_status`` fails closed to UNKNOWN.
* ADR-035 section 4.6 (OPEN-6 shape): ``BrokerSnapshot.complete`` defaults to
  ``False`` and ``BrokerPositionSnapshot.close_pending`` defaults to ``False``
  (fail closed). ``CLOSE_PENDING_CONFIRMED`` needs a complete snapshot, a
  position attributed by ``nexora_position_ref`` with identical instrument and
  side, exactly one broker claimant for that ref, and ``close_pending`` true.
  ``BROKER_FLAT_CONFIRMED`` needs a complete snapshot in which NO broker
  position claims the local ref (any instrument/side) and no unattributed
  broker position exists on the same instrument and side (symbol alone never
  proves absence; it only blocks the confirmation). Otherwise the previous
  non-confirming behavior applies. Freshness is not evaluated here (OPEN-1).
* Duplicate local ids or duplicate broker refs are corrupt input and raise
  ``ReconcilerInputError`` rather than being silently de-duplicated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexora.autonomous_contracts import TradeState
from nexora.execution.instrument_resolution import (
    ExecutionBindingMissingError,
    resolve_binding_instrument_id,
)
from nexora.execution.models import (
    ExecutionInstrumentResolver,
    ExecutionResult,
    ExecutionStatus,
    Side,
)
from nexora.execution.reconciliation import (
    ReconciliationFinding,
    ReconciliationRecord,
    ReconciliationStatus,
)
from nexora.position.models import PositionRecord


class ReconcilerInputError(ValueError):
    """Sanitized reconciler input error (code only)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, kw_only=True)
class BrokerPositionSnapshot:
    """One broker-side position, broker-agnostic and opaque.

    ``broker_position_ref`` is an opaque adapter-supplied reference.
    ``nexora_position_ref`` is the local ``position_id`` the adapter can
    attribute this position to, or ``None`` when it cannot (then the position
    is broker-only). No broker symbol or broker-specific field lives here.
    ``stop_price``/``target_prices`` are ``None``/empty when unprotected.
    """

    broker_position_ref: str
    instrument_id: str
    side: Side
    quantity: Decimal
    nexora_position_ref: str | None = None
    stop_price: Decimal | None = None
    target_prices: tuple[Decimal, ...] = ()
    # Adapter attestation that a close order is working (ADR-035 4.6, OPEN-6).
    close_pending: bool = False

    def __post_init__(self) -> None:
        if not self.broker_position_ref.strip() or not self.instrument_id.strip():
            raise ReconcilerInputError("missing_broker_position_identity")
        if self.nexora_position_ref is not None and not self.nexora_position_ref.strip():
            raise ReconcilerInputError("blank_nexora_position_ref")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ReconcilerInputError("invalid_broker_quantity")
        if self.stop_price is not None and (
            not self.stop_price.is_finite() or self.stop_price <= 0
        ):
            raise ReconcilerInputError("invalid_broker_stop_price")
        for target in self.target_prices:
            if not target.is_finite() or target <= 0:
                raise ReconcilerInputError("invalid_broker_target_price")


@dataclass(frozen=True, slots=True, kw_only=True)
class BrokerSnapshot:
    """Immutable set of broker-side positions at one observation.

    ``complete`` is the adapter's attestation that ``positions`` is the whole
    broker position set. It defaults to ``False``: an unattested snapshot can
    never confirm flat or close-pending (ADR-035 4.6).
    """

    positions: tuple[BrokerPositionSnapshot, ...]
    complete: bool = False


def _record(
    finding: ReconciliationFinding,
    observed_at: datetime,
    *,
    position_ref: str | None,
    local_quantity: Decimal | None = None,
    broker_quantity: Decimal | None = None,
    details_ref: str | None = None,
) -> ReconciliationRecord:
    return ReconciliationRecord(
        position_ref=position_ref,
        finding=finding,
        local_quantity=local_quantity,
        broker_quantity=broker_quantity,
        details_ref=details_ref,
        observed_at=observed_at,
    )


def _protection_matches(local: PositionRecord, broker: BrokerPositionSnapshot) -> bool:
    return local.protection.stop_price == broker.stop_price and sorted(
        local.protection.target_prices
    ) == sorted(broker.target_prices)


def _compare_snapshot(
    local_positions: Iterable[PositionRecord],
    snapshot: BrokerSnapshot,
    observed_at: datetime,
    instrument_resolver: object,
) -> list[ReconciliationRecord]:
    local_by_id: dict[str, PositionRecord] = {}
    for local in local_positions:
        if local.position_id in local_by_id:
            raise ReconcilerInputError("duplicate_local_position_id")
        local_by_id[local.position_id] = local
    # execution_symbol -> instrument_id once per local position; None = unresolvable.
    local_instrument: dict[str, str | None] = {}
    for local in local_by_id.values():
        try:
            local_instrument[local.position_id] = resolve_binding_instrument_id(
                instrument_resolver, local.symbol
            )
        except ExecutionBindingMissingError:
            local_instrument[local.position_id] = None
    broker_refs: set[str] = set()
    for broker in snapshot.positions:
        if broker.broker_position_ref in broker_refs:
            raise ReconcilerInputError("duplicate_broker_position_ref")
        broker_refs.add(broker.broker_position_ref)

    claims: dict[str, int] = {}
    for broker in snapshot.positions:
        if broker.nexora_position_ref is not None:
            claims[broker.nexora_position_ref] = claims.get(broker.nexora_position_ref, 0) + 1

    records: list[ReconciliationRecord] = []
    paired_local: set[str] = set()

    # Pair in deterministic broker-ref order so duplicate claims on one local
    # position resolve identically regardless of input order.
    for broker in sorted(snapshot.positions, key=lambda b: b.broker_position_ref):
        candidate = (
            local_by_id.get(broker.nexora_position_ref)
            if broker.nexora_position_ref is not None
            else None
        )
        if (
            candidate is None
            or candidate.state is TradeState.CLOSED
            or candidate.position_id in paired_local
            or local_instrument[candidate.position_id] != broker.instrument_id
            or candidate.side != broker.side
        ):
            records.append(
                _record(
                    ReconciliationFinding.BROKER_POSITION_LOCAL_MISSING,
                    observed_at,
                    position_ref=broker.broker_position_ref,
                    broker_quantity=broker.quantity,
                    details_ref="broker_only_not_nexora_owned",
                )
            )
            continue
        local = candidate
        paired_local.add(local.position_id)
        if snapshot.complete and broker.close_pending and claims[local.position_id] == 1:
            records.append(
                _record(
                    ReconciliationFinding.CLOSE_PENDING_CONFIRMED,
                    observed_at,
                    position_ref=local.position_id,
                    local_quantity=local.quantity,
                    broker_quantity=broker.quantity,
                )
            )
            continue
        mismatch = False
        if local.quantity != broker.quantity:
            mismatch = True
            records.append(
                _record(
                    ReconciliationFinding.QUANTITY_MISMATCH,
                    observed_at,
                    position_ref=local.position_id,
                    local_quantity=local.quantity,
                    broker_quantity=broker.quantity,
                )
            )
        if not _protection_matches(local, broker):
            mismatch = True
            records.append(
                _record(
                    ReconciliationFinding.PROTECTION_MISMATCH,
                    observed_at,
                    position_ref=local.position_id,
                    local_quantity=local.quantity,
                    broker_quantity=broker.quantity,
                )
            )
        if not mismatch:
            records.append(
                _record(
                    ReconciliationFinding.MATCH,
                    observed_at,
                    position_ref=local.position_id,
                    local_quantity=local.quantity,
                    broker_quantity=broker.quantity,
                )
            )

    for local in local_by_id.values():
        if local.state is not TradeState.CLOSED and local.position_id not in paired_local:
            if (
                snapshot.complete
                and local.position_id not in claims
                and local_instrument[local.position_id] is not None
                and not any(
                    b.nexora_position_ref is None
                    and b.instrument_id == local_instrument[local.position_id]
                    and b.side == local.side
                    for b in snapshot.positions
                )
            ):
                records.append(
                    _record(
                        ReconciliationFinding.BROKER_FLAT_CONFIRMED,
                        observed_at,
                        position_ref=local.position_id,
                        local_quantity=local.quantity,
                    )
                )
                continue
            records.append(
                _record(
                    ReconciliationFinding.LOCAL_OPEN_BROKER_MISSING,
                    observed_at,
                    position_ref=local.position_id,
                    local_quantity=local.quantity,
                )
            )

    if not records:
        # Both sides genuinely flat: an explicit, auditable MATCH.
        records.append(
            _record(
                ReconciliationFinding.MATCH,
                observed_at,
                position_ref=None,
                local_quantity=Decimal(0),
                broker_quantity=Decimal(0),
            )
        )
    return records


def classify_reconciliation(
    *,
    local_positions: Iterable[PositionRecord],
    broker_snapshot: BrokerSnapshot | None,
    observed_at: datetime,
    instrument_resolver: ExecutionInstrumentResolver,
    execution_results: Iterable[ExecutionResult] = (),
    restart_recovery_pending: bool = False,
) -> tuple[ReconciliationRecord, ...]:
    """Classify local vs broker state. Pure, deterministic, order-independent.

    Output is sorted by ``(position_ref or "", finding, details_ref or "")``.
    ``broker_snapshot is None`` (broker state unavailable) produces no
    position-comparison records. ``instrument_resolver`` is REQUIRED (no default): the
    same resolver instance the pipeline uses (ADR-035 s5.2, INV-19).
    """

    records: list[ReconciliationRecord] = []
    if broker_snapshot is not None:
        records.extend(
            _compare_snapshot(local_positions, broker_snapshot, observed_at, instrument_resolver)
        )
    for result in execution_results:
        if result.status is ExecutionStatus.UNKNOWN:
            records.append(
                _record(
                    ReconciliationFinding.EXECUTION_RESULT_UNKNOWN,
                    observed_at,
                    position_ref=None,
                    details_ref=f"execution_result:{result.result_id}",
                )
            )
    if restart_recovery_pending:
        records.append(
            _record(
                ReconciliationFinding.RESTART_RECOVERY_PENDING,
                observed_at,
                position_ref=None,
                details_ref="restart_recovery_pending",
            )
        )
    records.sort(key=lambda r: (r.position_ref or "", r.finding.value, r.details_ref or ""))
    return tuple(records)


def aggregate_reconciliation_status(
    records: Iterable[ReconciliationRecord],
) -> ReconciliationStatus:
    """Worst overall status, fail closed.

    Precedence UNKNOWN > UNSYNCHRONIZED > SYNCHRONIZED. No records, or any
    non-``ReconciliationRecord`` item, yields UNKNOWN — never SYNCHRONIZED.
    Feed the result to ``reconciliation_blocks_new_trade``.
    """

    seen: set[ReconciliationStatus] = set()
    for record in records:
        if not isinstance(record, ReconciliationRecord):
            return ReconciliationStatus.UNKNOWN
        seen.add(record.status)
    if not seen or ReconciliationStatus.UNKNOWN in seen:
        return ReconciliationStatus.UNKNOWN
    if ReconciliationStatus.UNSYNCHRONIZED in seen:
        return ReconciliationStatus.UNSYNCHRONIZED
    return ReconciliationStatus.SYNCHRONIZED


_CONFIRMING_FINDINGS = frozenset(
    {
        ReconciliationFinding.MATCH,
        ReconciliationFinding.CLOSE_PENDING_CONFIRMED,
        ReconciliationFinding.BROKER_FLAT_CONFIRMED,
    }
)


def derive_recovery_target(
    records: Iterable[ReconciliationRecord],
    position_ref: str,
    *,
    snapshot_complete: bool,
) -> TradeState | None:
    """Evidence -> frozen recovery TARGET (ADR-035 section 6 table). Pure.

    Returns ``MANAGING`` / ``EXIT_PENDING`` / ``CLOSED`` for verified open /
    close-pending / broker-flat evidence, else ``None`` (remain EMERGENCY).
    This is NOT authorization and performs no transition: OPEN-20 is
    unresolved and ``operator_ref`` has no role here. The caller must pass
    records of ONE classification run and attest ``snapshot_complete`` from
    that run's snapshot; ``False`` always yields ``None`` (MATCH included,
    pending Rin decision section 12 #8). Freshness (OPEN-1) is not evaluated.
    Fails closed on: mixed ``observed_at``, any non-confirming or conflicting
    record for the position, more than one distinct confirming finding, any
    unattributed UNKNOWN record, and (OPEN-4 stricter reading) any other
    record in the run that is not SYNCHRONIZED.
    """

    if snapshot_complete is not True:
        return None
    run: list[ReconciliationRecord] = []
    for record in records:
        if not isinstance(record, ReconciliationRecord):
            return None
        run.append(record)
    if not run or len({r.observed_at for r in run}) != 1:
        return None
    mine = [r for r in run if r.position_ref == position_ref]
    confirming = {r.finding for r in mine if r.finding in _CONFIRMING_FINDINGS}
    if len(confirming) != 1 or any(r.finding not in _CONFIRMING_FINDINGS for r in mine):
        return None
    finding = next(iter(confirming))
    confirm = next(r for r in mine if r.finding is finding)
    if any(
        r.status is not ReconciliationStatus.SYNCHRONIZED
        for r in run
        if r.position_ref != position_ref
    ):
        return None
    if finding is ReconciliationFinding.MATCH:
        quantity = confirm.local_quantity
        if quantity is None or confirm.broker_quantity != quantity or quantity <= 0:
            return None
        return TradeState.MANAGING
    if finding is ReconciliationFinding.CLOSE_PENDING_CONFIRMED:
        return TradeState.EXIT_PENDING
    return TradeState.CLOSED
