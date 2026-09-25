"""ADR-023/024 Phase 2: isolated pipeline, startup and checkpoint integration."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from nexora.artifacts import canonical_serialize
from nexora.features import FeatureConfigError, default_features_config
from nexora.patterns import EMPTY_STATE, PatternEngine
from nexora.research import ResearchPipeline, checkpoint_state
from nexora.research import checkpoint as ckpt
from nexora.research.runtime import ResearchRuntime
from nexora.storage import SQLiteJournal
from nexora_api.research import configured_features, configured_runtime

from tests.test_pattern_engine import _shadow
from tests.test_recovery_checkpoint import (
    exact_state,
    journal_rows,
    payload_of,
    rewrite_payload,
    runtime_config,
    store_for,
    stream_events,
)


@pytest.mark.parametrize("partial", [False, True])
def test_shadow_changes_only_additive_pattern_output(partial: bool) -> None:
    config = runtime_config().pipeline
    disabled = ResearchPipeline(config)
    shadow = ResearchPipeline(
        config, features=_shadow({"legacy_pivot.double_top": "DISABLED"} if partial else None)
    )
    replay = ResearchPipeline(config, features=shadow.pattern_engine.features)
    evidence = []
    for event in stream_events(24):
        before = disabled.process(event)
        after = shadow.process(event)
        replay.replay(event)
        assert after == replay.snapshot()
        block = after.pop("pattern_engine")
        off = before.pop("pattern_engine")
        assert after == before  # all trading, structure, readiness and other fields
        assert off["status"]["effective_lifecycle"] == "DISABLED"
        assert off["status"]["health"] == "not_evaluated"
        assert off["current"] == off["changed"] == []
        assert block["status"]["effective_lifecycle"] == "SHADOW"
        evidence.extend(block["changed"])
        assert disabled.pattern_engine.state == EMPTY_STATE
    assert evidence
    assert all(r["lifecycle"] == "SHADOW" for r in evidence)
    if partial:
        assert all(r["algorithm_id"] != "legacy_pivot.double_top" for r in evidence)


def test_no_transition_and_duplicate_events_preserve_event_snapshot() -> None:
    pipeline = ResearchPipeline(runtime_config().pipeline, features=_shadow())
    events = stream_events(12)
    for event in events:
        output = pipeline.process(event)
    assert pipeline.process(events[-1]) == output
    quiet = replace(events[-1], source_event_id="quiet", identity_key="quiet", source_sequence=13)
    result = pipeline.process(quiet)
    assert result["pattern_engine"]["changed"] == []
    assert result["pattern_engine"]["sequence"] == output["pattern_engine"]["sequence"]


def test_disabled_skips_algorithms_and_shadow_failure_cannot_change_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = runtime_config().pipeline
    reference = ResearchPipeline(config)
    expected = [reference.process(event) for event in stream_events(6)]

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic_pattern_failure")

    monkeypatch.setattr(PatternEngine, "_step", broken)
    disabled = ResearchPipeline(config)
    shadow = ResearchPipeline(config, features=_shadow())
    for event, before in zip(stream_events(6), expected, strict=True):
        assert disabled.process(event) == before
        after = shadow.process(event)
        after.pop("pattern_engine")
        before.pop("pattern_engine")
        assert after == before


@pytest.mark.parametrize("mode", ["disabled", "shadow", "partial"])
def test_checkpoint_delta_cold_replay_and_future_events_are_byte_identical(
    tmp_path: Path,
    mode: str,
) -> None:
    features = (
        default_features_config()
        if mode == "disabled"
        else _shadow({"legacy_pivot.double_top": "DISABLED"} if mode == "partial" else None)
    )
    config, store = runtime_config(paper=True), store_for(tmp_path)
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        live = ResearchRuntime(
            config, journal, features=features, checkpoints=store, checkpoint_interval=0
        )
        events = stream_events(18)
        for event in events[:10]:
            live.ingest(event)
        live.checkpoint()
        payload = payload_of(store, live.stream)
        assert payload["feature_config_hash"] == features.feature_config_hash
        assert "_derived" not in payload["experience"]
        engine, restored_events, memory = checkpoint_state.restore_state(
            payload, config.pipeline, features=features
        )
        assert (
            ckpt.encode(checkpoint_state.encode_state(engine, restored_events, memory))[0]
            == (ckpt.encode(exact_state(live))[0])
        )
        for event in events[10:14]:
            live.ingest(event)
        rows = journal_rows(tmp_path / "journal.sqlite")
        recovered = ResearchRuntime(
            config, journal, features=features, checkpoints=store, checkpoint_interval=0
        )
        cold = ResearchRuntime(config, journal, features=features)
        assert recovered.last_recovery["mode"] == "checkpoint"
        assert recovered.last_recovery["replayed"] == 4
        assert ckpt.encode(exact_state(recovered))[0] == ckpt.encode(exact_state(cold))[0]
        assert exact_state(live) == exact_state(cold)
        assert recovered.paper is not None and cold.paper is not None
        assert recovered.paper.snapshot() == cold.paper.snapshot()
        assert journal_rows(tmp_path / "journal.sqlite") == rows
        # A no-delta restore also retains the last event's changed/health exactly.
        no_delta = ResearchRuntime(config, journal, features=features, checkpoints=store)
        assert no_delta.last_recovery["replayed"] == 0
        assert no_delta.experience._derived == type(no_delta.experience._derived)()
        assert exact_state(no_delta) == exact_state(cold)
        for event in events[14:]:
            recovered.engine.process(event)
            cold.engine.replay(event)
            assert recovered.engine.snapshot() == cold.engine.snapshot()
            assert recovered.engine.pattern_engine.state == cold.engine.pattern_engine.state
    finally:
        journal.close()


@pytest.mark.parametrize("from_shadow", [False, True])
def test_config_mismatch_rebuilds_same_stream_without_rewriting_rows(
    tmp_path: Path,
    from_shadow: bool,
) -> None:
    config, store = runtime_config(), store_for(tmp_path)
    old = _shadow() if from_shadow else default_features_config()
    new = default_features_config() if from_shadow else _shadow()
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        runtime = ResearchRuntime(config, journal, features=old, checkpoints=store)
        for event in stream_events(10):
            runtime.ingest(event)
        runtime.checkpoint()
        rows = journal_rows(tmp_path / "journal.sqlite")
        changed = ResearchRuntime(config, journal, features=new, checkpoints=store)
        assert changed.stream == runtime.stream
        assert changed.last_recovery["reason"] == "feature_config_mismatch"
        assert changed.last_recovery["mode"] == "full"
        assert changed.last_recovery["replayed"] == 10
        assert journal_rows(tmp_path / "journal.sqlite") == rows
        assert exact_state(changed) == exact_state(ResearchRuntime(config, journal, features=new))
        again = ResearchRuntime(config, journal, features=new, checkpoints=store)
        assert again.last_recovery["mode"] == "checkpoint"
        assert payload_of(store, changed.stream)["feature_config_hash"] == new.feature_config_hash
    finally:
        journal.close()


@pytest.mark.parametrize(
    "tamper",
    [
        lambda p: p.pop("feature_config_hash"),
        lambda p: p.update(feature_config_hash=42),
        lambda p: p.update(state_version=1),
        lambda p: p["pipeline"].pop("pattern_engine"),
        lambda p: p["pipeline"]["pattern_engine"].update(state_version=2),
        lambda p: p["pipeline"]["pattern_engine"].update(sequence=999),
        lambda p: p["pipeline"]["pattern_engine"].update(pivot_count=999),
        lambda p: p["pipeline"]["pattern_engine"].update(last_pivot_id="wrong"),
        lambda p: p["pipeline"]["pattern_engine"]["pending"].append(["wrong", 2]),
        lambda p: p["pipeline"]["pattern_engine"]["pending"][0].append(2),
        lambda p: p["pipeline"]["pattern_engine"]["window"].extend(
            p["pipeline"]["pattern_engine"]["window"] * 7
        ),
        lambda p: p["pipeline"]["pattern_engine"]["window"][0].update(column_id=999),
        lambda p: p["pipeline"]["pattern_engine"]["current"][0].update(pattern_id="tampered"),
        lambda p: p["pipeline"]["pattern_engine"]["current"][0].update(algorithm_version="wrong"),
        lambda p: p["pipeline"]["pattern_engine"]["current"][0].update(parameters_hash="wrong"),
        lambda p: p["pipeline"]["pattern_engine"]["current"][0].update(lifecycle="DISABLED"),
    ],
)
def test_corrupt_pattern_state_falls_back_atomically(
    tmp_path: Path,
    tamper: Callable[[dict[str, Any]], Any],
) -> None:
    config, features, store = runtime_config(), _shadow(), store_for(tmp_path)
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        runtime = ResearchRuntime(config, journal, features=features, checkpoints=store)
        for event in stream_events(24):
            runtime.ingest(event)
            if runtime.engine.pattern_engine.state.current:
                break
        assert runtime.engine.pattern_engine.state.current
        runtime.checkpoint()
        payload = payload_of(store, runtime.stream)
        tamper(payload)
        rewrite_payload(store, runtime.stream, ckpt.encode(payload)[0])
        rows = journal_rows(tmp_path / "journal.sqlite")
        recovered = ResearchRuntime(config, journal, features=features, checkpoints=store)
        assert recovered.last_recovery["mode"] == "full"
        assert recovered.last_recovery["reason"] == "state_invalid"
        assert exact_state(recovered) == exact_state(runtime)
        assert journal_rows(tmp_path / "journal.sqlite") == rows
    finally:
        journal.close()


def test_feature_hash_checked_before_component_decode() -> None:
    payload = {
        "state_version": checkpoint_state.STATE_VERSION,
        "feature_config_hash": "0" * 64,
        "events": None,
        "pipeline": None,
        "experience": None,
    }
    with pytest.raises(checkpoint_state.FeatureConfigMismatch):
        checkpoint_state.restore_state(payload, runtime_config().pipeline)


def test_every_event_snapshot_round_trips_including_expired_evidence(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        runtime = ResearchRuntime(runtime_config(), journal, features=_shadow())
        statuses: set[str] = set()
        for event in stream_events(20):
            runtime.ingest(event)
            payload = exact_state(runtime)
            engine, events, memory = checkpoint_state.restore_state(
                payload, runtime.config.pipeline, features=runtime.features
            )
            assert checkpoint_state.encode_state(engine, events, memory) == payload
            statuses.update(r.status for r in engine._output["pattern_engine"].changed)
        assert statuses == {"confirmed", "expired"}
    finally:
        journal.close()


def test_rejected_event_health_survives_checkpoint_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    config, features, store = runtime_config(), _shadow(), store_for(tmp_path)
    try:
        runtime = ResearchRuntime(config, journal, features=features, checkpoints=store)
        events = stream_events(14)
        for event in events[:8]:
            runtime.ingest(event)
        with monkeypatch.context() as patch:

            def broken(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("synthetic_step_failure")

            patch.setattr(PatternEngine, "_step", broken)
            runtime.ingest(events[8])
        assert runtime.engine._output["pattern_engine"].status.health == "unavailable"
        runtime.checkpoint()
        restored = ResearchRuntime(config, journal, features=features, checkpoints=store)
        assert restored.last_recovery["mode"] == "checkpoint"
        assert exact_state(restored) == exact_state(runtime)
        for event in events[9:]:
            runtime.engine.process(event)
            restored.engine.replay(event)
            assert restored.engine.snapshot() == runtime.engine.snapshot()
    finally:
        journal.close()


def test_restored_result_for_disabled_unit_is_rejected(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        runtime = ResearchRuntime(runtime_config(), journal, features=_shadow())
        for event in stream_events(24):
            runtime.ingest(event)
            if runtime.engine.pattern_engine.state.current:
                break
        result = runtime.engine.pattern_engine.state.current[0]
        features = _shadow({result.algorithm_id: "DISABLED"})
        payload = exact_state(runtime)
        # Even a re-signed hash cannot allow state for a disabled unit.
        payload["feature_config_hash"] = features.feature_config_hash
        with pytest.raises(checkpoint_state.StateInvalid, match="pattern_result_invalid"):
            checkpoint_state.restore_state(payload, runtime.config.pipeline, features=features)
    finally:
        journal.close()


def test_feature_configuration_cannot_read_another_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXORA_FEATURES_CONFIG", str(tmp_path / "foreign" / "features.json"))
    with pytest.raises(ValueError, match="configuration_outside_environment"):
        configured_features()


def test_nonempty_disabled_state_rejected(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        runtime = ResearchRuntime(runtime_config(), journal)
        runtime.ingest(stream_events(1)[0])
        payload = exact_state(runtime)
        payload["pipeline"]["pattern_engine"]["sequence"] = 1
        with pytest.raises(checkpoint_state.StateInvalid, match="pattern_disabled_state"):
            checkpoint_state.restore_state(payload, runtime.config.pipeline)
    finally:
        journal.close()


def test_startup_feature_file_and_existing_api_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient
    from nexora_api.main import create_app

    assert configured_features() == default_features_config()
    (tmp_path / "code").mkdir()
    config = tmp_path / "code" / "research.json"
    config.write_text(json.dumps(canonical_serialize(runtime_config())), encoding="utf-8")
    feature_file = tmp_path / "code" / "features.json"
    feature_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "api-test",
                "features": {"pattern_engine": {"lifecycle": "SHADOW"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXORA_RESEARCH_CONFIG", str(config))
    monkeypatch.setenv("NEXORA_FEATURES_CONFIG", str(feature_file))
    monkeypatch.setenv("NEXORA_RESEARCH_CHECKPOINTS", "off")
    journal = SQLiteJournal(tmp_path / "journal.sqlite")
    try:
        runtime = configured_runtime(journal)
        assert runtime is not None
        runtime.ingest(stream_events(1)[0])
        with TestClient(
            create_app(runtime=runtime, journal=journal, start_worker=False),
            base_url="http://localhost",
        ) as client:
            output = client.get("/state").json()["research"]["output"]
        assert output["pattern_engine"] == runtime.engine.snapshot()["pattern_engine"]
        assert output["pattern_engine"]["status"]["effective_lifecycle"] == "SHADOW"
    finally:
        journal.close()


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "{",
        '{"schema_version": 1, "version": "v",'
        '"features": {"pattern_engine": {"lifecycle": "ACTIVE"}}}',
    ],
)
def test_bad_feature_file_fails_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
) -> None:
    (tmp_path / "code").mkdir()
    path = tmp_path / "code" / "features.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("NEXORA_FEATURES_CONFIG", str(path))
    with pytest.raises(FeatureConfigError):
        configured_features()
