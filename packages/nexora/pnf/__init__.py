"""Deterministic fixed-box Point & Figure engine contracts and implementation."""

from nexora.pnf.engine import PnfEngine
from nexora.pnf.fixtures import (
    exact_threshold_fixture,
    flat_fixture,
    monotonic_rise_fall_fixture,
    multi_box_gap_fixture,
    reversal_boundary_fixture,
)
from nexora.pnf.models import (
    ColumnDirection,
    PnfColumn,
    PnfConfig,
    PnfInputError,
    PnfSnapshot,
    PnfSymbolState,
    PnfTransition,
    PnfTransitionReason,
    PnfTransitionType,
)

__all__ = [
    "ColumnDirection",
    "PnfColumn",
    "PnfConfig",
    "PnfEngine",
    "PnfInputError",
    "PnfSnapshot",
    "PnfSymbolState",
    "PnfTransition",
    "PnfTransitionReason",
    "PnfTransitionType",
    "exact_threshold_fixture",
    "flat_fixture",
    "monotonic_rise_fall_fixture",
    "multi_box_gap_fixture",
    "reversal_boundary_fixture",
]
