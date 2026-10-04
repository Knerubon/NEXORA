"""ExecutionPreflight (ADR-035 s3.5; pipeline step 4, before the claim).

A PURE, read-only, fail-closed function of its inputs. No I/O, no clock read
(``now`` is a parameter), no broker/MT5/network import, no durable write, and it
never constructs or calls a broker adapter. A preflight denial happens before the
claim and therefore creates no durable state (ADR-035 s3.1/3.2 step 4).

Implemented (structural, determinable from existing types): instrument match,
volume validation through the shared ``validate_volume`` (ADR-035 s4.5), capability
freshness against a caller-supplied APPROVED bound.

Deliberately NOT decided here (ADR gaps; every one fails closed, see the task record):

* ``market_refs`` has no frozen shape and BrokerCapabilities has no health field, so
  the ``stops_level``/``freeze_level`` distance check for MODIFY_PROTECTION cannot be
  performed: MODIFY_PROTECTION is always denied with
  ``preflight_stops_freeze_check_unavailable``.
* Policy checks (session/spread/slippage/margin) consume Quant-owned opaque refs whose
  numeric content is undecided. Risk-reducing kinds deny ``preflight_policy_undecided``
  (OPEN-11). OPEN denies ``preflight_policy_evaluation_unavailable``. Consequently
  ``allowed`` is currently unreachable from ``evaluate``; this is intentional.
* No freshness bound exists (OPEN-1). The bound is injected by the caller; none supplied
  or an invalid one denies with ``preflight_capabilities_freshness_bound_missing``.

All ``preflight_*`` codes other than ``preflight_policy_undecided`` and
``preflight_volume_validation_error`` are PENDING ARCHITECT APPROVAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from nexora.autonomous.broker_capabilities import BrokerCapabilities, validate_volume
from nexora.autonomous_contracts import TradeIntentKind, is_risk_reducing
from nexora.execution.models import ExecutionRequest

REASON_POLICY_UNDECIDED = "preflight_policy_undecided"
REASON_VOLUME_VALIDATION_ERROR = "preflight_volume_validation_error"
# Pending Architect approval (names not frozen by ADR-035):
REASON_CLOCK_REQUIRES_TIMEZONE = "preflight_clock_requires_timezone"
REASON_CAPABILITIES_MISSING = "preflight_capabilities_missing"
REASON_INSTRUMENT_MISMATCH = "preflight_instrument_mismatch"
REASON_QUANTITY_MISSING = "preflight_quantity_missing"
REASON_FRESHNESS_BOUND_MISSING = "preflight_capabilities_freshness_bound_missing"
REASON_CAPABILITIES_STALE = "preflight_capabilities_stale"
REASON_STOPS_FREEZE_UNAVAILABLE = "preflight_stops_freeze_check_unavailable"
REASON_POLICY_EVALUATION_UNAVAILABLE = "preflight_policy_evaluation_unavailable"

_QUANTITY_KINDS = (TradeIntentKind.OPEN, TradeIntentKind.REDUCE, TradeIntentKind.CLOSE)


def _is_aware(moment: object) -> bool:
    return isinstance(moment, datetime) and moment.utcoffset() is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class PreflightDecision:
    """ADR-035 s3.5. ``allowed`` iff no reason codes. ``evaluated_at`` is the caller's
    ``now`` as given and ``capabilities_observed_at`` is ``None`` when no capability
    object was usable; both must be aware when ``allowed`` (a deny may carry a naive or
    absent value, because the deny is the reason it is not aware/present).
    """

    allowed: bool
    reason_codes: tuple[str, ...]
    request_ref: str
    evaluated_at: datetime
    capabilities_observed_at: datetime | None

    def __post_init__(self) -> None:
        if self.allowed:
            if self.reason_codes:
                raise ValueError("allowed_preflight_decision_must_not_carry_reasons")
            if not _is_aware(self.evaluated_at) or not _is_aware(self.capabilities_observed_at):
                raise ValueError("allowed_preflight_decision_requires_aware_datetimes")
        elif not self.reason_codes:
            raise ValueError("denied_preflight_decision_requires_reasons")


def _freshness_reasons(
    capabilities: BrokerCapabilities,
    now: datetime,
    max_capabilities_age: timedelta | None,
) -> list[str]:
    if not isinstance(max_capabilities_age, timedelta) or max_capabilities_age < timedelta(0):
        return [REASON_FRESHNESS_BOUND_MISSING]
    if not _is_aware(now) or not _is_aware(capabilities.observed_at):
        return []  # the timezone reason is already recorded
    age = now - capabilities.observed_at
    if age < timedelta(0) or age > max_capabilities_age:
        return [REASON_CAPABILITIES_STALE]
    return []


class ExecutionPreflight:
    """Stateless, side-effect-free preflight evaluator (no constructor inputs)."""

    def evaluate(
        self,
        request: ExecutionRequest,
        capabilities: BrokerCapabilities | None,
        market_refs: object,
        *,
        now: datetime,
        max_capabilities_age: timedelta | None = None,
    ) -> PreflightDecision:
        """``market_refs`` is accepted for the ADR-035 s3.5 signature but unused: its
        shape is undefined (ADR gap). ``max_capabilities_age`` is the APPROVED
        freshness bound supplied by the caller; ``None`` fails closed.
        """

        del market_refs
        reasons: list[str] = []

        def add(code: str) -> None:
            if code not in reasons:
                reasons.append(code)

        if not _is_aware(now):
            add(REASON_CLOCK_REQUIRES_TIMEZONE)

        observed_at: datetime | None = None
        if not isinstance(capabilities, BrokerCapabilities):
            add(REASON_CAPABILITIES_MISSING)
        else:
            observed_at = capabilities.observed_at
            if request.instrument_id != capabilities.binding.instrument_id:
                add(REASON_INSTRUMENT_MISMATCH)
            if request.action in _QUANTITY_KINDS:
                if request.quantity is None:
                    add(REASON_QUANTITY_MISSING)
                else:
                    try:
                        volume_reason = validate_volume(capabilities, request.quantity)
                    except Exception:
                        add(REASON_VOLUME_VALIDATION_ERROR)
                    else:
                        if volume_reason is not None:
                            add(volume_reason)
            if not _is_aware(capabilities.observed_at):
                add(REASON_CLOCK_REQUIRES_TIMEZONE)
            for code in _freshness_reasons(capabilities, now, max_capabilities_age):
                add(code)

        if request.action is TradeIntentKind.MODIFY_PROTECTION:
            add(REASON_STOPS_FREEZE_UNAVAILABLE)
        if is_risk_reducing(request.action):
            add(REASON_POLICY_UNDECIDED)
        else:
            add(REASON_POLICY_EVALUATION_UNAVAILABLE)

        return PreflightDecision(
            allowed=not reasons,
            reason_codes=tuple(reasons),
            request_ref=request.request_id,
            evaluated_at=now,
            capabilities_observed_at=observed_at,
        )
