"""Event-driven prediction freeze over committed rows (ADR-026 Decisions 3, 4, 8, 12A).

Pure state machine: no I/O, no wall clock, no environment, no persistence. Given the same
committed rows and identity config it emits byte-identical records, which is what
freeze-before-write crash recovery relies on (recompute from committed rows).
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from nexora.m30_bias.candles import (
    M30Candle,
    Sample,
    bucket_start,
    build_candle,
    require_utc,
    seconds,
)
from nexora.m30_bias.evaluate import evaluate_outcome
from nexora.m30_bias.models import (
    BIAS_VALUES,
    BUCKET,
    POLICY_VERSION,
    SUPPORTED_TIME_CONTRACTS,
    AlgorithmIdentity,
    FeedIdentity,
    M30BiasEvidence,
    M30BiasOutcome,
    M30BiasPrediction,
    M30BiasRecordStatus,
    M30BiasValue,
    M30IdentityError,
    M30IdentityUnavailable,
    PolicySpec,
    candle_id,
    canonical_json_bytes,
    content_value,
    feed_identity,
    prediction_id,
    sha256_hex,
    validate_freeze_lead,
)

if TYPE_CHECKING:
    from nexora.market_data.models import NormalizedPriceEvent

M30Emission = M30BiasPrediction | M30BiasOutcome | M30IdentityUnavailable


@dataclass(frozen=True, slots=True)
class CommittedRow:
    """One committed research-journal row: the event and its recorded pipeline output.

    ``output`` is the committed payload (``row["output"]``); it is never recomputed and
    must not be mutated by the caller after it is committed.
    """

    event: NormalizedPriceEvent
    output: Mapping[str, Any]
    completeness: str


@dataclass(frozen=True, slots=True)
class ContextCandle:
    candle: M30Candle
    closed: bool  # bucket_end <= cutoff_time; otherwise a partial candle cut at the cutoff


@dataclass(frozen=True, slots=True)
class FreezeContext:
    """Everything an algorithm may read: built only from the information set ``I_k`` (N2)."""

    target_start: datetime
    target_end: datetime
    cutoff_time: datetime
    snapshot_event_time: datetime
    snapshot_received_at: datetime
    snapshot_event_identity: str
    snapshot_output: dict[str, Any]  # detached copy of the committed output of the last I_k row
    reference_price: Decimal
    candles: tuple[ContextCandle, ...]


@dataclass(frozen=True, slots=True)
class AlgorithmVerdict:
    bias: M30BiasValue
    evidence: tuple[M30BiasEvidence, ...]
    reason_codes: tuple[str, ...] = ()


class M30BiasAlgorithm(Protocol):
    """A Quant-approved algorithm (Decision 10). None is shipped in Phase 2A."""

    @property
    def algorithm_id(self) -> str: ...
    @property
    def algorithm_version(self) -> str: ...
    @property
    def params(self) -> Mapping[str, Any]: ...
    @property
    def required_history(self) -> int: ...
    def evaluate(self, context: FreezeContext) -> AlgorithmVerdict: ...


class ThresholdPolicy(Protocol):
    """A Quant-approved θ policy (Q-M3). None is shipped in Phase 2A."""

    @property
    def policy_id(self) -> str: ...
    @property
    def params(self) -> Mapping[str, Any]: ...
    def compute(self, context: FreezeContext) -> Decimal | None: ...


@dataclass(slots=True)
class _Last:
    sample: Sample
    output: Mapping[str, Any]


@dataclass(slots=True)
class _Pending:
    prediction: M30BiasPrediction
    window: list[Sample]


class M30BiasCore:
    """Deterministic freeze/evaluate state machine for one feed and one ``algorithm_key``.

    Unresolved Quant inputs have no defaults: ``freeze_lead_seconds`` (Q-M4) and
    ``threshold_policy`` (Q-M3, ``None`` = undecided) are required keyword arguments, and
    ``eligibility_policy`` accepts only the frozen structural rule (Q-M8 undecided).
    """

    def __init__(
        self,
        *,
        time_contract: str,
        algorithm: M30BiasAlgorithm,
        threshold_policy: ThresholdPolicy | None,
        freeze_lead_seconds: int,
        eligibility_policy: str,
        evidence_source: str,
    ) -> None:
        if time_contract not in SUPPORTED_TIME_CONTRACTS:
            raise M30IdentityError("candle_identity_unavailable", "unsupported_time_contract")
        history = algorithm.required_history
        if type(history) is not int or history < 0:
            raise M30IdentityError("algorithm_identity_invalid", "invalid_required_history")
        self._delta = timedelta(seconds=validate_freeze_lead(freeze_lead_seconds))
        self._freeze_lead_seconds = freeze_lead_seconds
        self._time_contract = time_contract
        self._algorithm = algorithm
        self._threshold_policy = threshold_policy
        self._evidence_source = evidence_source
        self.algorithm_key = AlgorithmIdentity(
            algorithm_id=algorithm.algorithm_id,
            algorithm_version=algorithm.algorithm_version,
            algorithm_params=algorithm.params,
            freeze_lead_seconds=freeze_lead_seconds,
            threshold_policy=(
                PolicySpec(threshold_policy.policy_id, threshold_policy.params)
                if threshold_policy is not None
                else None
            ),
            eligibility_policy=eligibility_policy,
            evidence_source=evidence_source,
        ).key()
        self._history_size = history
        self._feed: FeedIdentity | None = None
        self._halted: M30IdentityUnavailable | None = None
        self._last: _Last | None = None
        self._current: list[Sample] = []
        self._history: deque[M30Candle] = deque(maxlen=history)
        self._pending: dict[datetime, _Pending] = {}

    @property
    def health(self) -> str:
        return "unavailable" if self._halted is not None else "ready"

    @property
    def halted(self) -> M30IdentityUnavailable | None:
        return self._halted

    def process(self, row: CommittedRow) -> tuple[M30Emission, ...]:
        """Advance by one committed row; returns records frozen/evaluated by this row."""
        if self._halted is not None:
            return ()
        event = row.event
        sample = Sample.from_event(event, row.completeness)
        if sample.received_at < sample.event_time or not sample.price.is_finite():
            raise ValueError("invalid_event")
        if sample.price <= 0:
            raise ValueError("invalid_event")
        if self._last is not None:
            if sample.identity == self._last.sample.identity:
                raise ValueError("duplicate_event")
            if sample.event_time < self._last.sample.event_time:
                raise ValueError("out_of_order_event")
        try:
            feed = feed_identity(self._time_contract, event)
        except M30IdentityError as exc:
            return self._halt((exc.code, exc.detail), sample.identity)
        if self._feed is None:
            self._feed = feed
        elif feed != self._feed:
            return self._halt(
                ("candle_identity_unavailable", "feed_identity_changed"), sample.identity
            )

        emitted: list[M30Emission] = []
        if self._last is not None:
            emitted.extend(self._freeze_crossed(self._last, sample, event))
        for start in sorted(self._pending):
            pending = self._pending[start]
            if sample.event_time >= pending.prediction.target_end:
                emitted.append(evaluate_outcome(pending.prediction, pending.window, sample))
                del self._pending[start]
        for start, pending in self._pending.items():
            if start <= sample.event_time < pending.prediction.target_end:
                pending.window.append(sample)
        if self._current and bucket_start(sample.event_time) != bucket_start(
            self._current[0].event_time
        ):
            self._history.append(build_candle(tuple(self._current)))
            self._current = []
        self._current.append(sample)
        self._last = _Last(sample, row.output)
        return tuple(emitted)

    def _halt(self, reasons: tuple[str, str], identity: str) -> tuple[M30Emission, ...]:
        # Fail closed: no record can be keyed; pending outcomes are not written.
        self._halted = M30IdentityUnavailable(reason_codes=reasons, event_identity=identity)
        self._pending.clear()
        return (self._halted,)

    def _freeze_crossed(
        self, last: _Last, trigger: Sample, event: NormalizedPriceEvent
    ) -> list[M30BiasPrediction]:
        """Targets whose cutoff ``C_k = B_k - Δ`` satisfies ``L < C_k <= F`` (Decision 3)."""
        latest_info = last.sample.event_time
        first = bucket_start(latest_info + self._delta) + BUCKET
        final = bucket_start(trigger.event_time + self._delta)
        if first > final:
            return []
        records: list[M30BiasPrediction] = []
        eligible = latest_info >= first - BUCKET
        if eligible:
            records.append(self._record(first, "FROZEN", last, trigger, event))
        if final != first or not eligible:
            # Feed was not alive in the 30 minutes before the cutoff: one SKIPPED record
            # for the latest crossed target, nothing for the empty buckets in between.
            records.append(self._record(final, "SKIPPED", last, trigger, event))
        for record in records:
            self._pending[record.target_start] = _Pending(record, [])
        return records

    def _record(
        self,
        target: datetime,
        status: M30BiasRecordStatus,
        last: _Last,
        trigger: Sample,
        event: NormalizedPriceEvent,
    ) -> M30BiasPrediction:
        assert self._feed is not None
        cutoff = target - self._delta
        snapshot_bytes = canonical_json_bytes(content_value(last.output))
        output = dict(json.loads(snapshot_bytes))
        signals = output.get("signals")
        decision = signals.get("decision") if isinstance(signals, dict) else None
        engine_version = decision.get("engine_version") if isinstance(decision, dict) else None
        config_version = output.get("config_version")
        provenance: dict[str, str | None] = {
            "evidence_source": self._evidence_source,
            "pipeline_config_version": config_version if isinstance(config_version, str) else None,
            "signal_engine_version": engine_version if isinstance(engine_version, str) else None,
            "snapshot_output_sha256": sha256_hex(snapshot_bytes),
        }
        bias: M30BiasValue = "UNAVAILABLE"
        evidence: tuple[M30BiasEvidence, ...] = ()
        reasons: list[str] = []
        threshold: Decimal | None = None
        if status == "SKIPPED":
            reasons.append("discontinuous_feed")
        else:
            context = self._context(target, cutoff, last, output)
            bias, evidence, verdict_reasons = self._evaluate(context)
            reasons.extend(verdict_reasons)
            threshold = self._threshold(context)
        if threshold is None:
            reasons.append(
                "threshold_policy_undecided"
                if self._threshold_policy is None
                else "threshold_unavailable"
            )
        late = trigger.received_at > target
        candle = candle_id(self._feed, target)
        return M30BiasPrediction(
            schema_version=1,
            policy_version=POLICY_VERSION,
            prediction_id=prediction_id(self.algorithm_key, candle),
            candle_id=candle,
            algorithm_key=self.algorithm_key,
            source=event.source,
            symbol=event.symbol,
            price_source=event.price_source,
            units=event.units,
            target_start=target,
            target_end=target + BUCKET,
            freeze_lead_seconds=self._freeze_lead_seconds,
            cutoff_time=cutoff,
            prediction_time=trigger.received_at,
            snapshot_event_time=last.sample.event_time,
            snapshot_received_at=last.sample.received_at,
            snapshot_event_identity=last.sample.identity,
            freeze_event_identity=trigger.identity,
            freeze_after_target_open=late,
            freeze_lag_seconds=seconds(trigger.received_at - target) if late else Decimal(0),
            status=status,
            bias=bias,
            reason_codes=tuple(reasons),
            reference_price=last.sample.price,
            threshold=threshold,
            evidence=evidence,
            input_provenance=provenance,
        )

    def _context(
        self, target: datetime, cutoff: datetime, last: _Last, output: dict[str, Any]
    ) -> FreezeContext:
        candles = [*self._history, build_candle(tuple(self._current))]
        if self._history_size:
            candles = candles[-self._history_size :]
        else:
            candles = []
        return FreezeContext(
            target_start=target,
            target_end=target + BUCKET,
            cutoff_time=cutoff,
            snapshot_event_time=last.sample.event_time,
            snapshot_received_at=last.sample.received_at,
            snapshot_event_identity=last.sample.identity,
            snapshot_output=output,
            reference_price=last.sample.price,
            candles=tuple(ContextCandle(c, c.bucket_end <= cutoff) for c in candles),
        )

    def _evaluate(
        self, context: FreezeContext
    ) -> tuple[M30BiasValue, tuple[M30BiasEvidence, ...], tuple[str, ...]]:
        try:
            verdict = self._algorithm.evaluate(context)
        except Exception:
            return "UNAVAILABLE", (), ("algorithm_failed",)
        if not _valid_verdict(verdict):
            return "UNAVAILABLE", (), ("algorithm_invalid_output",)
        late = tuple(
            f"evidence_after_cutoff:{item.component}:{item.code}"
            for item in verdict.evidence
            if require_utc(item.as_of_event_time) >= context.cutoff_time
            or require_utc(item.as_of_received_at) > context.snapshot_received_at
        )
        if late:
            # N1: never a silent drop; the record becomes UNAVAILABLE and says why.
            return "UNAVAILABLE", (), ("evidence_after_cutoff", *late)
        return verdict.bias, verdict.evidence, verdict.reason_codes

    def _threshold(self, context: FreezeContext) -> Decimal | None:
        if self._threshold_policy is None:
            return None
        try:
            value = self._threshold_policy.compute(context)
        except Exception:
            return None
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            return None
        return value


def _valid_verdict(verdict: object) -> bool:
    if not isinstance(verdict, AlgorithmVerdict) or verdict.bias not in BIAS_VALUES:
        return False
    if not isinstance(verdict.evidence, tuple) or not isinstance(verdict.reason_codes, tuple):
        return False
    if not all(isinstance(code, str) and code for code in verdict.reason_codes):
        return False
    for item in verdict.evidence:
        if not isinstance(item, M30BiasEvidence):
            return False
        if not item.component or not item.code or not item.source_version:
            return False
        if type(item.polarity) is not int or item.polarity not in (-1, 0, 1):
            return False
        if item.value is not None and not isinstance(item.value, str):
            return False
        for moment in (item.as_of_event_time, item.as_of_received_at):
            if not isinstance(moment, datetime) or moment.utcoffset() is None:
                return False
    return True


def recompute(
    core_factory: Callable[[], M30BiasCore], rows: Iterable[CommittedRow]
) -> tuple[M30Emission, ...]:
    """Freeze-before-write recovery primitive: rebuild emissions from committed rows only."""
    core = core_factory()
    emitted: list[M30Emission] = []
    for row in rows:
        emitted.extend(core.process(row))
    return tuple(emitted)
