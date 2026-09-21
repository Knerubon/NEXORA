"""Independent synthetic research-memory contracts; no profitability claims."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.experience import ExperienceRepository, ExperienceService
from nexora.experience.engine import fingerprint
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal

from tests.test_readiness_regressions import events, pipeline_config

T0 = datetime(2026, 9, 20, 10, tzinfo=UTC)


def event(minutes: int, price: str = "100", **kwargs: Any) -> NormalizedPriceEvent:
    return replace(
        events()[0],
        identity_key=f"sample:{minutes}",
        source_event_id=f"sample:{minutes}",
        event_time=T0 + timedelta(minutes=minutes),
        received_at=T0 + timedelta(minutes=minutes),
        source_sequence=minutes + 1,
        price=D(price),
        close=D(price),
        **kwargs,
    )


def output(action: str = "WAIT", *, with_plan: bool = True) -> dict[str, Any]:
    return {
        "signals": {
            "decision": {
                "action": action,
                "score": 75,
                "buy_strength": 78,
                "sell_strength": 46,
                "strength_available": True,
                "engine_version": "synthetic-engine-v1",
                "config_version": "synthetic-config-v1",
                "patterns": [],
                "positive_evidence": [
                    {"component": "matrix", "code": "aligned", "polarity": "bullish", "points": 20}
                ],
                "negative_evidence": [],
                "future_conditions": [],
                "entry_zone": {"low": "99", "high": "101", "reason": "fixture"}
                if with_plan
                else None,
                "invalidation_price": ("90" if action == "BUY" else "110") if with_plan else None,
                "targets": [
                    {"name": "TP1", "price": "110" if action == "BUY" else "90"},
                    {"name": "TP2", "price": "120" if action == "BUY" else "80"},
                ]
                if with_plan
                else [],
                "risk_reward": "2" if with_plan else None,
            },
            "latest": None,
        },
        "matrix": {
            "resolutions": [
                {
                    "name": name,
                    "direction": "X",
                    "status": "ready",
                    "latest_transition": {"column_id": 1, "to_price": "100"},
                }
                for name in ("fast", "medium", "slow")
            ]
        },
        "structure": {"pivots": [], "levels": []},
        "regime": {"state": {"label": "trend", "reason": "fixture", "config_version": "r1"}},
        "columns": [],
        "transitions": [],
    }


@pytest.fixture
def store(tmp_path: Path) -> Any:
    journal = SQLiteJournal(tmp_path / "experiences.sqlite")
    try:
        yield journal
    finally:
        journal.close()


def service(store: SQLiteJournal) -> ExperienceService:
    return ExperienceService(store, RuntimeConfig(pipeline_config(), "USD/oz"), "research:test")


def test_poll_dedup_and_meaningful_episodes(store: SQLiteJournal) -> None:
    observer = service(store)
    first = output()
    observer.observe(event(0), first)
    eid = observer.repository.all()[0].experience_id
    for minute in range(1, 4):
        noisy = deepcopy(first)
        noisy["signals"]["decision"].update(score=minute, buy_strength=minute)
        noisy["matrix"].update(sequence=minute, generated_at=str(minute))
        noisy["matrix"]["resolutions"][0]["latest_transition"]["to_price"] = str(100 + minute)
        observer.observe(event(minute, str(100 + minute)), noisy)
    assert len(observer.repository.all()) == 1
    observer.observe(event(4), output("SELL"))
    observer.observe(event(5), first)
    assert [e.action for e in observer.repository.all()] == ["WAIT", "SELL", "WAIT"]
    assert len({e.experience_id for e in observer.repository.all()}) == 3
    assert observer.repository.all()[0].experience_id == eid


@pytest.mark.parametrize(
    "change", ["matrix", "column", "readiness", "structure", "pattern", "config"]
)
def test_transition_fingerprint(change: str) -> None:
    before = output()
    after = deepcopy(before)
    if change == "matrix":
        after["matrix"]["resolutions"][1]["direction"] = "O"
    elif change == "column":
        after["matrix"]["resolutions"][0]["latest_transition"]["column_id"] = 2
    elif change == "readiness":
        after["matrix"]["resolutions"][2]["status"] = "stale"
    elif change == "structure":
        after["structure"]["levels"] = [{"price": "90", "status": "confirmed"}]
    elif change == "pattern":
        after["signals"]["decision"]["patterns"] = [{"pattern_type": "double_top"}]
    else:
        after["signals"]["decision"]["engine_version"] = "v2"
    assert fingerprint("scope", before) != fingerprint("scope", after)
    assert fingerprint("other-config-hash", before) != fingerprint("scope", before)


def test_multiple_patterns_and_nested_immutability(store: SQLiteJournal) -> None:
    observer = service(store)
    value = output("SELL")
    patterns = [
        {"pattern_type": "double_top", "relation": "confirmation"},
        {"pattern_type": "double_bottom", "relation": "conflict"},
    ]
    value["signals"]["decision"]["patterns"] = deepcopy(patterns)
    observer.observe(event(0), value)
    experience = observer.repository.all()[0]
    before = canonical_hash(experience)
    value["signals"]["decision"]["patterns"].clear()
    exposed = experience.context()
    exposed["decision"]["patterns"].clear()
    assert experience.context()["decision"]["patterns"] == patterns
    with pytest.raises(FrozenInstanceError):
        cast(Any, experience).action = "BUY"
    observer.observe(event(60, "1000"), output("BUY"))
    assert canonical_hash(observer.repository.get(experience.experience_id)) == before
    assert experience.context()["decision"]["buy_strength"] == 78
    assert experience.context()["decision"]["sell_strength"] == 46


@pytest.mark.parametrize(
    "action,prices", [("BUY", ("100", "112", "94", "105")), ("SELL", ("100", "88", "106", "95"))]
)
def test_direction_math_horizons_and_r(
    store: SQLiteJournal,
    action: str,
    prices: tuple[str, ...],
) -> None:
    observer = service(store)
    value = output(action)
    observer.observe(event(0), value)
    eid = observer.repository.all()[0].experience_id
    for minute, price in zip((1, 2, 3, 5), prices, strict=True):
        observer.observe(event(minute, price), value, completeness="partial")
    horizon = observer.repository.outcomes(eid)[0]
    assert horizon["mfe"] == "12" and horizon["mae"] == "6"
    assert horizon["mfe_r"] == "1.2" and horizon["mae_r"] == "0.6"
    assert horizon["price_change"] == ("5" if action == "BUY" else "-5")
    assert horizon["directional_change"] == "5"
    assert horizon["sample_count"] == 4
    assert horizon["plan_hits"]["TP1"]["price"] == prices[1]
    for minute in (15, 30, 60):
        observer.observe(event(minute, prices[-1]), value)
    assert [o["horizon_minutes"] for o in observer.repository.outcomes(eid)] == [5, 15, 30, 60]
    assert observer.repository.lifecycle(eid)[-1]["state"] == "CLOSED"
    assert observer.repository.summary()["completed"] == 1


def test_late_endpoint_does_not_leak_into_excursion_or_hits(store: SQLiteJournal) -> None:
    observer = service(store)
    value = output("BUY")
    observer.observe(event(0), value)
    observer.observe(event(1), value)
    observer.observe(event(6, "150", is_gap=True), value, completeness="partial")
    eid = observer.repository.all()[0].experience_id
    horizon = observer.repository.outcomes(eid)[0]
    assert horizon["endpoint_delay_seconds"] == 60
    assert horizon["price_change"] == "50"
    assert horizon["mfe"] == "0" and horizon["mae"] == "0"
    assert horizon["plan_hits"]["TP1"] is None
    assert horizon["sample_event_ids"] == ["sample:1"]
    assert observer.repository.raw_observations(eid)[-1]["event"]["is_gap"]
    # Even later extremes cannot update the completed +5m label.
    observer.observe(event(15, "500"), value)
    assert observer.repository.outcomes(eid)[0] == horizon


def test_wait_market_movement_without_fabricated_trade(store: SQLiteJournal) -> None:
    observer = service(store)
    value = output("WAIT")  # Even malformed WAIT with a plan must not become a trade.
    observer.observe(event(0), value)
    for minute, price in ((1, "110"), (2, "94"), (5, "102"), (60, "200")):
        observer.observe(event(minute, price), value)
    eid = observer.repository.all()[0].experience_id
    row = observer.repository.outcomes(eid)[0]
    assert row["market_upward_excursion"] == "10"
    assert row["market_downward_excursion"] == "6"
    assert row["price_change"] == "2"
    for field in ("mfe", "mae", "mfe_r", "mae_r", "risk_distance", "directional_change"):
        assert row[field] is None
    assert all(hit is None for hit in row["plan_hits"].values())
    assert [r["state"] for r in observer.repository.lifecycle(eid)] == ["OBSERVED", "CLOSED"]


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_missing_plan_and_null_evidence(store: SQLiteJournal, action: str) -> None:
    observer = service(store)
    value = output(action, with_plan=False)
    decision = value["signals"]["decision"]
    for key in ("buy_strength", "sell_strength", "engine_version", "config_version"):
        decision.pop(key)
    observer.observe(event(0), value)
    observer.observe(event(5, "105"), value)
    observer.observe(event(60, "105"), value)
    experience = observer.repository.all()[0]
    row = observer.repository.outcomes(experience.experience_id)[0]
    assert row["mfe_r"] is None and row["risk_distance"] is None
    assert row["reference_kind"] == "t0_price"
    assert experience.context()["provenance"]["signal_engine_version"] is None
    assert experience.context()["news_context"] is None
    assert observer.repository.lifecycle(experience.experience_id)[-1]["state"] == "EXPIRED"


@pytest.mark.parametrize(
    "action,price,terminal",
    [
        ("BUY", "89", "INVALIDATED"),
        ("SELL", "111", "INVALIDATED"),
        ("BUY", "121", "TP2"),
        ("SELL", "79", "TP2"),
    ],
)
def test_lifecycle_requires_future_entry(
    store: SQLiteJournal,
    action: str,
    price: str,
    terminal: str,
) -> None:
    observer = service(store)
    value = output(action)
    observer.observe(event(0), value)
    eid = observer.repository.all()[0].experience_id
    observer.observe(event(1, price), value)
    assert observer.repository.lifecycle(eid)[-1]["state"] == "SIGNAL_CREATED"
    observer.observe(event(2), value)
    assert [r["state"] for r in observer.repository.lifecycle(eid)][-2:] == [
        "ENTRY_TRIGGERED",
        "ACTIVE",
    ]
    observer.observe(event(3, price), value)
    assert observer.repository.lifecycle(eid)[-1]["state"] == terminal
    observer.observe(event(60), value)
    assert observer.repository.lifecycle(eid)[-1]["state"] == terminal


def test_delayed_t0_and_empty_horizon_window(store: SQLiteJournal) -> None:
    observer = service(store)
    t0 = replace(event(0), received_at=T0 + timedelta(minutes=10))
    observer.observe(t0, output("BUY"))
    observer.observe(event(5, "1000"), output("BUY"))
    observer.observe(event(20, "102"), output("BUY"))
    eid = observer.repository.all()[0].experience_id
    row = observer.repository.outcomes(eid)[0]
    assert row["due_at"] == (T0 + timedelta(minutes=15)).isoformat()
    assert row["mfe"] is None and row["sample_count"] == 0
    assert row["endpoint_delay_seconds"] == 300
    assert row["price_change"] == "2"


def test_restart_original_decisions_pending_horizons_and_config_scope(
    store: SQLiteJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = RuntimeConfig(pipeline_config(), "USD/oz")
    runtime = ResearchRuntime(config, store)
    for e in (event(0), event(1), event(5, "104")):
        runtime.ingest(e)
    repository = ExperienceRepository(store)
    before = repository.all()
    old_outcomes = {e.experience_id: repository.outcomes(e.experience_id) for e in before}
    original = ResearchPipeline.process

    def changed_engine(self: ResearchPipeline, e: NormalizedPriceEvent) -> dict[str, Any]:
        result = original(self, e)
        result["signals"]["decision"]["score"] = 999  # Upgrade interpretation must not be frozen.
        return result

    monkeypatch.setattr(ResearchPipeline, "process", changed_engine)
    recovered = ResearchRuntime(config, store)
    assert repository.all() == before
    assert {e.experience_id: repository.outcomes(e.experience_id) for e in before} == old_outcomes
    recovered.ingest(event(60, "104"))
    assert len(repository.outcomes(before[0].experience_id)) == 4
    assert repository.get(before[0].experience_id) == before[0]
    # Configuration change gets an independent stream/identity even for identical data.
    changed = ResearchRuntime(replace(config, implementation_version="synthetic-new"), store)
    changed.ingest(event(0))
    assert repository.all()[-1].scope != before[0].scope


def test_append_failure_recovers_after_runtime_commit(
    store: SQLiteJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = RuntimeConfig(pipeline_config(), "USD/oz")
    runtime = ResearchRuntime(config, store)
    runtime.ingest(event(0))
    original = store.append
    failed = False

    def interrupted(
        stream: str, key: str, payload: Any, *, expected_count: int | None = None
    ) -> bool:
        nonlocal failed
        if stream.endswith(":outcomes") and not failed:
            failed = True
            raise OSError("synthetic_interruption")
        return original(stream, key, payload, expected_count=expected_count)

    monkeypatch.setattr(store, "append", interrupted)
    with pytest.raises(OSError, match="synthetic_interruption"):
        runtime.ingest(event(60))
    restored = ResearchRuntime(config, store)
    eid = restored.experience.repository.all()[0].experience_id
    assert len(restored.experience.repository.outcomes(eid)) == 4
    restored.ingest(event(60))
    assert len(restored.events()) == 2
    assert len(restored.experience.repository.outcomes(eid)) == 4


def test_runtime_observer_has_no_feedback_to_any_decision(store: SQLiteJournal) -> None:
    config = pipeline_config()
    pure = ResearchPipeline(config)
    runtime = ResearchRuntime(RuntimeConfig(config, "USD/oz"), store)
    for e in events():
        expected = pure.process(e)
        runtime.ingest(e)
        assert runtime.snapshot()["output"] == expected
    frozen = runtime.experience.repository.all()
    for minute, price in ((5, "1"), (15, "1000"), (30, "99"), (60, "101")):
        e = replace(event(minute, price), source_sequence=100 + minute)
        runtime.ingest(e)
        assert runtime.snapshot()["output"] == pure.process(e)
    for experience in frozen:
        assert runtime.experience.repository.get(experience.experience_id) == experience


def test_existing_database_roundtrip_and_conflict(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    journal = SQLiteJournal(path)
    journal.append("legacy", "one", {"preserve": True})
    observer = service(journal)
    value = output()
    observer.observe(event(0), value, metadata={"quality": {"status": "stale"}})
    before = observer.repository.all()
    journal.close()
    reopened = SQLiteJournal(path)
    try:
        assert reopened.read("legacy") == ({"preserve": True},)
        assert ExperienceRepository(reopened).all() == before
        service(reopened).observe(event(0), value, metadata={"quality": {"status": "stale"}})
        altered = replace(before[0], context_json="{}")
        with pytest.raises(ValueError, match="identity_conflict"):
            ExperienceRepository(reopened).save(altered)
        assert canonical_serialize(before[0].context()["metadata"])["quality"]["status"] == "stale"
    finally:
        reopened.close()


@pytest.mark.parametrize("stop", ["100", "105", None])
def test_invalid_risk_never_divides_or_triggers_entry(
    store: SQLiteJournal, stop: str | None
) -> None:
    observer = service(store)
    value = output("BUY")
    value["signals"]["decision"]["invalidation_price"] = stop
    observer.observe(event(0), value)
    observer.observe(event(1), value)
    observer.observe(event(5, "110"), value)
    eid = observer.repository.all()[0].experience_id
    row = observer.repository.outcomes(eid)[0]
    assert row["mfe"] == "10" and row["mfe_r"] is None
    assert not row["entry_observed_by_horizon"]
    assert observer.repository.lifecycle(eid)[-1]["state"] == "SIGNAL_CREATED"


def test_original_plan_is_used_and_missing_tp_remains_unknown(store: SQLiteJournal) -> None:
    observer = service(store)
    original = output("BUY")
    original["signals"]["decision"]["targets"] = [{"name": "TP1", "price": "110"}]
    observer.observe(event(0), original)
    observer.observe(event(1), original)
    changed_plan = deepcopy(original)
    changed_plan["signals"]["decision"].update(
        entry_zone={"low": "199", "high": "201"},
        invalidation_price="190",
        targets=[{"name": "TP1", "price": "210"}, {"name": "TP2", "price": "220"}],
    )
    observer.observe(event(5, "120"), changed_plan)
    assert len(observer.repository.all()) == 1
    eid = observer.repository.all()[0].experience_id
    row = observer.repository.outcomes(eid)[0]
    assert row["measurement_reference"] == "100"
    assert row["mfe_r"] == "2"
    assert row["plan_hits"]["TP1"] is not None
    assert row["plan_hits"]["TP2"] is None


def test_raw_ohlc_is_preserved_without_inventing_intrabar_excursions(store: SQLiteJournal) -> None:
    observer = service(store)
    observer.observe(event(0), output("BUY"))
    observer.observe(event(5, "101", high=D("500"), low=D("1")), output("BUY"))
    eid = observer.repository.all()[0].experience_id
    assert observer.repository.outcomes(eid)[0]["mfe"] == "1"
    assert observer.repository.outcomes(eid)[0]["mae"] == "0"
    assert observer.repository.raw_observations(eid)[0]["event"]["high"] == "500"
    # Without another accepted observation the other horizons stay pending indefinitely.
    assert observer.repository.summary()["pending"] == 1


def test_source_scope_isolation_and_duplicate_conflict(store: SQLiteJournal) -> None:
    observer = service(store)
    observer.observe(event(0), output())
    observer.observe(event(0), output())
    eid = observer.repository.all()[0].experience_id
    observer.observe(event(60, "200", source="different-feed"), output())
    assert not observer.repository.outcomes(eid)
    assert len(observer.repository.all()) == 2
    with pytest.raises(ValueError, match="identity_conflict"):
        observer.observe(event(0, "999"), output())


def test_actual_multiple_pattern_contract_is_frozen(store: SQLiteJournal) -> None:
    from nexora.signals.models import PatternDirection, PatternEvidence

    value = output("SELL")
    cases: tuple[tuple[str, PatternDirection, Literal["confirmation", "conflict"]], ...] = (
        ("double_top", "bearish", "confirmation"),
        ("double_bottom", "bullish", "conflict"),
    )
    patterns = tuple(
        PatternEvidence(
            name,
            direction,
            T0 - timedelta(minutes=20),
            T0,
            D("90"),
            D("110"),
            name,
            f"fixture:{name}",
            "synthetic-pattern-v1",
            relation,
        )
        for name, direction, relation in cases
    )
    value["signals"]["decision"]["patterns"] = canonical_serialize(patterns)
    observer = service(store)
    observer.observe(event(0), value)
    frozen = observer.repository.all()[0]
    assert frozen.context()["decision"]["patterns"] == canonical_serialize(patterns)
    reordered = deepcopy(value)
    reordered["signals"]["decision"]["patterns"].reverse()
    observer.observe(event(1), reordered)
    assert len(observer.repository.all()) == 1


def test_pending_horizons_recover_in_fresh_process(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    path = tmp_path / "fresh.sqlite"
    journal = SQLiteJournal(path)
    config = RuntimeConfig(pipeline_config(), "USD/oz")
    runtime = ResearchRuntime(config, journal)
    runtime.ingest(event(0))
    frozen = ExperienceRepository(journal).all()[0]
    journal.close()
    program = """
