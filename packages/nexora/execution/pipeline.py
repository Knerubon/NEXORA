"""ExecutionPipeline (ADR-035 s3; PR-4): fully fail-closed orchestration, NO real transmission.

Composes the existing, separately tested components in the ADR-035 frozen order::

    1 reconciliation gate (+ OPEN-16 global dedup gate)  -> 2 resolution
    -> 2b authority re-evaluation -> 3 ExecutionGuard -> 4 ExecutionPreflight
    -> 5 idempotency claim -> 6 last look + attempt marker -> 7 adapter.submit (LOCKED)
    -> 8 result persistence -> 9 result-driven lifecycle + post-execution reconciliation

The pipeline contains NO policy and duplicates NO component logic: it only enforces the
order and stop-on-first-denial. It has no pattern logic, no second guard, no second
dedup. Any step that cannot establish safety denies and nothing later runs.

HARD BOUNDARIES (all enforced by tests):

* ``ExecutionPreflight`` is DENY-ONLY in V1. The production composition (``transmission``
  is ``None``) therefore can never reach step 5: the claim, the attempt marker and
  ``adapter.submit`` are unreachable. Even if a preflight decision were ``allowed`` the
  production composition still denies (``transmission_not_wired``).
* No real broker adapter exists, is imported, constructed or wired here: no default
  adapter, no env/flag/config switch, no MT5 import, no broker order call. ``adapter.submit``
  can only be reached through the non-production seam
  (``non_production_transmission_seam``), which refuses any adapter that is not explicitly
  marked ``NEXORA_NON_PRODUCTION_TEST_ADAPTER = True`` and which lives outside the
  ``nexora.`` package namespace (so ``SimulatedBrokerAdapter`` and every production adapter
  class are refused). The seam is referenced ONLY by this module and tests (AST-enforced).
  The marker is adapter self-attestation, a speed bump and an audit signal, not a safeguard
  against a malicious caller; the real barrier is that no adapter exists and preflight is
  deny-only.
* ``recover_from_emergency`` / ``plan_emergency_recovery`` are never called. An EMERGENCY
  position is denied at resolution. ``operator_ref`` is never consulted (audit only).
* The pipeline never retries, never reclaims, never loops, never calls
  ``establish_index_genesis`` / ``register_known_keys``.
* ``DedupRecord.latest_result`` is never consulted: dedup safety comes from ``inspect()``
  / ``enumerate_unresolved()`` only.

Cost note: the OPEN-16 gate calls ``enumerate_unresolved()`` once per run (step 1); that
reads the whole dedup index and re-derives every indexed key via ``inspect()``, so cost is
O(indexed keys) per intent. A genesis-marker enumeration is an operator attestation of
completeness, not proof beyond the documented rule; an empty result means "none
unresolved" ONLY when nothing was raised, and the index never grants permission.

Time: every clock read goes through the injected ``clock``; no ambient clock.

Unresolved ADR items taken on the fail-closed path (see tasks/PR4-execution-pipeline.md):
OPEN-1 (bounds are injected; absent => deny), OPEN-2/OPEN-6 (no BrokerStateQuery port:
evidence is supplied by the caller; no post-execution classification performed),
OPEN-5 (no in-flight position exclusion), OPEN-8 (no residual-volume check beyond
preflight), OPEN-9 (the instrument resolver port is injected; none => deny), OPEN-14/15
(an unfresh key, including a released generation, is denied: no same-key retry),
OPEN-17 (OPEN/REDUCE quantity and MODIFY_PROTECTION payload are explicit inputs; absent =>
deny), OPEN-19 (lifecycle result is computed once and returned, never persisted here),
OPEN-20 (recovery never applied).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from nexora.artifacts import canonical_hash
from nexora.autonomous.authority import (
    AuthorityDecision,
    ProtectionChange,
    TradingConfig,
    authorize_trade_intent,
)
from nexora.autonomous.broker_capabilities import BrokerCapabilities
from nexora.autonomous.health import SystemHealthSnapshot
from nexora.autonomous_contracts import (
    ManualOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradeState,
)
from nexora.entry_readiness.models import EntryReadinessState
from nexora.execution.broker_adapter import BrokerExecutionAdapter
from nexora.execution.dedup_store import (
    ClaimOutcome,
    DedupEnumerationIncompleteError,
    DedupKeyState,
    DedupKeyStatus,
    DedupStoreCorruptError,
    DedupStoreError,
    DedupStoreIOError,
    ExecutionDedupStore,
    InMemoryExecutionDedupStore,
)
from nexora.execution.guard import GuardDecision, evaluate_execution_guard
from nexora.execution.idempotency import derive_new_position_ref, execution_request_idempotency_key
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    PriceConstraint,
    ProtectionRequest,
    ResolvedExecution,
    is_safe_to_retry_without_reconciliation,
)
from nexora.execution.preflight import ExecutionPreflight, PreflightDecision
from nexora.execution.reconciler import aggregate_reconciliation_status
from nexora.execution.reconciliation import ReconciliationFinding, ReconciliationRecord
from nexora.execution.reconciliation import ReconciliationStatus as ReconStatus
from nexora.execution.recovery import RECOVERY_AUTHORIZATION_NOT_RESOLVED
from nexora.position.models import PositionInputError, PositionRecord
from nexora.position.result_lifecycle import apply_execution_result
from nexora.position.supervisor import _require_tightening_only
from nexora.risk.models import RiskDecision

# --------------------------------------------------------------------------- evidence refs

_EVIDENCE_REF_RE = re.compile(r"sha256:[0-9a-f]{64}")


def is_valid_evidence_ref(value: object) -> bool:
    """Canonical execution-boundary allow-list: ``sha256:<64 lowercase hex>`` ONLY.

    No free text, URL, JWT, key, broker payload or PII can match. Uppercase hex, other
    lengths, other prefixes, surrounding whitespace and non-str values are all rejected.
    """

    return type(value) is str and _EVIDENCE_REF_RE.fullmatch(value) is not None


def _digest(value: Any) -> str:
    return f"sha256:{canonical_hash(value)}"


# --------------------------------------------------------------------------- seam

ADAPTER_MARKER_ATTRIBUTE = "NEXORA_NON_PRODUCTION_TEST_ADAPTER"
_SEAM_TOKEN = object()


class PipelineWiringError(ValueError):
    """The pipeline was wired in a way that is not permitted (fail closed at construction)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _PreflightPort(Protocol):
    def evaluate(
        self,
        request: ExecutionRequest,
        capabilities: BrokerCapabilities | None,
        market_refs: object,
        *,
        now: datetime,
        max_capabilities_age: timedelta | None = None,
    ) -> PreflightDecision: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class NonProductionTransmissionSeam:
    """The ONLY way an adapter can be attached. Mint it with
    ``non_production_transmission_seam``; direct construction is refused by the pipeline.
    Tests only: production modules must never reference it (AST-enforced)."""

    adapter: BrokerExecutionAdapter
    preflight: _PreflightPort
    _token: object = field(repr=False, compare=False)


