"""M30 Next Candle Bias V1 pure core (ADR-026 Phase 2A).

Research/analytical output only: never a decision input, trade permission or probability.
No runtime wiring, persistence, lifecycle or configuration lives here.
"""

from nexora.m30_bias.candles import M30Candle, Sample, bucket_end, bucket_start, build_candle
from nexora.m30_bias.evaluate import evaluate_outcome, excursions, first_touch, threshold_label
from nexora.m30_bias.freeze import (
    AlgorithmVerdict,
    CommittedRow,
    ContextCandle,
    FreezeContext,
    M30BiasAlgorithm,
    M30BiasCore,
    M30Emission,
    ThresholdPolicy,
    recompute,
)
from nexora.m30_bias.models import (
    AlgorithmIdentity,
    FeedIdentity,
    M30BiasEvidence,
    M30BiasOutcome,
    M30BiasPrediction,
    M30ConflictError,
    M30IdentityError,
    M30IdentityUnavailable,
    PolicySpec,
    candle_id,
    candle_id_bytes,
    feed_identity,
    outcome_id,
    prediction_id,
    record_bytes,
    resolve_write,
)

__all__ = [
    "AlgorithmIdentity",
    "AlgorithmVerdict",
    "CommittedRow",
    "ContextCandle",
    "FeedIdentity",
    "FreezeContext",
    "M30BiasAlgorithm",
    "M30BiasCore",
    "M30BiasEvidence",
    "M30BiasOutcome",
    "M30BiasPrediction",
    "M30Candle",
    "M30ConflictError",
    "M30Emission",
    "M30IdentityError",
    "M30IdentityUnavailable",
    "PolicySpec",
    "Sample",
    "ThresholdPolicy",
    "bucket_end",
    "bucket_start",
    "build_candle",
    "candle_id",
    "candle_id_bytes",
    "evaluate_outcome",
    "excursions",
    "feed_identity",
    "first_touch",
    "outcome_id",
    "prediction_id",
    "recompute",
    "record_bytes",
    "resolve_write",
    "threshold_label",
]
