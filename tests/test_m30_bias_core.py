"""ADR-026 Phase 2A: freeze semantics, no-look-ahead and crash recomputation."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from nexora.m30_bias import (
    AlgorithmVerdict,
    CommittedRow,
    FeedIdentity,
    FreezeContext,
    M30BiasCore,
    M30BiasEvidence,
    M30BiasOutcome,
    M30BiasPrediction,
    M30ConflictError,
    M30Emission,
    M30IdentityError,
    M30IdentityUnavailable,
    candle_id,
    recompute,
    record_bytes,
    resolve_write,
)
from nexora.m30_bias.models import M30BiasValue
from nexora.market_data.models import NormalizedPriceEvent

MT5_SOURCE = "MT5-quote-observation:time-offset=0"
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
DELAY = timedelta(milliseconds=100)


def ev(
    at: datetime,
    price: str,
    key: str,
    *,
    source: str = MT5_SOURCE,
    delay: timedelta = DELAY,
    sequence: int = 0,
    is_gap: bool = False,
) -> NormalizedPriceEvent:
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=key,
        source=source,
        symbol="XAUUSD",
        kind="tick",
        event_time=at,
        received_at=at + delay,
        source_sequence=sequence,
        source_order=0,
        source_event_id=key,
        price_source="bid",
        units="USD",
        precision=2,
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price) + Decimal("0.2"),
        is_gap=is_gap,
    )


def row(event: NormalizedPriceEvent, completeness: str = "unknown") -> CommittedRow:
    output = {
        "config_version": "pipeline-cfg-1",
        "signals": {"decision": {"engine_version": "signal-v1", "action": "WAIT"}},
        "marker": event.identity_key,
        "price": str(event.price),
    }
    return CommittedRow(event=event, output=output, completeness=completeness)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def scenario() -> list[CommittedRow]:
    """Δ = 0 scenario: target 10:30 frozen by the 10:30:00 event, closed at 11:00."""
    return [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(10), "100.40", "e2", sequence=2)),
        row(ev(at(30) - timedelta(microseconds=1), "100.20", "e3", sequence=3)),
        row(ev(at(30), "100.50", "e4", sequence=4)),  # F for target 10:30
        row(ev(at(40), "99.20", "e5", sequence=5)),
        row(ev(at(50), "101.30", "e6", sequence=6)),
        row(ev(at(59), "100.80", "e7", sequence=7)),
        row(ev(at(60), "100.90", "e8", sequence=8)),  # closes 10:30, freezes 11:00
        row(ev(at(75), "101.00", "e9", sequence=9)),
    ]


@dataclass
class FixtureAlgorithm:
    """Test-only algorithm exercising the contract; NOT a bias rule (ADR-026 Decision 10)."""

    algorithm_id: str = "fixture-alg"
    algorithm_version: str = "1"
    params: Mapping[str, Any] = field(default_factory=lambda: {"lookback": "1"})
    required_history: int = 2
    invert: bool = False
    seen: list[FreezeContext] = field(default_factory=list)

    def evaluate(self, context: FreezeContext) -> AlgorithmVerdict:
        self.seen.append(context)
        last = context.candles[-1].candle
        move = last.close - last.open
        bias: M30BiasValue = "UP" if move > 0 else "DOWN" if move < 0 else "NO_EDGE"
        if self.invert and bias != "NO_EDGE":
            bias = "DOWN" if bias == "UP" else "UP"
        evidence = (
            M30BiasEvidence(
                component="m30_candles",
                code="fixture_last_body",
                polarity=1 if move > 0 else -1 if move < 0 else 0,
                value=str(move),
                as_of_event_time=last.last_sample_time,
                as_of_received_at=context.snapshot_received_at,
                source_version="fixture-1",
            ),
            M30BiasEvidence(
                component="signal",
                code="fixture_snapshot_marker",
                polarity=0,
                value=str(context.snapshot_output["marker"]),
                as_of_event_time=context.snapshot_event_time,
                as_of_received_at=context.snapshot_received_at,
                source_version="signal-v1",
            ),
        )
        # Mutating the detached context must not affect any later record.
        context.snapshot_output["marker"] = "mutated"
        return AlgorithmVerdict(bias=bias, evidence=evidence)


@dataclass
class ExplicitThreshold:
    """Test-only explicit θ; no θ policy is shipped or defaulted (Q-M3)."""

    value: Decimal
    policy_id: str = "test-explicit"
    params: Mapping[str, Any] = field(default_factory=dict)

    def compute(self, context: FreezeContext) -> Decimal | None:
        return self.value


def core(
    algorithm: Any = None,
    *,
    delta: int = 0,
    threshold: Any = None,
    time_contract: str = "legacy-adr019",
) -> M30BiasCore:
    return M30BiasCore(
        time_contract=time_contract,
        algorithm=algorithm if algorithm is not None else FixtureAlgorithm(),
        threshold_policy=threshold,
        freeze_lead_seconds=delta,
        eligibility_policy="structural-v1",
        evidence_source="research:test-stream",
    )


def run(rows: list[CommittedRow], **kwargs: Any) -> list[M30Emission]:
    engine = core(**kwargs)
    out: list[M30Emission] = []
    for item in rows:
        out.extend(engine.process(item))
    return out


def predictions(items: list[M30Emission]) -> list[M30BiasPrediction]:
    return [i for i in items if isinstance(i, M30BiasPrediction)]


def outcomes(items: list[M30Emission]) -> list[M30BiasOutcome]:
    return [i for i in items if isinstance(i, M30BiasOutcome)]


def as_bytes(items: list[M30Emission]) -> list[bytes]:
    return [record_bytes(i) for i in items if not isinstance(i, M30IdentityUnavailable)]


# --- freeze point ----------------------------------------------------------------------


def test_freeze_point_fields_follow_decision_3() -> None:
    emitted = run(scenario())
    first = predictions(emitted)[0]
    assert first.status == "FROZEN"
    assert first.target_start == at(30) and first.target_end == at(60)
    assert first.cutoff_time == at(30)
    assert first.snapshot_event_identity == "e3"
    assert first.snapshot_event_time == at(30) - timedelta(microseconds=1)
    assert first.snapshot_received_at == first.snapshot_event_time + DELAY
    assert first.freeze_event_identity == "e4"
    assert first.prediction_time == at(30) + DELAY
    assert first.freeze_after_target_open is True
    assert first.freeze_lag_seconds == Decimal("0.1")
    assert first.reference_price == Decimal("100.20")
    # Last I_k candle 10:00 bucket: open 100.00, close 100.20 -> fixture says UP.
    assert first.bias == "UP"
    assert first.input_provenance["pipeline_config_version"] == "pipeline-cfg-1"
    assert first.input_provenance["signal_engine_version"] == "signal-v1"
    assert first.input_provenance["evidence_source"] == "research:test-stream"


def test_no_record_before_any_cutoff_is_crossed() -> None:
    assert run(scenario()[:3]) == []


def test_algorithm_sees_only_last_information_row_snapshot() -> None:
    algorithm = FixtureAlgorithm()
    rows = scenario()
    emitted = run(rows, algorithm=algorithm)
    context = algorithm.seen[0]
    assert context.snapshot_event_identity == "e3"
    assert all(c.candle.last_sample_time < context.cutoff_time for c in context.candles)
    assert all(c.closed for c in context.candles)
    assert "e3" in [e.value for e in predictions(emitted)[0].evidence]
    # The fixture mutated its detached context copy; committed payloads are untouched.
    assert rows[2].output["marker"] == "e3"


def test_threshold_without_policy_is_undecided_not_defaulted() -> None:
    first = predictions(run(scenario()))[0]
    assert first.threshold is None
    assert "threshold_policy_undecided" in first.reason_codes
    with pytest.raises(TypeError):
        M30BiasCore(  # type: ignore[call-arg]
            time_contract="legacy-adr019",
            algorithm=FixtureAlgorithm(),
            freeze_lead_seconds=0,
            eligibility_policy="structural-v1",
            evidence_source="research:x",
        )
    with pytest.raises(TypeError):
        M30BiasCore(  # type: ignore[call-arg]
            time_contract="legacy-adr019",
            algorithm=FixtureAlgorithm(),
            threshold_policy=None,
            eligibility_policy="structural-v1",
            evidence_source="research:x",
        )


def test_explicit_threshold_is_frozen_into_prediction() -> None:
    first = predictions(run(scenario(), threshold=ExplicitThreshold(Decimal("0.75"))))[0]
    assert first.threshold == Decimal("0.75")
    assert "threshold_policy_undecided" not in first.reason_codes


def test_invalid_threshold_value_is_unavailable() -> None:
    first = predictions(run(scenario(), threshold=ExplicitThreshold(Decimal("-1"))))[0]
    assert first.threshold is None and "threshold_unavailable" in first.reason_codes


def test_positive_lead_freezes_before_target_open() -> None:
    rows = [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(20), "100.40", "e2", sequence=2)),
        row(ev(at(25) - timedelta(seconds=1), "100.30", "e3", sequence=3)),
        row(ev(at(25), "100.10", "e4", sequence=4)),  # F: cutoff 10:25 for target 10:30
        row(ev(at(31), "100.90", "e5", sequence=5)),
        row(ev(at(60), "101.00", "e6", sequence=6)),
    ]
    emitted = run(rows, delta=300)
    first = predictions(emitted)[0]
    assert first.cutoff_time == at(25) and first.target_start == at(30)
    assert first.snapshot_event_identity == "e3"
    assert first.freeze_after_target_open is False and first.freeze_lag_seconds == 0
    outcome = outcomes(emitted)[0]
    assert outcome.sample_count == 1  # e4 precedes B_k: not in W_k
    assert outcome.pre_target_drift == Decimal("100.90") - Decimal("100.30")


def test_discontinuous_feed_freezes_first_target_and_skips_latest() -> None:
    rows = [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(10), "100.40", "e2", sequence=2)),
        row(ev(at(75), "100.70", "e3", sequence=3)),  # 11:15 after a gap
    ]
    emitted = run(rows)
    frozen, skipped = predictions(emitted)
    assert frozen.target_start == at(30) and frozen.status == "FROZEN"
    assert frozen.freeze_lag_seconds == Decimal(45 * 60) + Decimal("0.1")
    assert skipped.target_start == at(60) and skipped.status == "SKIPPED"
    assert skipped.bias == "UNAVAILABLE" and skipped.evidence == ()
    assert "discontinuous_feed" in skipped.reason_codes
    [no_data] = outcomes(emitted)  # target 10:30 closed by the same event, no samples
    assert no_data.prediction_id == frozen.prediction_id and no_data.status == "NO_DATA"


def test_lead_crossing_boundary_skips_when_feed_not_alive_in_window() -> None:
    rows = [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(27), "100.40", "e2", sequence=2)),  # after 10:25 cutoff of target 10:30
        row(ev(at(56), "100.70", "e3", sequence=3)),  # crosses 10:55 cutoff of target 11:00
    ]
    emitted = run(rows, delta=300)
    targets = [(p.target_start, p.status) for p in predictions(emitted)]
    assert targets == [(at(30), "FROZEN"), (at(60), "SKIPPED")]


# --- no-look-ahead ------------------------------------------------------------------


def _mutate_after(rows: list[CommittedRow], index: int) -> list[CommittedRow]:
    changed = rows[:index]
    for offset, item in enumerate(rows[index:]):
        price = str(Decimal("150") + offset)
        changed.append(row(dataclasses.replace(item.event, price=Decimal(price))))
    changed.append(row(ev(at(200), "1.00", "future", sequence=99)))
    return changed


def test_future_injection_after_cutoff_cannot_change_prediction() -> None:
    base = predictions(run(scenario()))[0]
    injected = predictions(run(_mutate_after(scenario(), 3)))[0]  # F (index 3) onward
    assert record_bytes(base) == record_bytes(injected)


def test_prefix_invariance() -> None:
    full = run(scenario())
    reference = record_bytes(predictions(full)[0])
    for end in range(4, len(scenario()) + 1):
        assert record_bytes(predictions(run(scenario()[:end]))[0]) == reference


def test_evidence_timestamps_precede_cutoff() -> None:
    for prediction in predictions(run(scenario())):
        for item in prediction.evidence:
            assert item.as_of_event_time < prediction.cutoff_time
            assert item.as_of_received_at <= prediction.snapshot_received_at


@dataclass
class LeakyAlgorithm(FixtureAlgorithm):
    def evaluate(self, context: FreezeContext) -> AlgorithmVerdict:
        leak = M30BiasEvidence(
            component="matrix",
            code="leak",
            polarity=1,
            value=None,
            as_of_event_time=context.cutoff_time,
            as_of_received_at=context.snapshot_received_at,
            source_version="x",
        )
        return AlgorithmVerdict(bias="UP", evidence=(leak,))


def test_evidence_at_or_after_cutoff_makes_record_unavailable() -> None:
    first = predictions(run(scenario(), algorithm=LeakyAlgorithm()))[0]
    assert first.bias == "UNAVAILABLE" and first.evidence == ()
    assert first.reason_codes[:2] == ("evidence_after_cutoff", "evidence_after_cutoff:matrix:leak")


@dataclass
class FailingAlgorithm(FixtureAlgorithm):
    def evaluate(self, context: FreezeContext) -> AlgorithmVerdict:
        raise RuntimeError("boom")


@dataclass
class InvalidAlgorithm(FixtureAlgorithm):
    def evaluate(self, context: FreezeContext) -> AlgorithmVerdict:
        return AlgorithmVerdict(bias="MAYBE", evidence=())  # type: ignore[arg-type]


def test_algorithm_failure_and_invalid_output_fail_closed() -> None:
    failed = predictions(run(scenario(), algorithm=FailingAlgorithm()))[0]
    assert failed.bias == "UNAVAILABLE" and failed.reason_codes[0] == "algorithm_failed"
    invalid = predictions(run(scenario(), algorithm=InvalidAlgorithm()))[0]
    assert invalid.bias == "UNAVAILABLE" and invalid.reason_codes[0] == "algorithm_invalid_output"


def test_noncanonical_input_is_rejected() -> None:
    engine = core()
    engine.process(row(ev(at(10), "100", "a", sequence=1)))
    with pytest.raises(ValueError, match="duplicate_event"):
        engine.process(row(ev(at(11), "100", "a", sequence=2)))
    with pytest.raises(ValueError, match="out_of_order_event"):
        engine.process(row(ev(at(9), "100", "b", sequence=3)))


# --- identity failure ------------------------------------------------------------------


def test_identity_unavailable_writes_no_record_and_halts() -> None:
    engine = core()
    [halt] = engine.process(row(ev(at(0), "100", "x1", source="recorded-dataset")))
    assert isinstance(halt, M30IdentityUnavailable)
    assert halt.reason_codes == ("candle_identity_unavailable", "time_contract_mismatch")
    assert engine.health == "unavailable"
    assert engine.process(row(ev(at(31), "100", "x2", source="recorded-dataset"))) == ()


def test_feed_identity_change_halts_and_drops_pending() -> None:
    rows = scenario()[:5]
    rows.append(row(ev(at(45), "100", "z", source="MT5-quote-observation:time-offset=10800")))
    rows.append(row(ev(at(61), "100", "z2")))
    emitted = run(rows)
    assert isinstance(emitted[-1], M30IdentityUnavailable)
    assert emitted[-1].reason_codes[1] == "feed_identity_changed"
    assert outcomes(emitted) == []


def test_unsupported_time_contract_is_rejected_at_construction() -> None:
    with pytest.raises(M30IdentityError, match="unsupported_time_contract"):
        core(time_contract="adr025-binding-v1")


def test_recorded_utc_contract_accepts_non_mt5_source() -> None:
    rows = [row(dataclasses.replace(r.event, source="recorded-dataset")) for r in scenario()]
    assert predictions(run(rows, time_contract="recorded-utc-v1"))


# --- determinism / crash recomputation (Decision 12A) --------------------------------


def test_same_inputs_give_byte_identical_records() -> None:
    assert as_bytes(run(scenario())) == as_bytes(run(scenario()))


def test_crash_at_every_row_recomputes_identical_records() -> None:
    rows = scenario()
    uninterrupted = core()
    emitted_by_row = [list(uninterrupted.process(item)) for item in rows]
    for cut in range(len(rows) + 1):
        expected = as_bytes([e for batch in emitted_by_row[:cut] for e in batch])
        recovered = as_bytes(list(recompute(core, rows[:cut])))
        assert recovered == expected
        for stored, again in zip(expected, recovered, strict=True):
            assert resolve_write(stored, again, "m30_prediction_conflict") == "noop"


def test_trigger_not_committed_before_crash_keeps_prediction_substance() -> None:
    rows = scenario()
    committed_without_f = rows[:3] + rows[4:]  # F (e4) never committed
    original = predictions(run(rows))[0]
    recovered = predictions(run(committed_without_f))[0]
    assert recovered.freeze_event_identity == "e5"
    for name in (
        "prediction_id",
        "candle_id",
        "cutoff_time",
        "snapshot_event_identity",
        "bias",
        "evidence",
        "reference_price",
        "threshold",
        "input_provenance",
    ):
        assert getattr(recovered, name) == getattr(original, name)


def test_unversioned_rule_change_is_a_visible_conflict() -> None:
    stored = record_bytes(predictions(run(scenario()))[0])
    changed = record_bytes(predictions(run(scenario(), algorithm=FixtureAlgorithm(invert=True)))[0])
    with pytest.raises(M30ConflictError, match="m30_prediction_conflict"):
        resolve_write(stored, changed, "m30_prediction_conflict")
    assert resolve_write(None, changed, "m30_prediction_conflict") == "write"


WALL_CLOCK_OR_RUNTIME_FIELDS = {
    "materialized_at",
    "generated_at",
    "created_at",
    "recorded_at",
    "wall_clock",
    "generation_mode",
    "generation_reason",
    "lifecycle",
    "lifecycle_at_generation",
    "feature_status",
    "health",
    "host",
    "process_id",
    "code_fingerprint",
}


def test_hashed_content_has_no_wall_clock_or_lifecycle_fields() -> None:
    for model in (M30BiasPrediction, M30BiasOutcome):
        names = {f.name for f in dataclasses.fields(model)}
        assert not names & WALL_CLOCK_OR_RUNTIME_FIELDS
    event_times = {
        t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        for r in scenario()
        for t in (r.event.event_time, r.event.received_at)
    }
    for data in as_bytes(run(scenario())):
        for token in data.decode().split('"'):
            if token[:4].isdigit() and token.endswith("Z") and "T" in token:
                # Only input event times or M30 bucket boundaries (Δ = 0) may appear.
                boundary = token.endswith(("00:00.000000Z", "30:00.000000Z"))
                assert token in event_times or boundary, token


# --- Δ > 0 (ADR-026 rev 2.1 Decision 3) ---------------------------------------------------

LEAD = 300  # Δ = 5 minutes: C_k = B_k - 5 min
LEGACY_FEED = FeedIdentity(
    time_contract="legacy-adr019",
    feed_key="legacy:MT5-quote-observation:time-offset=0:XAUUSD",
    instrument_key="XAUUSD",
    price_source="bid",
    units="USD",
)


def expected_candle(bucket_start_minutes: int) -> str:
    return candle_id(LEGACY_FEED, at(bucket_start_minutes))


def targets(items: list[M30Emission]) -> list[tuple[datetime, str, str]]:
    return [(p.target_start, p.status, p.freeze_event_identity) for p in predictions(items)]


def lead_scenario() -> list[CommittedRow]:
    """Δ = 300 s. Cutoffs 10:25 / 10:55 are crossed by events strictly after them."""
    return [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(20), "100.40", "e2", sequence=2)),
        row(ev(at(25) - timedelta(microseconds=1), "100.30", "e3", sequence=3)),
        row(ev(at(26), "100.10", "e4", sequence=4)),  # F for target 10:30 (C = 10:25)
        row(ev(at(31), "100.90", "e5", sequence=5)),
        row(ev(at(50), "100.60", "e6", sequence=6)),
        row(ev(at(57), "100.70", "e7", sequence=7)),  # F for target 11:00 (C = 10:55)
        row(ev(at(61), "101.00", "e8", sequence=8)),  # closes 10:30
        row(ev(at(80), "101.10", "e9", sequence=9)),
    ]


def test_lead_gap_one_trigger_freezes_earliest_and_skips_latest_crossed_target() -> None:
    # F = 11:57 crosses cutoffs 10:25, 10:55, 11:25 and 11:55 (targets 10:30..12:00).
    # Earliest target 10:30 is eligible (e2 at 10:20 is in [10:00, 10:25)) -> FROZEN.
    # Latest crossed target is 12:00 (= bucket_start(11:57 + 5 min)), NOT 11:30, the
    # bucket containing F. Nothing is written for 11:00 and 11:30.
    rows = [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(20), "100.40", "e2", sequence=2)),
        row(ev(at(117), "100.70", "e3", sequence=3)),
    ]
    emitted = run(rows, delta=LEAD)
    assert targets(emitted) == [(at(30), "FROZEN", "e3"), (at(120), "SKIPPED", "e3")]
    frozen, skipped = predictions(emitted)
    assert frozen.candle_id == expected_candle(30)
    assert skipped.candle_id == expected_candle(120)
    assert expected_candle(90) not in {p.candle_id for p in predictions(emitted)}
    assert frozen.snapshot_event_identity == skipped.snapshot_event_identity == "e2"
    assert skipped.cutoff_time == at(115)
    assert skipped.freeze_after_target_open is False and skipped.freeze_lag_seconds == 0
    assert "discontinuous_feed" in skipped.reason_codes and skipped.bias == "UNAVAILABLE"


def test_lead_trigger_inside_latest_target_skips_that_target_as_late_freeze() -> None:
    # F = 11:10 >= B_final = 11:00. Crossed: 10:30 (C 10:25) and 11:00 (C 10:55).
    rows = [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(20), "100.40", "e2", sequence=2)),
        row(ev(at(70), "100.70", "e3", sequence=3)),
    ]
    emitted = run(rows, delta=LEAD)
    assert targets(emitted) == [(at(30), "FROZEN", "e3"), (at(60), "SKIPPED", "e3")]
    skipped = predictions(emitted)[1]
    assert skipped.candle_id == expected_candle(60)
    assert skipped.freeze_after_target_open is True
    assert skipped.freeze_lag_seconds == Decimal(10 * 60) + Decimal("0.1")


def test_lead_trigger_inside_single_crossed_target_is_a_late_frozen_record() -> None:
    # F = 10:32 crosses only cutoff 10:25; target 10:30 is eligible -> FROZEN, no SKIPPED.
    rows = [
        row(ev(at(0), "100.00", "e1", sequence=1)),
        row(ev(at(20), "100.40", "e2", sequence=2)),
        row(ev(at(32), "100.70", "e3", sequence=3)),
    ]
    emitted = run(rows, delta=LEAD)
    assert targets(emitted) == [(at(30), "FROZEN", "e3")]
    frozen = predictions(emitted)[0]
    assert frozen.freeze_after_target_open is True
    assert frozen.freeze_lag_seconds == Decimal(2 * 60) + Decimal("0.1")


def test_lead_scenario_freeze_points() -> None:
    emitted = run(lead_scenario(), delta=LEAD)
    assert targets(emitted) == [(at(30), "FROZEN", "e4"), (at(60), "FROZEN", "e7")]
    first, second = predictions(emitted)
    assert (first.cutoff_time, first.snapshot_event_identity) == (at(25), "e3")
    assert (second.cutoff_time, second.snapshot_event_identity) == (at(55), "e6")
    assert first.freeze_after_target_open is False and second.freeze_after_target_open is False


def test_lead_hashed_timestamps_are_event_times_boundaries_or_cutoffs() -> None:
    event_times = {
        t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        for r in lead_scenario()
        for t in (r.event.event_time, r.event.received_at)
    }
    # Independently stated: targets 10:30 and 11:00, ends 11:00 and 11:30, cutoffs
    # 10:25 and 10:55 (B_k - 5 min); no cutoff coincides with an input event time.
    boundaries = {"2026-01-05T10:30:00.000000Z", "2026-01-05T11:00:00.000000Z"}
    boundaries.add("2026-01-05T11:30:00.000000Z")
    cutoffs = {"2026-01-05T10:25:00.000000Z", "2026-01-05T10:55:00.000000Z"}
    assert not cutoffs & event_times
    seen: set[str] = set()
    for data in as_bytes(run(lead_scenario(), delta=LEAD)):
        for token in data.decode().split('"'):
            if token[:4].isdigit() and token.endswith("Z") and "T" in token:
                assert token in event_times | boundaries | cutoffs, token
                seen.add(token)
    assert cutoffs <= seen


def test_lead_prefix_invariance() -> None:
    rows = lead_scenario()
    full = predictions(run(rows, delta=LEAD))
    for end in range(4, len(rows) + 1):  # from F of target 10:30 (e4) onward
        prefix = predictions(run(rows[:end], delta=LEAD))
        assert record_bytes(prefix[0]) == record_bytes(full[0])
    for end in range(7, len(rows) + 1):  # from F of target 11:00 (e7) onward
        prefix = predictions(run(rows[:end], delta=LEAD))
        assert record_bytes(prefix[1]) == record_bytes(full[1])


def test_lead_future_mutation_from_trigger_cannot_change_prediction() -> None:
    base = predictions(run(lead_scenario(), delta=LEAD))[0]
    # Rows from F (e4, after cutoff 10:25 but before B_k 10:30) onward are mutated.
    injected = predictions(run(_mutate_after(lead_scenario(), 3), delta=LEAD))[0]
    assert injected.freeze_event_identity == "e4"
    assert record_bytes(base) == record_bytes(injected)


def test_lead_event_between_cutoff_and_target_open_is_not_information() -> None:
    # An event in [C_k, B_k) is the trigger, never part of I_k, even at exactly C_k.
    rows = lead_scenario()
    at_cutoff = rows[:3] + [row(dataclasses.replace(rows[3].event, event_time=at(25)))]
    first = predictions(run(at_cutoff, delta=LEAD))[0]
    assert first.snapshot_event_identity == "e3" and first.freeze_event_identity == "e4"
    assert first.reference_price == Decimal("100.30")
