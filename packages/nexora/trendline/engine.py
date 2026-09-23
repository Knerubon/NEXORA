"""Causal P&F trendline engine (ADR-020).

Anchors are consumed directly from ``StructureEngine``'s ``ConfirmedPivot``
stream -- this engine never rediscovers pivots on its own. Every decision
(anchor selection, projection, break, touch, retest) is a pure function of
transitions/pivots already processed as of the current step: appending a
future transition can never rewrite a previously recorded ``TrendlineLine``.

Same-transition precedence (ADR-020, frozen): a retest resolution
(``retesting`` -> ``retest_held``/``retest_failed``) is never replaced on the
same transition that produced it, even if a fresh valid same-kind anchor
pair is already available. The resolved state must be observable as the
current line for at least the snapshot of the resolving transition.
Replacement eligibility begins strictly on a later transition -- tracked via
``TrendlineLine.retest_resolved_sequence`` compared against the engine's own
monotonic transition-processing sequence, never wall-clock time.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from nexora.artifacts import canonical_hash
from nexora.pnf import PnfTransition
from nexora.structure import StructureSnapshot
from nexora.trendline.models import (
    RetestOutcome,
    TrendlineAnchor,
    TrendlineKind,
    TrendlineLine,
    TrendlineSnapshot,
)

_REPLACEABLE_STATES = frozenset({"broken", "retest_held", "retest_failed"})


@dataclass(slots=True)
class TrendlineEngine:
    symbol: str
    _sequence: int = field(default=0, init=False, repr=False)
    _column_by_transition: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _known_pivot_count: int = field(default=0, init=False, repr=False)
    _lows: list[TrendlineAnchor] = field(default_factory=list, init=False, repr=False)
    _highs: list[TrendlineAnchor] = field(default_factory=list, init=False, repr=False)
    _active_bullish: TrendlineLine | None = field(default=None, init=False, repr=False)
    _active_bearish: TrendlineLine | None = field(default=None, init=False, repr=False)
    _history: list[TrendlineLine] = field(default_factory=list, init=False, repr=False)

    def process(self, transition: PnfTransition, structure: StructureSnapshot) -> None:
        """Mutate engine state for one transition. Call `snapshot()` separately to read it.

        Split from snapshot construction so a caller processing several transitions per
        event (e.g. a multi-box gap fill) doesn't pay for an O(history) tuple copy on every
        intermediate transition it never reads.
        """
        if transition.symbol != self.symbol:
            return
        self._sequence += 1
        self._column_by_transition[transition.identity_key] = transition.column_id
        self._ingest_new_pivots(structure)
        self._active_bullish = self._evaluate_line(self._active_bullish, transition)
        self._active_bearish = self._evaluate_line(self._active_bearish, transition)
        self._try_form_or_replace(transition)

    def snapshot(self) -> TrendlineSnapshot:
        return TrendlineSnapshot(
            schema_version=1,
            symbol=self.symbol,
            sequence=self._sequence,
            active_bullish=self._active_bullish,
            active_bearish=self._active_bearish,
            history=tuple(self._history),
        )

    def _ingest_new_pivots(self, structure: StructureSnapshot) -> None:
        pivots = structure.pivots
        if len(pivots) <= self._known_pivot_count:
            return
        for pivot in pivots[self._known_pivot_count :]:
            column_id = self._column_by_transition.get(pivot.source_transition_id)
            if column_id is None:
                continue
            anchor = TrendlineAnchor(
                pivot_kind=pivot.kind,
                price=pivot.price,
                column_id=column_id,
                source_pivot_id=pivot.source_transition_id,
                event_time=pivot.occurrence_time,
            )
            if pivot.kind == "low":
                self._lows.append(anchor)
            else:
                self._highs.append(anchor)
        self._known_pivot_count = len(pivots)

    def _evaluate_line(
        self, line: TrendlineLine | None, transition: PnfTransition
    ) -> TrendlineLine | None:
        if line is None or line.state not in ("active", "broken", "retesting"):
            return line
        box = transition.effective_box_size
        column = transition.column_id
        slope = line.slope_price_per_column
        projected = line.anchor_b.price + slope * (column - line.anchor_b.column_id)
        distance = transition.to_price - projected
        age = column - line.anchor_b.column_id
        bullish = line.kind == "bullish_support"

        if line.state == "active":
            is_break = distance < 0 if bullish else distance > 0
            if is_break:
                return replace(
                    line,
                    state="broken",
                    projected_price_at_latest_column=projected,
                    break_column=column,
                    break_transition_id=transition.identity_key,
                    age_columns=age,
                    evidence=(*line.evidence, f"break@col{column}"),
                )
            is_touch = (0 <= distance < box) if bullish else (0 <= -distance < box)
            if is_touch:
                return replace(
                    line,
                    projected_price_at_latest_column=projected,
                    touch_columns=(*line.touch_columns, column),
                    age_columns=age,
                    evidence=(*line.evidence, f"touch@col{column}"),
                )
            return replace(line, projected_price_at_latest_column=projected, age_columns=age)

        if line.state == "broken":
            if abs(distance) < box:
                return replace(
                    line,
                    state="retesting",
                    projected_price_at_latest_column=projected,
                    retest_column=column,
                    age_columns=age,
                    evidence=(*line.evidence, f"retest_entry@col{column}"),
                )
            return replace(line, projected_price_at_latest_column=projected, age_columns=age)

        held = distance <= -box if bullish else distance >= box
        failed = distance >= box if bullish else distance <= -box
        outcome: RetestOutcome | None = "held" if held else ("failed" if failed else None)
        if outcome is None:
            return replace(line, projected_price_at_latest_column=projected, age_columns=age)
        return replace(
            line,
            state="retest_held" if outcome == "held" else "retest_failed",
            retest_outcome=outcome,
            projected_price_at_latest_column=projected,
            retest_resolved_column=column,
            retest_resolved_sequence=self._sequence,
            age_columns=age,
            evidence=(*line.evidence, f"retest_{outcome}@col{column}"),
        )

    def _try_form_or_replace(self, transition: PnfTransition) -> None:
        plans: tuple[tuple[TrendlineKind, list[TrendlineAnchor], TrendlineLine | None], ...] = (
            ("bullish_support", self._lows, self._active_bullish),
            ("bearish_resistance", self._highs, self._active_bearish),
        )
        for kind, pool, current in plans:
            if len(pool) < 2:
                continue
            anchor_a, anchor_b = pool[-2], pool[-1]
            if anchor_b.column_id == anchor_a.column_id:
                continue
            valid = (
                anchor_b.price > anchor_a.price
                if kind == "bullish_support"
                else anchor_b.price < anchor_a.price
            )
            if not valid:
                continue
            if current is not None:
                if current.state not in _REPLACEABLE_STATES:
                    continue
                if (
                    current.retest_resolved_sequence is not None
                    and self._sequence <= current.retest_resolved_sequence
                ):
                    # H1 (ADR-020 same-transition precedence): a retest resolution must be
                    # observable as the current line for at least the transition that
                    # produced it. Replacement eligibility begins strictly on a later
                    # transition (self._sequence > retest_resolved_sequence).
                    continue
                if (
                    current.anchor_a.source_pivot_id == anchor_a.source_pivot_id
                    and current.anchor_b.source_pivot_id == anchor_b.source_pivot_id
                ):
                    continue
            new_line = self._build_line(kind, anchor_a, anchor_b, transition)
            if current is not None:
                archived = replace(current, state="replaced", replaced_by_line_id=new_line.line_id)
                self._history.append(archived)
            if kind == "bullish_support":
                self._active_bullish = new_line
            else:
                self._active_bearish = new_line

    def _build_line(
        self,
        kind: TrendlineKind,
        anchor_a: TrendlineAnchor,
        anchor_b: TrendlineAnchor,
        transition: PnfTransition,
    ) -> TrendlineLine:
        slope = (anchor_b.price - anchor_a.price) / Decimal(anchor_b.column_id - anchor_a.column_id)
        column = transition.column_id
        projected = anchor_b.price + slope * (column - anchor_b.column_id)
        line_id = canonical_hash(
            (self.symbol, kind, anchor_a.source_pivot_id, anchor_b.source_pivot_id)
        )
        return TrendlineLine(
            line_id=line_id,
            kind=kind,
            state="active",
            anchor_a=anchor_a,
            anchor_b=anchor_b,
            slope_price_per_column=slope,
            projected_price_at_latest_column=projected,
            touch_columns=(),
            break_column=None,
            break_transition_id=None,
            retest_column=None,
            retest_resolved_column=None,
            retest_resolved_sequence=None,
            retest_outcome="none",
            replaced_by_line_id=None,
            age_columns=column - anchor_b.column_id,
            config_version=transition.config_version,
            evidence=(f"anchors_valid:{anchor_a.source_pivot_id}->{anchor_b.source_pivot_id}",),
        )
