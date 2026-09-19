"""Explainable research signal engine."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import cast

from nexora.market_regime import RegimeSnapshot
from nexora.matrix import MatrixSnapshot
from nexora.pnf import PnfTransition
from nexora.signals.models import (
    PatternEvidence,
    PriceRange,
    ResearchSignal,
    SignalAction,
    SignalConfig,
    SignalDecision,
    SignalEvidence,
    SignalSide,
    SignalSnapshot,
    SignalTarget,
)
from nexora.structure import CandidateLevel, StructureSnapshot


@dataclass(slots=True)
class SignalEngine:
    config: SignalConfig
    _sequence: int = field(default=0, init=False, repr=False)
    _history: list[ResearchSignal] = field(default_factory=list, init=False, repr=False)
    _last_signal_sequence: int | None = field(default=None, init=False, repr=False)
    _last_decision: SignalDecision = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._last_decision = self._wait_decision(
            reason_code="waiting_for_inputs",
            reason_text="No decision yet.",
            source_refs=(),
        )

    def evaluate(
        self,
        *,
        structure: StructureSnapshot,
        regime: RegimeSnapshot,
        matrix: MatrixSnapshot,
    ) -> SignalSnapshot:
        self._sequence += 1
        self._expire_if_needed()

        if self._cooldown_active():
            self._last_decision = self._wait_decision(
                reason_code="cooldown_active",
                reason_text="Signal cooldown window is active.",
                source_refs=("cooldown",),
            )
            return self.snapshot()
        if (
            not structure.pivots
            or matrix.alignment == "unavailable"
            or regime.state.label == "unknown"
        ):
            self._last_decision = self._wait_decision(
                reason_code="insufficient_inputs",
                reason_text="Confirmed structure, matrix, or regime inputs are unavailable.",
                source_refs=("insufficient_inputs",),
            )
            return self.snapshot()

        # Reject inconsistent snapshots instead of consuming future confirmations.
        cutoff = matrix.generated_at
        if (
            any(
                p.confirmation_time > cutoff or p.occurrence_time > p.confirmation_time
                for p in structure.pivots
            )
            or any(level.updated_at > cutoff for level in structure.levels)
            or regime.state.effective_time > cutoff
            or any(
                r.latest_transition is not None and r.latest_transition.event_time > cutoff
                for r in matrix.resolutions
            )
        ):
            self._last_decision = self._wait_decision(
                reason_code="future_inputs",
                reason_text="Inputs contain information confirmed after decision time.",
                source_refs=("future_inputs",),
            )
            return self.snapshot()

        assessment = self._assess_components(
            structure=structure,
            regime=regime,
            matrix=matrix,
        )
        action, score = self._resolve_action_and_score(
            assessment.buy_points, assessment.sell_points
        )
        if matrix.alignment == "mixed" or regime.state.label in {"range", "high_volatility"}:
            action = "WAIT"
            score = min(score, self.config.wait_score_max)
        source_refs = _dedupe_refs(
            (
                *assessment.source_refs,
                regime.state.source_ref or "regime:none",
            )
        )

        if action == "WAIT":
            self._last_decision = SignalDecision(
                action="WAIT",
                score=score,
                buy_strength=max(0, min(100, assessment.buy_points)),
                sell_strength=max(0, min(100, assessment.sell_points)),
                strength_available=True,
                entry_zone=None,
                invalidation_price=None,
                invalidation_reason=None,
                targets=(),
                risk_reward=None,
                patterns=assessment.patterns,
                positive_evidence=assessment.positive_evidence,
                negative_evidence=assessment.negative_evidence,
                future_conditions=self._future_conditions(regime=regime, matrix=matrix),
                config_version=self.config.version,
                engine_version=f"{self.config.version}:p8b-v2",
                source_refs=source_refs,
            )
            return self.snapshot()

        setup = self._build_trade_setup(
            action=action,
            structure=structure,
            matrix=matrix,
        )
        if setup is None:
            self._last_decision = self._wait_decision(
                reason_code="missing_trade_setup",
                reason_text="Deterministic entry/invalidation targets are unavailable.",
                source_refs=source_refs,
            )
            self._last_decision = replace(
                self._last_decision,
                buy_strength=max(0, min(100, assessment.buy_points)),
                sell_strength=max(0, min(100, assessment.sell_points)),
                strength_available=True,
                patterns=assessment.patterns,
                positive_evidence=assessment.positive_evidence,
                negative_evidence=(
                    *assessment.negative_evidence,
                    *self._last_decision.negative_evidence,
                ),
            )
            return self.snapshot()
        entry_zone, invalidation_price, invalidation_reason, targets, risk_reward = setup

        side: SignalSide = "long" if action == "BUY" else "short"
        reasons = tuple(item.reason for item in assessment.positive_evidence)
        if not reasons:
            reasons = ("Scoring did not reach actionable evidence threshold.",)
        reason_codes = tuple(item.code for item in assessment.positive_evidence)
        signal = ResearchSignal(
            signal_id=f"{self.config.symbol}:{self._sequence}:{side}",
            symbol=self.config.symbol,
            side=side,
            sequence=self._sequence,
            occurrence_time=assessment.occurrence_time,
            confirmation_time=assessment.confirmation_time,
            decision_time=matrix.generated_at,
            reasons=reasons,
            reason_codes=reason_codes,
            source_refs=source_refs,
            config_version=self.config.version,
            engine_versions=(
                assessment.engine_version_ref,
                regime.state.config_version,
                f"{self.config.version}:p8b-v2",
            ),
            status="active",
            decision=SignalDecision(
                action=action,
                score=score,
                buy_strength=max(0, min(100, assessment.buy_points)),
                sell_strength=max(0, min(100, assessment.sell_points)),
                strength_available=True,
                entry_zone=entry_zone,
                invalidation_price=invalidation_price,
                invalidation_reason=invalidation_reason,
                targets=targets,
                risk_reward=risk_reward,
                patterns=assessment.patterns,
                positive_evidence=assessment.positive_evidence,
                negative_evidence=assessment.negative_evidence,
                future_conditions=self._future_conditions(regime=regime, matrix=matrix),
                config_version=self.config.version,
                engine_version=f"{self.config.version}:p8b-v2",
                source_refs=source_refs,
            ),
        )
        self._last_decision = cast(SignalDecision, signal.decision)
        if self._history and self._is_duplicate(self._history[-1], signal):
            return self.snapshot()
        self._history.append(signal)
        self._last_signal_sequence = self._sequence
        return self.snapshot()

    def snapshot(self) -> SignalSnapshot:
        latest = self._history[-1] if self._history else None
        return SignalSnapshot(
            schema_version=1,
            symbol=self.config.symbol,
            sequence=self._sequence,
            latest=latest,
            history=tuple(self._history),
            decision=self._last_decision,
        )

    @classmethod
    def from_snapshot(cls, config: SignalConfig, snapshot: SignalSnapshot) -> SignalEngine:
        engine = cls(config=config)
        engine._sequence = snapshot.sequence
        engine._history = list(snapshot.history)
        engine._last_decision = snapshot.decision
        if snapshot.latest is not None and snapshot.latest.status == "active":
            engine._last_signal_sequence = snapshot.latest.sequence
        return engine

    def _cooldown_active(self) -> bool:
        if self._last_signal_sequence is None:
            return False
        return (self._sequence - self._last_signal_sequence) <= self.config.cooldown_events

    def _expire_if_needed(self) -> None:
        if not self._history:
            return
        latest = self._history[-1]
        if (
            latest.status == "active"
            and (self._sequence - latest.sequence) > self.config.expiry_events
        ):
            self._history[-1] = replace(latest, status="expired")

    def _assess_components(
        self,
        *,
        structure: StructureSnapshot,
        regime: RegimeSnapshot,
        matrix: MatrixSnapshot,
    ) -> _Assessment:
        latest_pivot = structure.pivots[-1]
        positive: list[SignalEvidence] = []
        negative: list[SignalEvidence] = []
        source_refs = [latest_pivot.source_transition_id]
        buy_points = 0
        sell_points = 0

        pnf_side, pnf_reason, pnf_code, pnf_ref = self._pnf_evidence(matrix)
        if pnf_side == "BUY":
            buy_points += self.config.weights.pnf_reversal
            positive.append(
                SignalEvidence(
                    component="pnf",
                    code=pnf_code,
                    points=self.config.weights.pnf_reversal,
                    polarity="bullish",
                    reason=pnf_reason,
                    source_refs=(pnf_ref,),
                )
            )
        elif pnf_side == "SELL":
            sell_points += self.config.weights.pnf_reversal
            positive.append(
                SignalEvidence(
                    component="pnf",
                    code=pnf_code,
                    points=self.config.weights.pnf_reversal,
                    polarity="bearish",
                    reason=pnf_reason,
                    source_refs=(pnf_ref,),
                )
            )
        source_refs.append(pnf_ref)

        structure_side, structure_reason, structure_code, structure_ref = self._structure_evidence(
            structure
        )
        if structure_side == "BUY":
            buy_points += self.config.weights.structure
            positive.append(
                SignalEvidence(
                    component="structure",
                    code=structure_code,
                    points=self.config.weights.structure,
                    polarity="bullish",
                    reason=structure_reason,
                    source_refs=(structure_ref,),
                )
            )
        elif structure_side == "SELL":
            sell_points += self.config.weights.structure
            positive.append(
                SignalEvidence(
                    component="structure",
                    code=structure_code,
                    points=self.config.weights.structure,
                    polarity="bearish",
                    reason=structure_reason,
                    source_refs=(structure_ref,),
                )
            )
        source_refs.append(structure_ref)

        if matrix.alignment == "mixed":
            negative.append(
                SignalEvidence(
                    component="matrix",
                    code="matrix_mixed",
                    points=0,
                    polarity="neutral",
                    reason="Matrix disagreement detected.",
                    source_refs=tuple(
                        state.latest_transition.identity_key
                        if state.latest_transition is not None
                        else f"matrix:{state.name}:none"
                        for state in matrix.resolutions
                    ),
                )
            )
        if regime.state.label == "range":
            negative.append(
                SignalEvidence(
                    component="regime",
                    code="regime_range_caution",
                    points=0,
                    polarity="neutral",
                    reason="Range regime requires breakout confirmation.",
                    source_refs=(regime.state.source_ref or "regime:none",),
                )
            )
        if regime.state.label == "high_volatility":
            negative.append(
                SignalEvidence(
                    component="regime",
                    code="regime_volatility_caution",
                    points=0,
                    polarity="neutral",
                    reason="High volatility regime requires stricter confirmation.",
                    source_refs=(regime.state.source_ref or "regime:none",),
                )
            )

        sr_side, sr_reason, sr_code, sr_ref = self._support_resistance_evidence(
            structure=structure,
            matrix=matrix,
        )
        if sr_side == "BUY":
            buy_points += self.config.weights.support_resistance
            positive.append(
                SignalEvidence(
                    component="support_resistance",
                    code=sr_code,
                    points=self.config.weights.support_resistance,
                    polarity="bullish",
                    reason=sr_reason,
                    source_refs=(sr_ref,),
                )
            )
        elif sr_side == "SELL":
            sell_points += self.config.weights.support_resistance
            positive.append(
                SignalEvidence(
                    component="support_resistance",
                    code=sr_code,
                    points=self.config.weights.support_resistance,
                    polarity="bearish",
                    reason=sr_reason,
                    source_refs=(sr_ref,),
                )
            )
        source_refs.append(sr_ref)

        matrix_side, matrix_reason, matrix_code, matrix_refs = self._matrix_evidence(matrix)
        if matrix_side == "BUY":
            buy_points += self.config.weights.matrix
            positive.append(
                SignalEvidence(
                    component="matrix",
                    code=matrix_code,
                    points=self.config.weights.matrix,
                    polarity="bullish",
                    reason=matrix_reason,
                    source_refs=matrix_refs,
                )
            )
        elif matrix_side == "SELL":
            sell_points += self.config.weights.matrix
            positive.append(
                SignalEvidence(
                    component="matrix",
                    code=matrix_code,
                    points=self.config.weights.matrix,
                    polarity="bearish",
                    reason=matrix_reason,
                    source_refs=matrix_refs,
                )
            )
        source_refs.extend(matrix_refs)

        regime_side, regime_reason, regime_code = self._regime_evidence(
            regime=regime, matrix_side=matrix_side
        )
        if regime_side == "BUY":
            buy_points += self.config.weights.regime
            positive.append(
                SignalEvidence(
                    component="regime",
                    code=regime_code,
                    points=self.config.weights.regime,
                    polarity="bullish",
                    reason=regime_reason,
                    source_refs=(regime.state.source_ref or "regime:none",),
                )
            )
        elif regime_side == "SELL":
            sell_points += self.config.weights.regime
            positive.append(
                SignalEvidence(
                    component="regime",
                    code=regime_code,
                    points=self.config.weights.regime,
                    polarity="bearish",
                    reason=regime_reason,
                    source_refs=(regime.state.source_ref or "regime:none",),
                )
            )

        dominant_before = "BUY" if buy_points >= sell_points else "SELL"
        patterns = tuple(
            replace(
                pattern,
                relation=(
                    "confirmation"
                    if (pattern.direction == "bullish") == (dominant_before == "BUY")
                    else "conflict"
                ),
            )
            for pattern in self._patterns(structure)
        )
        for pattern in patterns:
            if pattern.relation == "confirmation":
                points = self.config.weights.pattern_confirmation
                if pattern.direction == "bullish":
                    buy_points = min(100, buy_points + points)
                    positive.append(
                        SignalEvidence(
                            component="pattern",
                            code=pattern.evidence_code,
                            points=points,
                            polarity="bullish",
                            reason=f"Pattern confirmation: {pattern.pattern_type}.",
                            source_refs=(pattern.source_data_reference,),
                        )
                    )
                elif pattern.direction == "bearish":
                    sell_points = min(100, sell_points + points)
                    positive.append(
                        SignalEvidence(
                            component="pattern",
                            code=pattern.evidence_code,
                            points=points,
                            polarity="bearish",
                            reason=f"Pattern confirmation: {pattern.pattern_type}.",
                            source_refs=(pattern.source_data_reference,),
                        )
                    )
            elif pattern.relation == "conflict":
                penalty = self.config.weights.pattern_conflict_penalty
                if pattern.direction == "bearish" and dominant_before == "BUY":
                    buy_points = max(0, buy_points - penalty)
                    negative.append(
                        SignalEvidence(
                            component="pattern",
                            code=pattern.evidence_code,
                            points=penalty,
                            polarity="bearish",
                            reason="Bearish reversal pattern detected near resistance context.",
                            source_refs=(pattern.source_data_reference,),
                        )
                    )
                elif pattern.direction == "bullish" and dominant_before == "SELL":
                    sell_points = max(0, sell_points - penalty)
                    negative.append(
                        SignalEvidence(
                            component="pattern",
                            code=pattern.evidence_code,
                            points=penalty,
                            polarity="bullish",
                            reason="Bullish reversal pattern detected near support context.",
                            source_refs=(pattern.source_data_reference,),
                        )
                    )
            source_refs.append(pattern.source_data_reference)

        return _Assessment(
            buy_points=min(100, buy_points),
            sell_points=min(100, sell_points),
            positive_evidence=tuple(positive),
            negative_evidence=tuple(negative),
            patterns=tuple(patterns),
            source_refs=tuple(source_refs),
            occurrence_time=latest_pivot.occurrence_time,
            confirmation_time=latest_pivot.confirmation_time,
            engine_version_ref=(
                matrix.resolutions[0].latest_transition.config_version
                if matrix.resolutions and matrix.resolutions[0].latest_transition is not None
                else "unknown"
            ),
        )

    def _resolve_action_and_score(
        self, buy_points: int, sell_points: int
    ) -> tuple[SignalAction, int]:
        if buy_points == 0 and sell_points == 0:
            return ("WAIT", 0)
        dominant = max(buy_points, sell_points)
        conflict = min(buy_points, sell_points)
        score = max(0, min(100, dominant - conflict))
        if abs(buy_points - sell_points) < self.config.action_gap_min:
            return ("WAIT", score)
        if score <= self.config.wait_score_max or score < self.config.action_score_min:
            return ("WAIT", score)
        return ("BUY", score) if buy_points > sell_points else ("SELL", score)

    def _build_trade_setup(
        self,
        *,
        action: SignalAction,
        structure: StructureSnapshot,
        matrix: MatrixSnapshot,
    ) -> tuple[PriceRange, Decimal, str, tuple[SignalTarget, ...], Decimal] | None:
        transition = _latest_transition(matrix)
        if transition is None:
            return None
        reference = transition.to_price
        half = transition.effective_box_size * self.config.entry_zone_half_width
        entry_zone = PriceRange(
            low=reference - half,
            high=reference + half,
            reason=f"Derived from {transition.identity_key} and effective box size.",
        )
        support = _latest_level(structure.levels, "support")
        resistance = _latest_level(structure.levels, "resistance")
        if action == "BUY":
            invalidation = support.price if support is not None else _latest_low_pivot(structure)
            if invalidation is None or invalidation >= entry_zone.low:
                return None
            risk = entry_zone.low - invalidation
            tp1 = reference + (risk * self.config.target_rr_tp1)
            tp2 = reference + (risk * self.config.target_rr_tp2)
            reward = tp2 - reference
        else:
            invalidation = (
                resistance.price if resistance is not None else _latest_high_pivot(structure)
            )
            if invalidation is None or invalidation <= entry_zone.high:
                return None
            risk = invalidation - entry_zone.high
            tp1 = reference - (risk * self.config.target_rr_tp1)
            tp2 = reference - (risk * self.config.target_rr_tp2)
            reward = reference - tp2
        if risk <= 0:
            return None
        rr = reward / risk
        targets = (
            SignalTarget(name="TP1", price=tp1, method="risk_reward"),
            SignalTarget(name="TP2", price=tp2, method="risk_reward"),
        )
        return (
            entry_zone,
            invalidation,
            "Derived from nearest confirmed structure level.",
            targets,
            rr,
        )

    def _future_conditions(
        self, *, regime: RegimeSnapshot, matrix: MatrixSnapshot
    ) -> tuple[str, ...]:
        conditions: list[str] = []
        if matrix.alignment == "mixed":
            conditions.append(
                "Additional matrix alignment would strengthen directional confidence."
            )
        if regime.state.label == "range":
            conditions.append("Breakout confirmation from range could strengthen setup.")
        if regime.state.label == "high_volatility":
            conditions.append("Volatility normalization would reduce conflict risk.")
        if not conditions:
            conditions.append("Recompute score on the next confirmed structure update.")
        return tuple(conditions)

    def _wait_decision(
        self,
        *,
        reason_code: str,
        reason_text: str,
        source_refs: tuple[str, ...],
    ) -> SignalDecision:
        evidence = SignalEvidence(
            component="pattern",
            code=reason_code,
            points=0,
            polarity="neutral",
            reason=reason_text,
            source_refs=source_refs,
        )
        return SignalDecision(
            action="WAIT",
            score=0,
            entry_zone=None,
            invalidation_price=None,
            invalidation_reason=None,
            targets=(),
            risk_reward=None,
            patterns=(),
            positive_evidence=(),
            negative_evidence=(evidence,),
            future_conditions=("Await confirmed market structure evidence.",),
            config_version=self.config.version,
            engine_version=f"{self.config.version}:p8b-v2",
            source_refs=source_refs,
        )

    def _pnf_evidence(self, matrix: MatrixSnapshot) -> tuple[SignalAction, str, str, str]:
        transition = _latest_transition(matrix)
        if transition is None:
            return ("WAIT", "P&F transition unavailable.", "pnf_unavailable", "pnf:none")
        if transition.type == "reversal" and transition.direction == "X":
            return (
                "BUY",
                "P&F reversal O→X confirmed.",
                "pnf_reversal_bullish",
                transition.identity_key,
            )
        if transition.type == "reversal" and transition.direction == "O":
            return (
                "SELL",
                "P&F reversal X→O confirmed.",
                "pnf_reversal_bearish",
                transition.identity_key,
            )
        if transition.direction == "X":
            return (
                "BUY",
                "P&F extension remains bullish.",
                "pnf_extension_bullish",
                transition.identity_key,
            )
        if transition.direction == "O":
            return (
                "SELL",
                "P&F extension remains bearish.",
                "pnf_extension_bearish",
                transition.identity_key,
            )
        return ("WAIT", "P&F evidence is neutral.", "pnf_neutral", transition.identity_key)

    def _structure_evidence(
        self, structure: StructureSnapshot
    ) -> tuple[SignalAction, str, str, str]:
        if len(structure.pivots) < 2:
            return (
                "WAIT",
                "Insufficient confirmed pivots for structure trend.",
                "structure_warmup",
                "pivot:none",
            )

        if len(structure.pivots) >= 3:
            first, second, third = structure.pivots[-3], structure.pivots[-2], structure.pivots[-1]
            if (
                first.kind == "low"
                and second.kind == "high"
                and third.kind == "low"
                and abs(first.price - third.price) <= self.config.pattern_price_tolerance
            ):
                return (
                    "BUY",
                    "Structure confirms a bullish double-bottom pattern.",
                    "structure_double_bottom",
                    third.source_transition_id,
                )
            if (
                first.kind == "high"
                and second.kind == "low"
                and third.kind == "high"
                and abs(first.price - third.price) <= self.config.pattern_price_tolerance
            ):
                return (
                    "SELL",
                    "Structure confirms a bearish double-top pattern.",
                    "structure_double_top",
                    third.source_transition_id,
                )
            if (
                first.kind == "low"
                and second.kind == "high"
                and third.kind == "low"
                and third.price > first.price
            ):
                return (
                    "BUY",
                    "Structure shows higher low support after a pivot trough.",
                    "structure_higher_low",
                    third.source_transition_id,
                )
            if (
                first.kind == "high"
                and second.kind == "low"
                and third.kind == "high"
                and third.price < first.price
            ):
                return (
                    "SELL",
                    "Structure shows lower high resistance after a pivot peak.",
                    "structure_lower_high",
                    third.source_transition_id,
                )

        left = structure.pivots[-2]
        right = structure.pivots[-1]
        if left.kind == "low" and right.kind == "high" and right.price > left.price:
            return (
                "BUY",
                "Structure shows HL→HH progression.",
                "structure_hl_hh",
                right.source_transition_id,
            )
        if left.kind == "high" and right.kind == "low" and right.price < left.price:
            return (
                "SELL",
                "Structure shows LH→LL progression.",
                "structure_lh_ll",
                right.source_transition_id,
            )
        if left.kind == "low" and right.kind == "low" and right.price < left.price:
            return (
                "SELL",
                "Latest confirmed structure continues to a lower low.",
                "structure_lower_low",
                right.source_transition_id,
            )
        if left.kind == "high" and right.kind == "high" and right.price > left.price:
            return (
                "BUY",
                "Latest confirmed structure continues to a higher high.",
                "structure_higher_high",
                right.source_transition_id,
            )
        if right.kind == "high":
            return (
                "BUY",
                "Latest confirmed structure pivot is a high.",
                "structure_recent_high",
                right.source_transition_id,
            )
        return (
            "SELL",
            "Latest confirmed structure pivot is a low.",
            "structure_recent_low",
            right.source_transition_id,
        )

    def _support_resistance_evidence(
        self,
        *,
        structure: StructureSnapshot,
        matrix: MatrixSnapshot,
    ) -> tuple[SignalAction, str, str, str]:
        transition = _latest_transition(matrix)
        if transition is None:
            return ("WAIT", "S/R context unavailable.", "sr_unavailable", "level:none")
        support = _latest_level(structure.levels, "support")
        resistance = _latest_level(structure.levels, "resistance")
        if support is not None and transition.to_price <= support.price:
            return (
                "BUY",
                "Price is near confirmed support.",
                "sr_near_support",
                support.source_pivot_id or "support:none",
            )
        if resistance is not None and transition.to_price >= resistance.price:
            return (
                "SELL",
                "Price is near confirmed resistance.",
                "sr_near_resistance",
                resistance.source_pivot_id or "resistance:none",
            )
        if transition.direction == "X":
            return ("BUY", "Price holds above support context.", "sr_support_context", "sr:context")
        if transition.direction == "O":
            return (
                "SELL",
                "Price holds below resistance context.",
                "sr_resistance_context",
                "sr:context",
            )
        return ("WAIT", "S/R context neutral.", "sr_neutral", "sr:neutral")

    def _matrix_evidence(
        self, matrix: MatrixSnapshot
    ) -> tuple[SignalAction, str, str, tuple[str, ...]]:
        refs = tuple(
            state.latest_transition.identity_key
            if state.latest_transition is not None
            else f"matrix:{state.name}:none"
            for state in matrix.resolutions
        )
        if matrix.alignment == "aligned_bullish":
            return ("BUY", "Fast/Medium/Slow matrix aligned bullish.", "matrix_bullish", refs)
        if matrix.alignment == "aligned_bearish":
            return ("SELL", "Fast/Medium/Slow matrix aligned bearish.", "matrix_bearish", refs)
        if matrix.alignment == "mixed":
            return ("WAIT", "Matrix disagreement detected.", "matrix_mixed", refs)
        return ("WAIT", "Matrix unavailable.", "matrix_unavailable", refs)

    def _regime_evidence(
        self,
        *,
        regime: RegimeSnapshot,
        matrix_side: SignalAction,
    ) -> tuple[SignalAction, str, str]:
        if regime.state.label == "trend" and matrix_side in {"BUY", "SELL"}:
            direction = "bullish" if matrix_side == "BUY" else "bearish"
            return (
                matrix_side,
                f"Trend regime supports {direction} continuation.",
                "regime_trend_support",
            )
        if regime.state.label == "range":
            return ("WAIT", "Range regime requires breakout confirmation.", "regime_range_caution")
        if regime.state.label == "high_volatility":
            return (
                "WAIT",
                "High volatility regime requires stricter confirmation.",
                "regime_volatility_caution",
            )
        return ("WAIT", "Regime evidence unavailable.", "regime_unknown")

    def _patterns(self, structure: StructureSnapshot) -> tuple[PatternEvidence, ...]:
        if len(structure.pivots) < 3:
            return ()
        first = structure.pivots[-3]
        second = structure.pivots[-2]
        third = structure.pivots[-1]
        patterns: list[PatternEvidence] = []
        if (
            first.kind == "low"
            and second.kind == "high"
            and third.kind == "low"
            and abs(first.price - third.price) <= self.config.pattern_price_tolerance
        ):
            patterns.append(
                PatternEvidence(
                    pattern_type="double_bottom",
                    direction="bullish",
                    start_time=first.occurrence_time,
                    confirmation_time=third.confirmation_time,
                    price_low=min(first.price, third.price),
                    price_high=second.price,
                    evidence_code="pattern_double_bottom",
                    source_data_reference=third.source_transition_id,
                    algorithm_version="p8a-pattern-v1",
                    relation="confirmation",
                )
            )
        if (
            first.kind == "high"
            and second.kind == "low"
            and third.kind == "high"
            and abs(first.price - third.price) <= self.config.pattern_price_tolerance
        ):
            patterns.append(
                PatternEvidence(
                    pattern_type="double_top",
                    direction="bearish",
                    start_time=first.occurrence_time,
                    confirmation_time=third.confirmation_time,
                    price_low=second.price,
                    price_high=max(first.price, third.price),
                    evidence_code="pattern_double_top",
                    source_data_reference=third.source_transition_id,
                    algorithm_version="p8a-pattern-v1",
                    relation="confirmation",
                )
            )
        # A three-pivot high/low/high shape is not head and shoulders.
        # Require five alternating pivots and a sixth confirmed neckline break.
        if len(structure.pivots) >= 6:
            window = structure.pivots[-6:]
            a, b, c, d, e, f = window
            kinds = tuple(p.kind for p in window)
            bearish = kinds == ("high", "low", "high", "low", "high", "low")
            bullish = kinds == ("low", "high", "low", "high", "low", "high")
            tolerance = self.config.pattern_price_tolerance
            name = None
            if bearish and c.price > max(a.price, e.price) + tolerance:
                if abs(a.price - e.price) <= tolerance and f.price < min(b.price, d.price):
                    name = "head_and_shoulders"
            if bullish and c.price < min(a.price, e.price) - tolerance:
                if abs(a.price - e.price) <= tolerance and f.price > max(b.price, d.price):
                    name = "inverse_head_and_shoulders"
            if name is None and bearish:
                if a.price > c.price > e.price and b.price < d.price and f.price < b.price:
                    name = "triangle_breakdown"
            if name is None and bullish:
                if a.price < c.price < e.price and b.price > d.price and f.price > b.price:
                    name = "triangle_breakout"
            if name is not None:
                patterns.append(
                    PatternEvidence(
                        pattern_type=name,
                        direction="bearish" if bearish else "bullish",
                        start_time=a.occurrence_time,
                        confirmation_time=max(p.confirmation_time for p in window),
                        price_low=min(p.price for p in window),
                        price_high=max(p.price for p in window),
                        evidence_code=f"pattern_{name}",
                        source_data_reference="|".join(p.source_transition_id for p in window),
                        algorithm_version="p8b-pattern-v2",
                        relation="confirmation",
                    )
                )
        if len(structure.pivots) >= 4:
            a, b, c, d = structure.pivots[-4:]
            name = None
            if (a.kind, b.kind, c.kind, d.kind) == ("high", "low", "high", "low"):
                if c.price > a.price and d.price < b.price:
                    name = "failed_breakout"
            if (a.kind, b.kind, c.kind, d.kind) == ("low", "high", "low", "high"):
                if c.price < a.price and d.price > b.price:
                    name = "failed_breakdown"
            if name is not None:
                patterns.append(
                    PatternEvidence(
                        pattern_type=name,
                        direction="bearish" if name == "failed_breakout" else "bullish",
                        start_time=a.occurrence_time,
                        confirmation_time=max(p.confirmation_time for p in (a, b, c, d)),
                        price_low=min(p.price for p in (a, b, c, d)),
                        price_high=max(p.price for p in (a, b, c, d)),
                        evidence_code=f"pattern_{name}",
                        source_data_reference="|".join(
                            p.source_transition_id for p in (a, b, c, d)
                        ),
                        algorithm_version="p8b-pattern-v2",
                        relation="confirmation",
                    )
                )
        return tuple(patterns)

    @staticmethod
    def _is_duplicate(previous: ResearchSignal, current: ResearchSignal) -> bool:
        return (
            previous.side == current.side
            and previous.source_refs == current.source_refs
            and previous.reason_codes == current.reason_codes
            and previous.status == "active"
        )


@dataclass(frozen=True, slots=True)
class _Assessment:
    buy_points: int
    sell_points: int
    positive_evidence: tuple[SignalEvidence, ...]
    negative_evidence: tuple[SignalEvidence, ...]
    patterns: tuple[PatternEvidence, ...]
    source_refs: tuple[str, ...]
    occurrence_time: datetime
    confirmation_time: datetime
    engine_version_ref: str


def _latest_transition(matrix: MatrixSnapshot) -> PnfTransition | None:
    candidates = [
        state.latest_transition
        for state in matrix.resolutions
        if state.latest_transition is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.event_time)


def _latest_level(levels: tuple[CandidateLevel, ...], side: str) -> CandidateLevel | None:
    confirmed = [level for level in levels if level.side == side and level.status == "confirmed"]
    if not confirmed:
        return None
    return confirmed[-1]


def _latest_low_pivot(structure: StructureSnapshot) -> Decimal | None:
    lows = [pivot.price for pivot in structure.pivots if pivot.kind == "low"]
    return lows[-1] if lows else None


def _latest_high_pivot(structure: StructureSnapshot) -> Decimal | None:
    highs = [pivot.price for pivot in structure.pivots if pivot.kind == "high"]
    return highs[-1] if highs else None


def _dedupe_refs(source_refs: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    for item in source_refs:
        if item not in ordered:
            ordered.append(item)
    return tuple(ordered)
