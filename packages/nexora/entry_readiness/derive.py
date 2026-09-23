"""Pure deterministic Entry Readiness evaluation (ADR-021).

``evaluate_entry_readiness`` is a stateless permission filter: it can only
preserve or narrow an already-existing ``SignalDecision.action``. It never
creates BUY/SELL permission and never mutates its inputs. Only the Trendline
line whose kind is aligned with the Signal's action is read; the opposite-kind
line has zero effect on the result (ADR-021 Decision 4).
"""

from __future__ import annotations

from nexora.entry_readiness.models import (
    EntryBlocker,
    EntryBlockerCode,
    EntryReadinessSnapshot,
    EntryReadinessState,
    PendingConfirmation,
)
from nexora.signals.models import SignalAction, SignalDecision
from nexora.trendline.models import TrendlineKind, TrendlineSnapshot

_ALIGNED_KIND: dict[SignalAction, TrendlineKind] = {
    "BUY": "bullish_support",
    "SELL": "bearish_resistance",
}


def evaluate_entry_readiness(
    *,
    decision: SignalDecision,
    trendline: TrendlineSnapshot,
    config_version: str,
) -> EntryReadinessSnapshot:
    if decision.action == "WAIT":
        return _snapshot(trendline.symbol, "NOT_READY", "WAIT", (), (), config_version)

    aligned_kind = _ALIGNED_KIND[decision.action]
    aligned_line = (
        trendline.active_bullish if aligned_kind == "bullish_support" else trendline.active_bearish
    )

    if aligned_line is None or aligned_line.state in ("active", "retest_failed"):
        return _snapshot(trendline.symbol, "READY", decision.action, (), (), config_version)

    if aligned_line.state == "retesting":
        pending = PendingConfirmation(
            code="aligned_trendline_retest_pending",
            side=decision.action,
            trendline_kind=aligned_kind,
            line_id=aligned_line.line_id,
            reason=_pending_reason(aligned_kind),
        )
        return _snapshot(
            trendline.symbol, "DEVELOPING", decision.action, (), (pending,), config_version
        )

    if aligned_line.state in ("broken", "retest_held"):
        code: EntryBlockerCode = (
            "aligned_trendline_broken"
            if aligned_line.state == "broken"
            else "aligned_trendline_retest_held"
        )
        blocker = EntryBlocker(
            code=code,
            side=decision.action,
            trendline_kind=aligned_kind,
            line_id=aligned_line.line_id,
            reason=_blocker_reason(code, aligned_kind),
        )
        return _snapshot(
            trendline.symbol, "BLOCKED", decision.action, (blocker,), (), config_version
        )

    raise AssertionError(
        f"unreachable aligned trendline state {aligned_line.state!r}: "
        "active_bullish/active_bearish never holds a 'replaced' line (ADR-020)"
    )


def _snapshot(
    symbol: str,
    state: EntryReadinessState,
    signal_action: SignalAction,
    blockers: tuple[EntryBlocker, ...],
    pending_confirmations: tuple[PendingConfirmation, ...],
    config_version: str,
) -> EntryReadinessSnapshot:
    return EntryReadinessSnapshot(
        schema_version=1,
        symbol=symbol,
        state=state,
        signal_action=signal_action,
        blockers=blockers,
        pending_confirmations=pending_confirmations,
        config_version=config_version,
    )


def _pending_reason(kind: TrendlineKind) -> str:
    label = "Bullish support" if kind == "bullish_support" else "Bearish resistance"
    return f"{label} retest has not yet resolved to RETEST_HELD or RETEST_FAILED."


def _blocker_reason(code: EntryBlockerCode, kind: TrendlineKind) -> str:
    label = "bullish support" if kind == "bullish_support" else "bearish resistance"
    if code == "aligned_trendline_broken":
        return f"Aligned {label} line is broken."
    return f"Aligned {label} line resolved RETEST_HELD, confirming the break."