def non_production_transmission_seam(
    adapter: object, *, preflight: _PreflightPort | None = None
) -> NonProductionTransmissionSeam:
    """Permitted TEST-ONLY seam. Refuses an adapter that is not a ``BrokerExecutionAdapter``,
    is not explicitly marked ``NEXORA_NON_PRODUCTION_TEST_ADAPTER = True`` on its class, or
    whose class lives under the ``nexora.`` package (this excludes the shipped
    simulator and every possible production adapter)."""

    if not isinstance(adapter, BrokerExecutionAdapter):
        raise PipelineWiringError("adapter_does_not_implement_broker_execution_adapter")
    adapter_type = type(adapter)
    if getattr(adapter_type, ADAPTER_MARKER_ATTRIBUTE, False) is not True:
        raise PipelineWiringError("adapter_not_marked_non_production_test_adapter")
    module = adapter_type.__module__
    if module == "nexora" or module.startswith("nexora."):
        raise PipelineWiringError("adapter_in_production_namespace_refused")
    return NonProductionTransmissionSeam(
        adapter=adapter,
        preflight=preflight if preflight is not None else ExecutionPreflight(),
        _token=_SEAM_TOKEN,
    )


# --------------------------------------------------------------------------- inputs / outputs


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciliationEvidence:
    """Caller-supplied verified evidence from ONE classification run (single snapshot).

    There is no BrokerStateQuery port yet (OPEN-2/OPEN-6): the pipeline cannot acquire
    evidence itself and never fabricates it. ``evidence_ref`` must be ``sha256:<hex>``."""

    records: tuple[ReconciliationRecord, ...]
    evidence_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineInputs:
    """Everything one run consumes. NO ``AuthorityDecision`` and NO ``AIAnalysis`` is an
    input: authority is recomputed at step 2b (ADR-035 D4)."""

    intent: TradeIntent
    request_id: str
    local_positions: tuple[PositionRecord, ...]
    reconciliation: ReconciliationEvidence | None
    health: SystemHealthSnapshot
    config: TradingConfig
    kill_switch: bool
    entry_readiness: EntryReadinessState | None = None
    risk_decision: RiskDecision | None = None
    requested_quantity: Decimal | None = None  # OPEN/REDUCE only (OPEN-17); absent => deny
    protection: ProtectionRequest | None = None  # MODIFY_PROTECTION (OPEN-17); absent => deny
    price_constraint: PriceConstraint | None = None
    capabilities: BrokerCapabilities | None = None


