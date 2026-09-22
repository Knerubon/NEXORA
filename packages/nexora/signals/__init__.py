"""Explainable research signal contracts and engine."""

from nexora.signals.decision_context import (
    Bias,
    DecisionAlignment,
    DecisionContext,
    DecisionState,
    derive_decision_context,
)
from nexora.signals.engine import SignalEngine
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
    SignalWeights,
)
from nexora.signals.repository import SignalSnapshotStore

__all__ = [
    "Bias",
    "DecisionAlignment",
    "DecisionContext",
    "DecisionState",
    "PatternEvidence",
    "PriceRange",
    "ResearchSignal",
    "SignalAction",
    "SignalConfig",
    "SignalDecision",
    "SignalEngine",
    "SignalEvidence",
    "SignalSide",
    "SignalSnapshot",
    "SignalSnapshotStore",
    "SignalTarget",
    "SignalWeights",
    "derive_decision_context",
]