import json, sys
from nexora.artifacts import decode
from nexora.experience import ExperienceRepository
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal
data = json.load(sys.stdin)
store = SQLiteJournal(sys.argv[1])
runtime = ResearchRuntime(decode(RuntimeConfig, data['config']), store)
runtime.ingest(decode(NormalizedPriceEvent, data['event']))
assert len(ExperienceRepository(store).outcomes(data['experience_id'])) == 4
store.close()
"""
    subprocess.run(
        [sys.executable, "-c", program, str(path)],
        check=True,
        timeout=30,
        input=json.dumps(
            canonical_serialize(
                {
                    "config": config,
                    "event": event(60),
                    "experience_id": frozen.experience_id,
                }
            )
        ),
        text=True,
        capture_output=True,
    )
    reopened = SQLiteJournal(path)
    try:
        repository = ExperienceRepository(reopened)
        assert repository.get(frozen.experience_id) == frozen
        assert len(repository.outcomes(frozen.experience_id)) == 4
    finally:
        reopened.close()


@pytest.mark.parametrize("restart_at", range(1, 7))
def test_delayed_entry_horizon_prefix_and_restart(
    store: SQLiteJournal, monkeypatch: pytest.MonkeyPatch, restart_at: int
) -> None:
    # Rin's independent timeline: no entry until +20, TP1 at +25.
    value = output("BUY")
    monkeypatch.setattr(ResearchPipeline, "process", lambda self, e: deepcopy(value))
    config = RuntimeConfig(pipeline_config(), "USD/oz")
    runtime = ResearchRuntime(config, store)
    sequence = [
        event(m, p)
        for m, p in ((0, "100"), (4, "105"), (20, "100"), (25, "110"), (30, "111"), (60, "111"))
    ]
    for index, sample in enumerate(sequence, 1):
        runtime.ingest(sample)
        if index == restart_at:
            runtime = ResearchRuntime(config, store)
    repo = ExperienceRepository(store)
    frozen = repo.all()[0]
    rows = repo.outcomes(frozen.experience_id)
    assert [r["horizon_minutes"] for r in rows] == [5, 15, 30, 60]
    for row in rows[:2]:
        assert row["entry_observed_by_horizon"] is False
        assert row["plan_hits"] == {"TP1": None, "TP2": None, "invalidation": None}
        assert row["sample_event_ids"] == ["sample:4"]
        assert row["mfe"] == "5" and row["mae"] == "0"
        assert row["endpoint_event_id"] == "sample:20"
    for row in rows[2:]:
        assert row["entry_observed_by_horizon"] is True
        assert row["plan_hits"]["TP1"]["event_id"] == "sample:25"
        assert row["plan_hits"]["TP2"] is None
    lifecycle = repo.lifecycle(frozen.experience_id)
    ResearchRuntime(config, store)
    assert repo.outcomes(frozen.experience_id) == rows
    assert repo.lifecycle(frozen.experience_id) == lifecycle
    assert repo.get(frozen.experience_id) == frozen


@pytest.mark.parametrize("restart_at", range(1, 9))
def test_late_market_time_cannot_backdate_horizon_or_lifecycle(
    store: SQLiteJournal, monkeypatch: pytest.MonkeyPatch, restart_at: int
) -> None:
    value = output("BUY")
    monkeypatch.setattr(ResearchPipeline, "process", lambda self, e: deepcopy(value))
    config = RuntimeConfig(pipeline_config(), "USD/oz")
    runtime = ResearchRuntime(config, store)
    # Receipt time is increasing. Entry market time +4 is not known until +20.
    # Then an older +3 TP2 and +5 invalidation arrive after newer lifecycle facts.
    timeline = (
        (0, 0, "100"),
        (2, 2, "105"),
        (20, 4, "100"),
        (21, 3, "120"),
        (25, 25, "110"),
        (26, 5, "89"),
        (30, 30, "111"),
        (60, 60, "111"),
    )
    for index, (received, market, price) in enumerate(timeline, 1):
        sample = replace(event(received, price), event_time=T0 + timedelta(minutes=market))
        runtime.ingest(sample)
        if index == restart_at:
            runtime = ResearchRuntime(config, store)
    repo = ExperienceRepository(store)
    frozen = repo.all()[0]
    rows = repo.outcomes(frozen.experience_id)
    for row in rows[:2]:
        assert row["entry_observed_by_horizon"] is False
        assert all(hit is None for hit in row["plan_hits"].values())
        assert row["sample_event_ids"] == ["sample:2"]
        assert row["mfe"] == "5" and row["mae"] == "0"
        assert row["endpoint_event_id"] == "sample:25"
    for row in rows[2:]:
        assert row["entry_observed_by_horizon"] is True
        assert row["plan_hits"]["TP1"]["event_id"] == "sample:25"
        assert row["plan_hits"]["TP2"] is None
        assert row["plan_hits"]["invalidation"] is None
        # Raw late prices still contribute to later setup-relative excursions.
        assert row["mfe"] == "20" and row["mae"] == "11"
    lifecycle = repo.lifecycle(frozen.experience_id)
    assert [r["state"] for r in lifecycle] == [
        "SIGNAL_CREATED",
        "ENTRY_TRIGGERED",
        "ACTIVE",
        "TP1",
        "CLOSED",
    ]
    ResearchRuntime(config, store)
    assert repo.outcomes(frozen.experience_id) == rows
    assert repo.lifecycle(frozen.experience_id) == lifecycle
    assert repo.get(frozen.experience_id) == frozen


def test_measure_replays_only_causal_window(
    store: SQLiteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexora.experience import engine

    observer = service(store)
    observer.observe(event(0), output("BUY"))
    frozen = observer.repository.all()[0]
    advance = engine.advance
    seen = []

    def checked_advance(experience: Any, previous: Any, sample: NormalizedPriceEvent) -> Any:
        assert sample.event_time <= T0 + timedelta(minutes=5)
        assert sample.received_at <= T0 + timedelta(minutes=5)
        seen.append(sample.identity_key)
        return advance(experience, previous, sample)

    monkeypatch.setattr(engine, "advance", checked_advance)
    samples = [
        {"event": sample, "completeness": "unknown"}
        for sample in (
            event(1),
            event(5, "110"),
            event(20, "120"),
            replace(event(21, "89"), event_time=T0 + timedelta(minutes=3)),
        )
    ]
    row = engine.measure(frozen, 5, samples, event(25, "150"))
    assert seen == ["sample:1", "sample:5"]
    assert row["entry_observed_by_horizon"] is True
    assert row["plan_hits"]["TP1"]["event_id"] == "sample:5"
    assert row["plan_hits"]["TP2"] is None
    assert row["plan_hits"]["invalidation"] is None
    assert row["mfe"] == D("10") and row["mae"] == D("0")
    assert row["price_change"] == D("50")
