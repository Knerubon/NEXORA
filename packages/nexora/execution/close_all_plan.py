"""Pure CLOSE ALL plan layer (ADR-034 section 8) on top of ``close_all``.

Given the explicit durable ``PositionRecord`` collection (NEXORA-owned) this
produces a typed, deterministic ``CloseAllPlan``: one entry per owned open
position, each carrying the CLOSE ``TradeIntent``, its idempotency key and the
``AuthorityDecision`` from the existing ``authorize_trade_intent`` chain
(reused, never reimplemented). Broker-only/external position refs are only
*reported* in ``excluded_not_owned``; they can never become an intent.

No I/O, no broker call, no ``ExecutionRequest`` creation and nothing is sent:
building the request is the Execution Guard's job, transmitting is later work.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from nexora.autonomous.authority import (
    AuthorityDecision,
    AuthorityPolicyStatus,
    ExecutionTransmissibility,
    authorize_trade_intent,
)
from nexora.autonomous.health import SystemHealthSnapshot
from nexora.autonomous_contracts import ManualOrigin, TradeIntent
from nexora.execution.close_all import build_manual_close_all_intents, owned_open_positions
from nexora.execution.idempotency import execution_request_idempotency_key
from nexora.execution.models import ExecutionContractError
from nexora.position.models import PositionRecord


@dataclass(frozen=True, slots=True)
class ObservedExternalPositionRef:
    """A broker-observed position that is NOT in NEXORA's durable records.

    Report-only: it carries no intent-producing capability and is never
    consulted to establish ownership.
    """

    external_ref: str
    symbol: str | None = None

    def __post_init__(self) -> None:
        if not self.external_ref.strip():
            raise ExecutionContractError("missing_external_position_ref")


@dataclass(frozen=True, slots=True)
class CloseAllPlanEntry:
    """One owned position's CLOSE intent with its authority verdict."""

    position_id: str
    intent: TradeIntent
    idempotency_key: str
    authority: AuthorityDecision

    @property
    def transmittable(self) -> bool:
        """True only if policy-authorized AND currently transmittable. An
        authorized-but-NOT_TRANSMITTABLE entry is still listed, never dropped,
        and never reported as executable."""

        return (
            self.authority.allowed
            and self.authority.transmissibility is ExecutionTransmissibility.TRANSMITTABLE
        )


@dataclass(frozen=True, slots=True)
class CloseAllPlan:
    entries: tuple[CloseAllPlanEntry, ...]
    excluded_not_owned: tuple[ObservedExternalPositionRef, ...]
    excluded_not_open: tuple[str, ...]
    excluded_out_of_scope: tuple[str, ...]

    @property
    def intents(self) -> tuple[TradeIntent, ...]:
        return tuple(entry.intent for entry in self.entries)

    @property
    def transmittable_entries(self) -> tuple[CloseAllPlanEntry, ...]:
        return tuple(entry for entry in self.entries if entry.transmittable)

    @property
    def authorized_not_transmittable_entries(self) -> tuple[CloseAllPlanEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.authority.policy_status is AuthorityPolicyStatus.AUTHORIZED
            and entry.authority.transmissibility is ExecutionTransmissibility.NOT_TRANSMITTABLE
        )

    @property
    def denied_entries(self) -> tuple[CloseAllPlanEntry, ...]:
        denied = AuthorityPolicyStatus.DENIED
        return tuple(e for e in self.entries if e.authority.policy_status is denied)

    @property
    def counts(self) -> Mapping[str, int]:
        return {
            "owned_open": len(self.entries),
            "transmittable": len(self.transmittable_entries),
            "authorized_not_transmittable": len(self.authorized_not_transmittable_entries),
            "denied": len(self.denied_entries),
            "excluded_not_owned": len(self.excluded_not_owned),
            "excluded_not_open": len(self.excluded_not_open),
            "excluded_out_of_scope": len(self.excluded_out_of_scope),
        }


def build_close_all_plan(
    positions: Iterable[PositionRecord],
    *,
    health: SystemHealthSnapshot,
    operator_ref: str,
    manual_request_id_prefix: str,
    requested_at: datetime,
    symbol: str | None = None,
    external_positions: Iterable[ObservedExternalPositionRef] = (),
    health_by_position: Mapping[str, SystemHealthSnapshot] | None = None,
) -> CloseAllPlan:
    """Builds the deterministic CLOSE ALL plan.

    ``health_by_position`` optionally overrides ``health`` for specific
    position ids (already-computed inputs; nothing is fetched here). Duplicate
    ``position_id`` values, or duplicate external refs, fail closed.
    """

    all_positions = tuple(positions)
    seen: set[str] = set()
    for position in all_positions:
        if position.position_id in seen:
            raise ExecutionContractError("duplicate_position_id_in_close_all_input")
        seen.add(position.position_id)

    externals = tuple(external_positions)
    external_refs = [ref.external_ref for ref in externals]
    if len(set(external_refs)) != len(external_refs):
        raise ExecutionContractError("duplicate_external_position_ref_in_close_all_input")

    overrides = health_by_position or {}
    unknown = set(overrides) - seen
    if unknown:
        raise ExecutionContractError("health_override_for_unknown_position")

    all_owned = owned_open_positions(all_positions)
    scoped_ids = {p.position_id for p in owned_open_positions(all_positions, symbol=symbol)}
    owned_ids = {p.position_id for p in all_owned}
    not_open = tuple(p.position_id for p in all_positions if p.position_id not in owned_ids)
    out_of_scope = tuple(p.position_id for p in all_owned if p.position_id not in scoped_ids)

    intents = build_manual_close_all_intents(
        all_positions,
        operator_ref=operator_ref,
        manual_request_id_prefix=manual_request_id_prefix,
        requested_at=requested_at,
        symbol=symbol,
    )
    entries: list[CloseAllPlanEntry] = []
    for intent in intents:
        origin = intent.origin
        if not isinstance(origin, ManualOrigin) or origin.position_id is None:
            raise ExecutionContractError("close_all_intent_missing_position_scope")
        position_id = origin.position_id
        decision = authorize_trade_intent(intent, health=overrides.get(position_id, health))
        entries.append(
            CloseAllPlanEntry(
                position_id=position_id,
                intent=intent,
                idempotency_key=execution_request_idempotency_key(intent),
                authority=decision,
            )
        )
    return CloseAllPlan(
        entries=tuple(entries),
        excluded_not_owned=externals,
        excluded_not_open=not_open,
        excluded_out_of_scope=out_of_scope,
    )