# Single ``execution_symbol -> canonical instrument_id`` conversion (ADR-035 s5.1); returns
# ``None`` when no explicit binding exists. PROVISIONAL port shape (OPEN-9); no default exists.
InstrumentResolver = Callable[[str], str | None]


class PipelineStatus(StrEnum):
    DENIED = "DENIED"  # a step in 1-4 (or wiring) denied; nothing durable written
    DUPLICATE = "DUPLICATE"  # claim lost; stored state returned; never transmitted
    ABORTED = "ABORTED"  # last look failed after the claim; abort marker attempted
    MARKER_FAILED = "MARKER_FAILED"  # attempt marker not durable; submit NOT called
    SUBMIT_RAISED = "SUBMIT_RAISED"  # adapter raised; key stays ATTEMPTED_NO_RESULT
    RESULT_REJECTED = "RESULT_REJECTED"  # result without durable attempt; not persisted
    RESULT_PERSIST_FAILED = "RESULT_PERSIST_FAILED"  # attempted, result not durable (UNKNOWN)
    COMPLETED = "COMPLETED"  # result persisted (any status)


class LifecycleStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE_OPEN = "NOT_APPLICABLE_OPEN"  # position creation is not specified (ADR 3.11)
    UNCHANGED = "UNCHANGED"
    APPLIED = "APPLIED"
    RECOVERY_DENIED = "RECOVERY_DENIED"  # recovery authorization denial; NOT a stale result
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"  # invalid/stale result; NO retry


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineOutcome:
    status: PipelineStatus
    reason_codes: tuple[str, ...]
    trace: tuple[str, ...]  # steps entered, in order (audit; never contains payloads)
    request: ExecutionRequest | None = None
    result: ExecutionResult | None = None
    result_persisted: bool = False
    unknown_pending: bool = False
    released: bool = False
    lifecycle: LifecycleStatus = LifecycleStatus.NOT_RUN
    next_position: PositionRecord | None = None  # NOT persisted here (OPEN-19)
    post_execution_reconciliation_required: bool = False


_POSITION_KINDS = (
    TradeIntentKind.REDUCE,
    TradeIntentKind.CLOSE,
    TradeIntentKind.MODIFY_PROTECTION,
)
_OPEN_STATES = (TradeState.OPEN, TradeState.MANAGING)


def _is_first_generation(status: DedupKeyStatus) -> bool:
    """ONE shared predicate: a key may only ever be transmitted in generation 0."""

    return status.generation in (None, 0)


class _Stop(Exception):
    """Internal: a step denied. Carries reason codes only."""

    def __init__(self, *codes: str) -> None:
        super().__init__(codes)
        self.codes = codes


@dataclass(slots=True)
class _Resolution:
    resolved: ResolvedExecution
    instrument_id: str
    position: PositionRecord | None
    quantity: Decimal | None
    position_ref: str | None
    new_position_ref: str | None
    protection_change: ProtectionChange | None
    protection: ProtectionRequest | None


