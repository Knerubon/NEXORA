"""Persistence contract for risk decisions and state snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.risk.models import RiskDecision, RiskState


class RiskDecisionStore:
    def __init__(self) -> None:
        self._decisions: list[str] = []
        self._states: list[str] = []

    def append_decision(self, decision: RiskDecision) -> None:
        payload = asdict(decision)
        payload["effective_time"] = decision.effective_time.isoformat()
        self._decisions.append(json.dumps(payload, sort_keys=True, default=str))

    def append_state(self, state: RiskState) -> None:
        self._states.append(json.dumps(asdict(state), sort_keys=True, default=str))

    def replay_decisions(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._decisions)

    def replay_states(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._states)
