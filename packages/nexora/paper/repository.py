"""Persistence contract for paper orders, fills, and checkpoints."""

from __future__ import annotations

import json
from dataclasses import asdict

from nexora.paper.models import PaperCheckpoint, PaperFill, PaperLedgerEntry, PaperOrder, PaperState


class PaperLedgerStore:
    def __init__(self) -> None:
        self._orders: list[str] = []
        self._fills: list[str] = []
        self._entries: list[str] = []
        self._checkpoints: list[str] = []
        self._states: list[str] = []

    def append_order(self, order: PaperOrder) -> None:
        payload = asdict(order)
        payload["created_at"] = order.created_at.isoformat()
        self._orders.append(json.dumps(payload, sort_keys=True, default=str))

    def append_fill(self, fill: PaperFill) -> None:
        payload = asdict(fill)
        payload["filled_at"] = fill.filled_at.isoformat()
        self._fills.append(json.dumps(payload, sort_keys=True, default=str))

    def append_ledger_entry(self, entry: PaperLedgerEntry) -> None:
        payload = asdict(entry)
        payload["created_at"] = entry.created_at.isoformat()
        self._entries.append(json.dumps(payload, sort_keys=True, default=str))

    def append_checkpoint(self, checkpoint: PaperCheckpoint) -> None:
        payload = asdict(checkpoint)
        payload["created_at"] = checkpoint.created_at.isoformat()
        self._checkpoints.append(json.dumps(payload, sort_keys=True, default=str))

    def append_state(self, state: PaperState) -> None:
        self._states.append(json.dumps(asdict(state), sort_keys=True, default=str))

    def replay_orders(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._orders)

    def replay_fills(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._fills)

    def replay_ledger(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._entries)

    def replay_checkpoints(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._checkpoints)

    def replay_states(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(item) for item in self._states)