class ExecutionPipeline:
    """ADR-035 ExecutionPipeline. Production composition: ``transmission=None``."""

    def __init__(
        self,
        *,
        dedup_store: ExecutionDedupStore,
        clock: Callable[[], datetime],
        instrument_resolver: InstrumentResolver | None = None,
        max_reconciliation_evidence_age: timedelta | None = None,
        max_preflight_age: timedelta | None = None,
        max_capabilities_age: timedelta | None = None,
        transmission: NonProductionTransmissionSeam | None = None,
    ) -> None:
        if transmission is not None and (
            type(transmission) is not NonProductionTransmissionSeam
            or transmission._token is not _SEAM_TOKEN
        ):
            raise PipelineWiringError("transmission_wiring_not_permitted")
        if transmission is None and isinstance(dedup_store, InMemoryExecutionDedupStore):
            # INV-26 / OPEN-18: a non-durable store is refused outside the test seam.
            raise PipelineWiringError("in_memory_dedup_store_refused")
        if not callable(clock):
            raise PipelineWiringError("clock_required")
        self._store = dedup_store
        self._clock = clock
        self._resolver = instrument_resolver
        self._max_evidence_age = max_reconciliation_evidence_age
        self._max_preflight_age = max_preflight_age
        self._max_capabilities_age = max_capabilities_age
        self._transmission = transmission
        self._production_preflight = ExecutionPreflight()

    # ------------------------------------------------------------------ public

    def run(self, inputs: PipelineInputs) -> PipelineOutcome:
        trace: list[str] = []
        try:
            return self._run(inputs, trace)
        except _Stop as stop:
            return PipelineOutcome(
                status=PipelineStatus.DENIED, reason_codes=stop.codes, trace=tuple(trace)
            )
        except Exception:  # fail closed; never leak exception text (may carry payload)
            return PipelineOutcome(
                status=PipelineStatus.DENIED,
                reason_codes=("pipeline_internal_error",),
                trace=tuple(trace),
            )

    # ------------------------------------------------------------------ steps

    def _now(self) -> datetime:
        moment = self._clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise _Stop("clock_requires_timezone")
        return moment

    def _run(self, inputs: PipelineInputs, trace: list[str]) -> PipelineOutcome:
        if not isinstance(inputs, PipelineInputs) or not isinstance(inputs.intent, TradeIntent):
            raise _Stop("invalid_inputs")
        if (
            not isinstance(inputs.request_id, str)
            or not inputs.request_id.strip()
            or not isinstance(inputs.kill_switch, bool)
            or not isinstance(inputs.config, TradingConfig)
            or not isinstance(inputs.health, SystemHealthSnapshot)
            or not isinstance(inputs.local_positions, tuple)
        ):
            raise _Stop("invalid_inputs")
        intent = inputs.intent
        key = execution_request_idempotency_key(intent)
        now = self._now()

        trace.append("1")
        status, evidence = self._step1_reconciliation_gate(inputs, key, now)
        trace.append("2")
        res = self._step2_resolution(inputs, evidence, now)
        trace.append("2b")
        authority = self._step2b_authority(inputs, res)
        trace.append("3")
        guard = self._step3_guard(inputs, res, authority, status, now)
        request = guard.request
        assert request is not None
        trace.append("4")
        decision = self._step4_preflight(inputs, request)

        trace.append("5")
        try:
            claim = self._store.claim(key)
        except Exception:
            raise _Stop("dedup_claim_failed") from None
        if claim is not ClaimOutcome.FIRST_CLAIM:
            return PipelineOutcome(
                status=PipelineStatus.DUPLICATE,
                reason_codes=("dedup_duplicate_claim", *self._state_codes(key)),
                trace=tuple(trace),
                request=request,
            )

        trace.append("6")
        refused = self._post_claim_key_check(key)
        if refused:
            # Claim won a RELEASED generation (A released between our step-1 gate and our
            # claim): never transmit; best-effort abort; fail closed.
            codes = list(refused)
            try:
                self._store.record_abort(key)
            except Exception:
                codes.append("abort_marker_failed")
            return PipelineOutcome(
                status=PipelineStatus.DENIED,
                reason_codes=tuple(codes),
                trace=tuple(trace),
                request=request,
            )
        failed = self._step6_last_look(
            inputs, res, authority, status, now, request, evidence, decision
        )
        if failed:
            codes = list(failed)
            try:
                self._store.record_abort(key)
            except Exception:
                codes.append("abort_marker_failed")
            return PipelineOutcome(
                status=PipelineStatus.ABORTED,
                reason_codes=tuple(codes),
                trace=tuple(trace),
                request=request,
            )
        try:
            self._store.record_attempt(
                key,
                request_digest=_digest(request),
                resolved_quantity=res.quantity,
                reconciliation_evidence_ref=evidence.evidence_ref,
                preflight_decision_ref=_digest(decision),
                written_at=self._now(),
            )
            # W1 defense in depth: re-read the authoritative state; "returned normally"
            # is not trusted on its own.
            marker = self._store.inspect(key)
            if marker.state is not DedupKeyState.ATTEMPTED_NO_RESULT or not _is_first_generation(
                marker
            ):
                raise _Stop("attempt_marker_not_verified")
        except Exception:
            # W1: no durable marker, no submit. State stays CLAIMED_NOT_ATTEMPTED.
            return PipelineOutcome(
                status=PipelineStatus.MARKER_FAILED,
                reason_codes=("attempt_marker_not_durable",),
                trace=tuple(trace),
                request=request,
            )

        trace.append("7")
        assert self._transmission is not None  # guaranteed by step 4
        try:
            raw = self._transmission.adapter.submit(request)
        except Exception:
            # Row 8a: stays ATTEMPTED_NO_RESULT (UNKNOWN-equivalent); never downgraded.
            return PipelineOutcome(
                status=PipelineStatus.SUBMIT_RAISED,
                reason_codes=("adapter_submit_raised",),
                trace=tuple(trace),
                request=request,
                unknown_pending=True,
                post_execution_reconciliation_required=True,
            )

        try:
            return self._post_submit(key, request, res, raw, trace)
        except Exception:
            # ANY failure after submit returned: the key may be ATTEMPTED_NO_RESULT or a
            # result may or may not be durable. Never a plain denial: UNKNOWN-pending.
            return PipelineOutcome(
                status=PipelineStatus.RESULT_PERSIST_FAILED,
                reason_codes=("post_submit_processing_failed",),
                trace=tuple(trace),
                request=request,
                unknown_pending=True,
                post_execution_reconciliation_required=True,
            )

    def _post_submit(
        self,
        key: str,
        request: ExecutionRequest,
        res: _Resolution,
        raw: object,
        trace: list[str],
    ) -> PipelineOutcome:
        trace.append("8")
        result = self._normalize_result(request, raw)
        persisted, codes_8, released = self._step8_persist(key, result)
        if not persisted:
            status8 = (
                PipelineStatus.RESULT_REJECTED
                if "result_without_durable_attempt" in codes_8
                or "result_attempt_verification_failed" in codes_8
                else PipelineStatus.RESULT_PERSIST_FAILED
            )
            return PipelineOutcome(
                status=status8,
                reason_codes=codes_8,
                trace=tuple(trace),
                request=request,
                result=result,
                unknown_pending=True,
                post_execution_reconciliation_required=True,
            )

        trace.append("9")
        lifecycle, next_position, codes_9 = self._step9_lifecycle(res, request, result)
        return PipelineOutcome(
            status=PipelineStatus.COMPLETED,
            reason_codes=(*codes_8, *codes_9),
            trace=tuple(trace),
            request=request,
            result=result,
            result_persisted=True,
            released=released,
            lifecycle=lifecycle,
            next_position=next_position,
            # No BrokerStateQuery port (OPEN-2/OPEN-6): fresh evidence cannot be acquired
            # here, so the NEXT intent's step 1 requires fresh caller-supplied evidence.
            post_execution_reconciliation_required=True,
        )

    # -- step 1 ------------------------------------------------------------

    def _step1_reconciliation_gate(
        self, inputs: PipelineInputs, key: str, now: datetime
    ) -> tuple[ReconStatus, ReconciliationEvidence]:
        self._open16_global_gate()
        self._own_key_gate(key)

        # Read every field ONCE, from an exact-type object (no subclass/property can change
        # its answer between check and use); only the immutable snapshot is used afterwards.
        raw_evidence = inputs.reconciliation
        if type(raw_evidence) is not ReconciliationEvidence:
            raise _Stop("reconciliation_evidence_missing")
        ref = raw_evidence.evidence_ref
        observed = raw_evidence.observed_at
        records = raw_evidence.records
        if not is_valid_evidence_ref(ref):
            raise _Stop("reconciliation_evidence_ref_invalid")
        if (
            type(observed) is not datetime
            or observed.tzinfo is None
            or observed.utcoffset() is None
        ):
            raise _Stop("reconciliation_evidence_observed_at_invalid")
        if (
            type(records) is not tuple
            or not records
            or not all(
                type(r) is ReconciliationRecord and type(r.observed_at) is datetime for r in records
            )
        ):
            raise _Stop("reconciliation_evidence_records_invalid")
        # Same-snapshot rule: every record is from ONE classification run.
        if any(r.observed_at != observed for r in records):
            raise _Stop("reconciliation_evidence_not_single_snapshot")
        self._require_fresh(observed, now, self._max_evidence_age, "reconciliation_evidence")
        status = aggregate_reconciliation_status(records)
        if status is not ReconStatus.SYNCHRONIZED:
            raise _Stop(f"reconciliation_not_synchronized:{status.value}")
        snapshot = ReconciliationEvidence(
            records=tuple(records), evidence_ref=ref, observed_at=observed
        )
        return status, snapshot

    def _open16_global_gate(self) -> None:
        try:
            unresolved = self._store.enumerate_unresolved()
        except DedupEnumerationIncompleteError:
            raise _Stop("dedup_enumeration_incomplete") from None
        except DedupStoreCorruptError:
            raise _Stop("dedup_store_corrupt") from None
        except DedupStoreIOError:
            raise _Stop("dedup_store_io_error") from None
        except DedupStoreError:
            raise _Stop("dedup_store_error") from None
        except Exception:
            raise _Stop("dedup_enumeration_failed") from None
        if not isinstance(unresolved, tuple) or not all(
            isinstance(s, DedupKeyStatus) for s in unresolved
        ):
            raise _Stop("dedup_enumeration_malformed")
        if unresolved:
            # Block on EVERY returned status; the index never grants permission.
            states = sorted({s.state.value for s in unresolved})
            raise _Stop(*(f"dedup_unresolved:{state}" for state in states))

    def _own_key_gate(self, key: str) -> None:
        try:
            status = self._store.inspect(key)
        except DedupStoreError:
            raise _Stop("dedup_store_unreadable") from None
        except Exception:
            raise _Stop("dedup_inspect_failed") from None
        if status.state is DedupKeyState.QUARANTINED:
            raise _Stop("dedup_quarantined")
        if status.state is not DedupKeyState.UNCLAIMED:
            raise _Stop(f"dedup_key_not_unclaimed:{status.state.value}")
        if not _is_first_generation(status):
            # OPEN-14/OPEN-15: a released generation means a same-key retry. Not decided.
            raise _Stop("dedup_same_key_retry_not_supported")

    def _post_claim_key_check(self, key: str) -> tuple[str, ...]:
        """After FIRST_CLAIM and before any marker/submit: the claimed generation must be the
        FIRST one and still only claimed. Same generation predicate as the step-1 gate.
        ``claim`` takes the CURRENT generation, so without this a racing caller could win a
        released generation (a same-key retry, OPEN-14/15). Fails closed on inspect errors."""

        try:
            status = self._store.inspect(key)
        except Exception:
            return ("dedup_post_claim_inspect_failed",)
        if status.state is not DedupKeyState.CLAIMED_NOT_ATTEMPTED:
            return (f"dedup_post_claim_state_unexpected:{status.state.value}",)
        if not _is_first_generation(status):
            return ("released_generation_not_retried",)
        return ()

    def _require_fresh(
        self,
        observed_at: datetime,
        now: datetime,
        bound: timedelta | None,
        label: str,
    ) -> None:
        if not isinstance(bound, timedelta) or bound < timedelta(0):
            raise _Stop(f"{label}_freshness_bound_missing")  # OPEN-1: no invented bound
        age = now - observed_at
        if age < timedelta(0) or age > bound:
            raise _Stop(f"{label}_stale")

    # -- step 2 ------------------------------------------------------------

    def _step2_resolution(
        self, inputs: PipelineInputs, evidence: ReconciliationEvidence, now: datetime
    ) -> _Resolution:
        intent = inputs.intent
        if self._resolver is None:
            raise _Stop("execution_binding_missing")
        try:
            instrument_id = self._resolver(intent.symbol)
        except Exception:
            raise _Stop("execution_binding_missing") from None
        if type(instrument_id) is not str or not instrument_id.strip():
            raise _Stop("execution_binding_missing")

        position: PositionRecord | None = None
        quantity: Decimal | None
        position_ref: str | None = None
        new_position_ref: str | None = None
        protection_change: ProtectionChange | None = None
        protection: ProtectionRequest | None = inputs.protection
        nexora_ref: str

        if intent.kind is TradeIntentKind.OPEN:
            quantity = self._payload_quantity(inputs)
            new_position_ref = derive_new_position_ref(intent)
            nexora_ref = new_position_ref
            if any(p.position_id == new_position_ref for p in inputs.local_positions):
                raise _Stop("new_position_ref_in_use")
        else:
            position = self._find_position(inputs)
            position_ref = position.position_id
            nexora_ref = position_ref
            if position.state not in _OPEN_STATES:
                # EMERGENCY included: recovery is never attempted here (OPEN-20).
                raise _Stop("position_not_open_or_managing")
            if position.symbol != intent.symbol:
                raise _Stop("position_symbol_mismatch")
            if position.side != intent.side:
                raise _Stop("position_side_mismatch")
            match = self._match_record(evidence, position_ref)
            if intent.kind is TradeIntentKind.MODIFY_PROTECTION:
                quantity = None
                if protection is None:
                    raise _Stop("protection_payload_missing")
                protection_change = self._derive_protection_change(position, protection)
                if protection_change is None:
                    raise _Stop("protection_change_unclassified")
            else:
                if match.local_quantity != position.quantity or (
                    match.broker_quantity != position.quantity
                ):
                    raise _Stop("resolution_quantity_not_confirmed_by_evidence")
                if intent.kind is TradeIntentKind.CLOSE:
                    quantity = position.quantity  # never fabricated; never capped
                else:
                    quantity = self._payload_quantity(inputs)
                    assert quantity is not None
                    if not quantity < position.quantity:
                        raise _Stop("reduce_quantity_invalid")
                protection = None

        try:
            resolved = ResolvedExecution(
                intent_proposal_id=intent.proposal_id,
                action=intent.kind,
                instrument_id=instrument_id,
                side=intent.side,
                nexora_position_ref=nexora_ref,
                resolved_quantity=quantity,
                protection_change=protection_change,
                reconciliation_evidence_ref=evidence.evidence_ref,
                reconciliation_observed_at=evidence.observed_at,
                resolved_at=now,
            )
        except ExecutionContractError:
            raise _Stop("resolution_invalid") from None
        return _Resolution(
            resolved=resolved,
            instrument_id=instrument_id,
            position=position,
            quantity=quantity,
            position_ref=position_ref,
            new_position_ref=new_position_ref,
            protection_change=protection_change,
            protection=protection,
        )

    @staticmethod
    def _payload_quantity(inputs: PipelineInputs) -> Decimal | None:
        quantity = inputs.requested_quantity
        if (
            not isinstance(quantity, Decimal)
            or not quantity.is_finite()
            or quantity <= 0  # OPEN-17: absent/invalid payload denies; never defaulted
        ):
            raise _Stop("quantity_payload_missing")
        return quantity

    @staticmethod
    def _find_position(inputs: PipelineInputs) -> PositionRecord:
        origin = inputs.intent.origin
        if not isinstance(origin, (PositionOrigin, ManualOrigin)) or origin.position_id is None:
            raise _Stop("position_reference_missing")
        matches = [p for p in inputs.local_positions if p.position_id == origin.position_id]
        if not matches:
            raise _Stop("position_not_found")
        if len(matches) > 1:
            raise _Stop("position_not_unique")
        return matches[0]

    @staticmethod
    def _match_record(evidence: ReconciliationEvidence, position_ref: str) -> ReconciliationRecord:
        own = [r for r in evidence.records if r.position_ref == position_ref]
        if len(own) != 1 or own[0].finding is not ReconciliationFinding.MATCH:
            raise _Stop("resolution_evidence_not_match")
        return own[0]

    @staticmethod
    def _derive_protection_change(
        position: PositionRecord, protection: ProtectionRequest
    ) -> ProtectionChange | None:
        """TIGHTEN only for a stop-only change that strictly tightens (same rule the
        supervisor uses); every other shape is unclassified => deny (ADR 3.4, OPEN-10).
        WIDEN is never derived."""

        if protection.stop_price is None or protection.target_prices:
            return None
        try:
            _require_tightening_only(position, protection.stop_price)
        except PositionInputError:
            return None
        return "TIGHTEN"

    # -- step 2b -----------------------------------------------------------

    @staticmethod
    def _step2b_authority(inputs: PipelineInputs, res: _Resolution) -> AuthorityDecision:
        try:
            return authorize_trade_intent(
                inputs.intent,
                health=inputs.health,
                config=inputs.config,
                entry_readiness=inputs.entry_readiness,
                risk_decision=inputs.risk_decision,
                protection_change=res.protection_change,
            )
        except (ValueError, TypeError, AttributeError):
            raise _Stop("authority_inputs_invalid") from None

    # -- step 3 ------------------------------------------------------------

    @staticmethod
    def _step3_guard(
        inputs: PipelineInputs,
        res: _Resolution,
        authority: AuthorityDecision,
        status: ReconStatus,
        created_at: datetime,
    ) -> GuardDecision:
        guard = evaluate_execution_guard(
            authority=authority,
            intent=inputs.intent,
            reconciliation=status,
            config=inputs.config,
            kill_switch=inputs.kill_switch,
            request_id=inputs.request_id,
            instrument_id=res.instrument_id,
            created_at=created_at,
            quantity=res.quantity,
            price_constraint=inputs.price_constraint,
            protection=res.protection,
            position_ref=res.position_ref,
            protection_change=res.protection_change,
            new_position_ref=res.new_position_ref,
        )
        if not guard.allowed or guard.request is None:
            raise _Stop(*(guard.reason_codes or ("guard_denied",)))
        return guard

    # -- step 4 ------------------------------------------------------------

    def _step4_preflight(
        self, inputs: PipelineInputs, request: ExecutionRequest
    ) -> PreflightDecision:
        now = self._now()
        port: _PreflightPort = (
            self._transmission.preflight
            if self._transmission is not None
            else self._production_preflight
        )
        decision = port.evaluate(
            request,
            inputs.capabilities,
            None,
            now=now,
            max_capabilities_age=self._max_capabilities_age,
        )
        if not isinstance(decision, PreflightDecision):
            raise _Stop("preflight_decision_invalid")
        if not decision.allowed:
            raise _Stop(*decision.reason_codes)
        # Defense in depth: PreflightDecision is constructible directly, so an allowed
        # instance is never authorization by itself. Production composition has no
        # transmission wiring and therefore always stops here (and preflight is deny-only).
        if self._transmission is None:
            raise _Stop("transmission_not_wired")
        if decision.request_ref != request.request_id:
            raise _Stop("preflight_request_mismatch")
        self._require_fresh(decision.evaluated_at, now, self._max_preflight_age, "preflight")
        return decision

    # -- step 6 ------------------------------------------------------------

    def _step6_last_look(
        self,
        inputs: PipelineInputs,
        res: _Resolution,
        authority: AuthorityDecision,
        status: ReconStatus,
        created_at: datetime,
        request: ExecutionRequest,
        evidence: ReconciliationEvidence,
        decision: PreflightDecision,
    ) -> tuple[str, ...]:
        """Returns failure codes (empty = pass). A failure aborts the claimed key."""

        try:
            now = self._now()
            self._require_fresh(
                evidence.observed_at, now, self._max_evidence_age, "reconciliation_evidence"
            )
            self._require_fresh(decision.evaluated_at, now, self._max_preflight_age, "preflight")
            again = self._step3_guard(inputs, res, authority, status, created_at)
            if again.request != request:  # pragma: no cover - the guard is pure
                return ("last_look_request_changed",)
        except _Stop as stop:
            return tuple(f"last_look:{code}" for code in stop.codes)
        except Exception:
            return ("last_look_failed",)
        return ()

    # -- step 8 ------------------------------------------------------------

    @staticmethod
    def _normalize_result(request: ExecutionRequest, raw: object) -> ExecutionResult:
        """Row 11 (ADR 3.10): an adapter result that is not a well-formed result for THIS
        request is persisted as UNKNOWN (``result_inconsistent_with_request``), never as
        the claimed status."""

        expected_ref = request.position_ref or request.new_position_ref
        consistent = (
            isinstance(raw, ExecutionResult)
            and raw.action is request.action  # also rejects legacy action=None results
            and raw.request_ref == request.request_id
            and raw.nexora_position_ref == expected_ref
            and raw.requested_quantity == request.quantity
        )
        if consistent:
            assert isinstance(raw, ExecutionResult)
            return raw
        observed_at = raw.observed_at if isinstance(raw, ExecutionResult) else request.created_at
        result_id = (
            raw.result_id
            if isinstance(raw, ExecutionResult)
            else f"pipeline-unknown:{request.idempotency_key}"
        )
        return ExecutionResult(
            result_id=result_id,
            request_ref=request.request_id,
            status=ExecutionStatus.UNKNOWN,
            requested_quantity=request.quantity,
            filled_quantity=Decimal("0"),
            remaining_quantity=None,
            reason_code="result_inconsistent_with_request",
            observed_at=observed_at,
            action=request.action,
            nexora_position_ref=expected_ref,
        )

    def _step8_persist(
        self, key: str, result: ExecutionResult
    ) -> tuple[bool, tuple[str, ...], bool]:
        # Orchestration-boundary enforcement (PR-4 gate): a result is recorded ONLY with
        # durable attempt evidence. The store itself still accepts legacy results.
        try:
            state = self._store.inspect(key).state
        except Exception:
            return False, ("result_attempt_verification_failed",), False
        if state is not DedupKeyState.ATTEMPTED_NO_RESULT:
            return False, ("result_without_durable_attempt",), False
        try:
            self._store.record_result(key, result)
        except Exception:
            return False, ("result_persist_failed",), False
        released = False
        codes: tuple[str, ...] = ()
        if is_safe_to_retry_without_reconciliation(result):
            # ADR step 8: release ONLY a clean zero-fill REJECTED. A release never
            # resubmits; the pipeline does not loop (and step 1 denies a released key).
            try:
                released = self._store.release_for_retry(key)
            except Exception:
                codes = ("release_failed",)
        return True, codes, released

    # -- step 9 ------------------------------------------------------------

    @staticmethod
    def _step9_lifecycle(
        res: _Resolution, request: ExecutionRequest, result: ExecutionResult
    ) -> tuple[LifecycleStatus, PositionRecord | None, tuple[str, ...]]:
        if res.position is None:
            return LifecycleStatus.NOT_APPLICABLE_OPEN, None, ()
        try:
            nxt = apply_execution_result(res.position, request, result)
        except PositionInputError as exc:
            # A recovery-authorization denial is DISTINCT from a stale/invalid result.
            if exc.code == RECOVERY_AUTHORIZATION_NOT_RESOLVED:
                return LifecycleStatus.RECOVERY_DENIED, None, (RECOVERY_AUTHORIZATION_NOT_RESOLVED,)
            return (
                LifecycleStatus.RECONCILIATION_REQUIRED,
                None,
                ("lifecycle_result_invalid_reconciliation_required",),
            )
        except Exception:
            return (
                LifecycleStatus.RECONCILIATION_REQUIRED,
                None,
                ("lifecycle_result_invalid_reconciliation_required",),
            )
        if nxt == res.position:
            return LifecycleStatus.UNCHANGED, None, ()
        return LifecycleStatus.APPLIED, nxt, ()

    # -- helpers -----------------------------------------------------------

    def _state_codes(self, key: str) -> tuple[str, ...]:
        try:
            return (f"dedup_state:{self._store.inspect(key).state.value}",)
        except Exception:
            return ("dedup_state_unreadable",)
