"""Edge-validation engineering components (EDGE-VALIDATION-1, Issue #77).

Research/backtest only. Reuses `nexora.backtest` hashing and dataset checks and adds:
dataset provenance + quality audit, chronological splits, cost accounting with explicit
unknown states, and a separate SL/TP-aware exit mode. Nothing here freezes the P&F
baseline or evaluates real-market edge.
"""

from nexora.edge.costs import (
    KNOWN_ZERO,
    UNAVAILABLE,
    UNKNOWN,
    CostBreakdown,
    CostComponent,
    CostInputError,
    CostModel,
    compute_costs,
)
from nexora.edge.dataset import (
    EdgeDatasetError,
    EdgeDatasetManifest,
    Provenance,
    QualityReport,
    audit_events,
    build_edge_manifest,
    verify_edge_dataset,
)
from nexora.edge.exits import (
    EdgeExitRun,
    EdgeTrade,
    ExitInputError,
    ExitPolicy,
    ExitResult,
    TradePlan,
    plan_from_signal,
    run_exit_mode,
    simulate_exit,
    to_edge_trade,
)
from nexora.edge.splits import (
    HoldoutLocked,
    HoldoutUnlock,
    SplitError,
    SplitPlan,
    WalkForwardWindow,
    select_segment,
    walk_forward_windows,
)

__all__ = [
    "KNOWN_ZERO",
    "UNAVAILABLE",
    "UNKNOWN",
    "CostBreakdown",
    "CostComponent",
    "CostInputError",
    "CostModel",
    "EdgeDatasetError",
    "EdgeDatasetManifest",
    "EdgeExitRun",
    "EdgeTrade",
    "ExitInputError",
    "ExitPolicy",
    "ExitResult",
    "HoldoutLocked",
    "HoldoutUnlock",
    "Provenance",
    "QualityReport",
    "SplitError",
    "SplitPlan",
    "TradePlan",
    "WalkForwardWindow",
    "audit_events",
    "build_edge_manifest",
    "compute_costs",
    "plan_from_signal",
    "run_exit_mode",
    "select_segment",
    "simulate_exit",
    "to_edge_trade",
    "verify_edge_dataset",
    "walk_forward_windows",
]
