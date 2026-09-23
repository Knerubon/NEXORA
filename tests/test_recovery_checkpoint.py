"""Checkpoint-assisted research recovery must equal full journal replay (ADR-022)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from nexora.artifacts import canonical_serialize
from nexora.market_data.models import NormalizedPriceEvent
from nexora.paper.session import PaperSessionConfig
from nexora.research import ResearchPipeline
from nexora.research.checkpoint import CheckpointStore
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.risk import risk_policy_fixture
from nexora.signals import SignalEngine
from nexora.storage import Journal, SQLiteJournal

from tests.test_readiness_regressions import pipeline_config
from tests.test_signal_intelligence import inputs
from tests.test_signals import _config

ENV = "development"


def stream_events(count: int, *, offset: int = 0) -> list[NormalizedPriceEvent]:
    """Deterministic zig-zag trend with enough reversals for signals and Experience horizons."""
    start = datetime(2026, 3, 1, 9, tzinfo=UTC)
    pattern = (100, 104, 99, 106, 98, 108, 102, 110, 104, 112)
    result = []
    for i in range(1, count + 1):
        price = D(pattern[(i - 1) % 10] + 3 * ((i - 1) // 10) + offset)
        at = start + timedelta(minutes=i)
        result.append(
            NormalizedPriceEvent(
                1,
                f"event:{i}",
                "recorded-test",
                "XAUUSD",
                "bar",
                at,
                at,
                i,
                i,
                f"event:{i}",
                "close",
                "USD/oz",
                1,
                price,
                close=price,
            )
        )
    return result


def runtime_config(*, paper: bool = False) -> RuntimeConfig:
    return RuntimeConfig(
        pipeline_config(),
        "USD/oz",
        paper=(
            PaperSessionConfig(
                "paper-checkpoint",
                "paper-account",
                D("10000"),
                D("0.05"),
                D("0.1"),
                risk_policy_fixture(),
                D("1"),
                D("1"),
            )
            if paper
            else None
        ),
    )


def store_for(tmp_path: Path, environment: str = ENV) -> CheckpointStore:
    return CheckpointStore(tmp_path / environment / "checkpoints", environment=environment)


def start(
    config: RuntimeConfig, journal: Journal, store: CheckpointStore | None, *, interval: int = 0
) -> ResearchRuntime:
    """Periodic checkpoints are off unless a test asks; recovery still writes one."""
    return ResearchRuntime(config, journal, checkpoints=store, checkpoint_interval=interval)


def state(runtime: ResearchRuntime) -> dict[str, Any]:
    """Every observable piece of recovered state, in canonical form."""
    result: dict[str, Any] = {
        "snapshot": runtime.snapshot(),
        "events": canonical_serialize(runtime.events()),
        "signals": canonical_serialize(runtime.engine.research_signals()),
        "experience": canonical_serialize(runtime.experience.checkpoint_state()),
    }
    if runtime.paper is not None:
        result["paper"] = runtime.paper.snapshot()
    return result


def journal_rows(path: Path) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT * FROM research_journal ORDER BY sequence").fetchall()
    finally:
        connection.close()


def rewrite_header(store: CheckpointStore, stream: str, **changes: Any) -> None:
    path = store.path_for(stream)
    raw = path.read_bytes()
    end = raw.index(b"\n")
    header = json.loads(raw[:end])
    header.update(changes)
    path.write_bytes(json.dumps(header, sort_keys=True).encode() + b"\n" + raw[end + 1 :])


def seeded(
    tmp_path: Path, count: int, *, paper: bool = False
) -> tuple[RuntimeConfig, SQLiteJournal, CheckpointStore, list[NormalizedPriceEvent]]:
    """A journal of `count` events with one checkpoint at its last event."""
    _, journal, store = new_journal(tmp_path)
    config = runtime_config(paper=paper)
    runtime = start(config, journal, store)
    stream = stream_events(count)
    for event in stream:
        runtime.ingest(event)
    runtime.checkpoint()
    return config, journal, store, stream


def full_replay(config: RuntimeConfig, journal: Journal) -> ResearchRuntime:
    return start(config, journal, None)


def spy_replays(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the identity of every event the engine replays during recovery."""
    replays: list[str] = []
    original = ResearchPipeline.replay

    def recording(self: ResearchPipeline, event: NormalizedPriceEvent) -> None:
        replays.append(event.identity_key)
        original(self, event)

    monkeypatch.setattr(ResearchPipeline, "replay", recording)
    return replays


def new_journal(tmp_path: Path) -> tuple[RuntimeConfig, SQLiteJournal, CheckpointStore]:
    return runtime_config(), SQLiteJournal(tmp_path / "r.sqlite"), store_for(tmp_path)


# Test 1 -------------------------------------------------------------------------------
def test_no_checkpoint_full_replay_then_writes_one(tmp_path: Path) -> None:
    config, journal = runtime_config(), SQLiteJournal(tmp_path / "r.sqlite")
    try:
        live = start(config, journal, None)
        for event in stream_events(30):
            live.ingest(event)
        store = store_for(tmp_path)
        recovered = start(config, journal, store)
        assert recovered.last_recovery["mode"] == "full"
        assert recovered.last_recovery["reason"] == "no_checkpoint"
        assert recovered.last_recovery["replayed"] == 30
        assert state(recovered) == state(live)
        assert store.path_for(recovered.stream).is_file()
        again = start(config, journal, store)
        assert again.last_recovery["mode"] == "checkpoint"
        assert again.last_recovery["replayed"] == 0
    finally:
        journal.close()


# Test 2 -------------------------------------------------------------------------------
def test_valid_checkpoint_without_delta_replays_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, journal, store, _ = seeded(tmp_path, 40)
    try:
        replays = spy_replays(monkeypatch)
        recovered = start(config, journal, store)
        assert recovered.last_recovery["mode"] == "checkpoint"
        assert recovered.last_recovery["replayed"] == 0 and replays == []
        assert len(recovered.events()) == 40
    finally:
        journal.close()


# Tests 3 and 9 ------------------------------------------------------------------------
def test_checkpoint_plus_delta_replays_only_later_events_and_never_the_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, journal, store, _ = seeded(tmp_path, 100)
    try:
        runtime = start(config, journal, store)
        for event in stream_events(110)[100:]:
            runtime.ingest(event)
        replays = spy_replays(monkeypatch)
        recovered = start(config, journal, store)
        assert recovered.last_recovery["mode"] == "checkpoint"
        assert recovered.last_recovery["replayed"] == 10
        assert replays == [f"event:{i}" for i in range(101, 111)]
        assert "event:100" not in replays
        keys = [event.identity_key for event in recovered.events()]
        assert keys == [f"event:{i}" for i in range(1, 111)]
        # Re-delivering the boundary event after restore is still a no-op.
        recovered.ingest(stream_events(100)[-1])
        assert len(recovered.events()) == 110
    finally:
        journal.close()


def commit_active_signal(journal: SQLiteJournal, stream: str, event: NormalizedPriceEvent) -> None:
    """Commit a research row whose recorded decision is an active BUY signal.

    Recovery drives paper and Experience from recorded output, exactly as in
    tests/test_signal_upgrade.py, so this exercises both side-effect paths.
    """
    signal = SignalEngine(_config()).evaluate(**inputs()).latest
    assert signal is not None and signal.decision is not None and signal.status == "active"
    signal = replace(
        signal,
        occurrence_time=event.event_time,
        confirmation_time=event.event_time,
        decision_time=event.received_at,
    )
    payload = canonical_serialize(signal)
    output = {"signals": {"latest": payload, "decision": payload["decision"]}}
    recorded = {"event": event, "output": output, "completeness": "complete"}
    assert journal.append(stream, event.identity_key, recorded)


# Test 4 -------------------------------------------------------------------------------
@pytest.mark.parametrize("paper", [False, True])
def test_checkpoint_plus_delta_state_equals_full_replay(tmp_path: Path, paper: bool) -> None:
    config, journal, store, _ = seeded(tmp_path, 20, paper=paper)
    try:
        stream = stream_events(40)
        runtime = start(config, journal, store)
        for event in stream[20:25]:
            runtime.ingest(event)
        commit_active_signal(journal, runtime.stream, stream[25])
        # This recovery replays the signal row and checkpoints the resulting memory.
        runtime = start(config, journal, store)
        assert runtime.last_recovery["replayed"] == 6
        for event in stream[26:32]:
            runtime.ingest(event)
        rows_before = journal_rows(tmp_path / "r.sqlite")
        accelerated = start(config, journal, store)
        reference = full_replay(config, journal)
        assert accelerated.last_recovery["mode"] == "checkpoint"
        assert accelerated.last_recovery["replayed"] == 6
        assert reference.last_recovery["replayed"] == 32
        assert state(accelerated) == state(reference) == state(runtime)
        # Recovery never rewrites, drops or adds journal history.
        assert journal_rows(tmp_path / "r.sqlite") == rows_before
        # Both continue identically on new live events.
        for event in stream[32:]:
            accelerated.ingest(event)
        assert state(accelerated) == state(full_replay(config, journal))
        assert any(e.action == "BUY" for e in accelerated.experience.repository.all())
        if paper:
            assert state(accelerated)["paper"]["orders"], "fixture must exercise paper orders"
    finally:
        journal.close()


# Test 5 -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        (lambda raw: raw[:-7] + b"garbage", "blob_hash_mismatch"),
        (lambda raw: raw[: len(raw) // 2], "blob_hash_mismatch"),
        (lambda raw: b"not a checkpoint", "header_corrupt"),
        (lambda raw: b"{broken json\n" + raw, "header_corrupt"),
    ],
)
def test_corrupted_checkpoint_falls_back_to_full_replay(
    tmp_path: Path, damage: Any, reason: str
) -> None:
    config, journal, store, _ = seeded(tmp_path, 30)
    try:
        path = store.path_for(start(config, journal, None).stream)
        path.write_bytes(damage(path.read_bytes()))
        recovered = start(config, journal, store)
        assert recovered.last_recovery == {
            **recovered.last_recovery,
            "mode": "full",
            "reason": reason,
        }
        assert recovered.last_recovery["replayed"] == 30
        assert state(recovered) == state(full_replay(config, journal))
        # The fallback rewrites a fresh valid checkpoint.
        assert start(config, journal, store).last_recovery["mode"] == "checkpoint"
    finally:
        journal.close()


def test_undecodable_blob_with_matching_hash_falls_back(tmp_path: Path) -> None:
    config, journal, store, _ = seeded(tmp_path, 20)
    try:
        stream = start(config, journal, None).stream
        path = store.path_for(stream)
        raw = path.read_bytes()
        blob = b"\x80\x05not-a-pickle"
        rewrite_header(
            store, stream, blob_hash=hashlib.sha256(blob).hexdigest(), blob_size=len(blob)
        )
        header = path.read_bytes().split(b"\n", 1)[0]
        path.write_bytes(header + b"\n" + blob)
        assert raw != path.read_bytes()
        recovered = start(config, journal, store)
        assert recovered.last_recovery["reason"] == "decode_failed"
        assert state(recovered) == state(full_replay(config, journal))
    finally:
        journal.close()


# Test 6 -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"schema_version": 999}, "schema_version_mismatch"),
        ({"format": "other"}, "schema_version_mismatch"),
        ({"code_fingerprint": "0" * 64}, "code_fingerprint_mismatch"),
    ],
)
def test_unsupported_checkpoint_version_falls_back(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    config, journal, store, _ = seeded(tmp_path, 20)
    try:
        rewrite_header(store, start(config, journal, None).stream, **changes)
        recovered = start(config, journal, store)
        assert (recovered.last_recovery["mode"], recovered.last_recovery["reason"]) == (
            "full",
            reason,
        )
        assert state(recovered) == state(full_replay(config, journal))
    finally:
        journal.close()


# Test 7 -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"last_sequence": 10**9}, "event_reference_invalid"),
        ({"last_content_hash": "0" * 64}, "event_reference_invalid"),
        ({"last_event_key": "event:1"}, "event_reference_invalid"),
        ({"event_count": 19}, "event_count_mismatch"),
        ({"state_hash": "0" * 64}, "state_validation_failed"),
    ],
)
def test_invalid_event_reference_or_state_falls_back(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    config, journal, store, _ = seeded(tmp_path, 20)
    try:
        rewrite_header(store, start(config, journal, None).stream, **changes)
        recovered = start(config, journal, store)
        assert recovered.last_recovery["reason"] == reason
        assert recovered.last_recovery["replayed"] == 20
        assert state(recovered) == state(full_replay(config, journal))
    finally:
        journal.close()


def test_checkpoint_newer_than_journal_is_rejected(tmp_path: Path) -> None:
    """A checkpoint that claims events the journal does not hold is never trusted."""
    config, journal, store, _ = seeded(tmp_path, 30)
    journal.close()
    shorter = SQLiteJournal(tmp_path / "shorter.sqlite")
    try:
        runtime = start(config, shorter, None)
        for event in stream_events(20):
            runtime.ingest(event)
        recovered = start(config, shorter, store)
        assert recovered.last_recovery["reason"] == "event_reference_invalid"
        assert len(recovered.events()) == 20
    finally:
        shorter.close()


# Test 8 -------------------------------------------------------------------------------
def test_environment_isolation(tmp_path: Path) -> None:
    from nexora_api.environment import Environment

    dev = Environment.resolve({"NEXORA_ENV": "development"}, code=tmp_path / "dev")
    prod = Environment.resolve({"NEXORA_ENV": "production"}, code=tmp_path / "prod")
    assert dev.checkpoints != prod.checkpoints
    assert dev.checkpoints.is_relative_to(dev.root) and prod.checkpoints.is_relative_to(prod.root)
    config = runtime_config()
    stores = {
        env.name: CheckpointStore(env.checkpoints, environment=env.name) for env in (dev, prod)
    }
    journals = {}
    for env, offset in ((dev, 0), (prod, 50)):
        env.storage.parent.mkdir(parents=True)
        journals[env.name] = SQLiteJournal(env.storage)
        runtime = start(config, journals[env.name], stores[env.name])
        for event in stream_events(25, offset=offset):
            runtime.ingest(event)
        runtime.checkpoint()
    try:
        stream = start(config, journals["development"], None).stream
        dev_file = stores["development"].path_for(stream)
        prod_file = stores["production"].path_for(stream)
        assert dev_file.parent == dev.checkpoints and prod_file.parent == prod.checkpoints
        reference = state(full_replay(config, journals["production"]))
        # A development checkpoint copied into production is refused by its header...
        prod_file.write_bytes(dev_file.read_bytes())
        recovered = start(config, journals["production"], stores["production"])
        assert recovered.last_recovery["reason"] == "environment_mismatch"
        assert state(recovered) == reference
        # ...and, with a forged environment, by the production journal's own rows.
        prod_file.write_bytes(dev_file.read_bytes())
        rewrite_header(stores["production"], stream, environment="production")
        recovered = start(config, journals["production"], stores["production"])
        assert recovered.last_recovery["reason"] == "event_reference_invalid"
        assert state(recovered) == reference
        # A production store never reads the development directory, and vice versa.
        assert (
            start(config, journals["development"], stores["development"]).last_recovery["mode"]
            == "checkpoint"
        )
    finally:
        for journal in journals.values():
            journal.close()


# Test 10 ------------------------------------------------------------------------------
def test_empty_journal_starts_without_checkpoint(tmp_path: Path) -> None:
    config, journal, store = new_journal(tmp_path)
    try:
        runtime = start(config, journal, store, interval=1)
        assert runtime.events() == ()
        assert runtime.last_recovery["replayed"] == 0
        assert not store.path_for(runtime.stream).exists()
        runtime.checkpoint()
        assert not store.path_for(runtime.stream).exists()
    finally:
        journal.close()


# Crash safety -------------------------------------------------------------------------
def test_crash_during_checkpoint_write_keeps_previous_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, journal, store, _ = seeded(tmp_path, 30)
    try:
        runtime = start(config, journal, store)
        path = store.path_for(runtime.stream)
        previous = path.read_bytes()
        for event in stream_events(35)[30:]:
            runtime.ingest(event)

        def crash(source: Any, target: Any) -> None:
            raise OSError("synthetic_crash_before_rename")

        monkeypatch.setattr(os, "replace", crash)
        runtime.checkpoint()  # A failed write never fails research processing.
        monkeypatch.undo()
        assert path.read_bytes() == previous
        assert not list(path.parent.glob("*.tmp"))
        recovered = start(config, journal, store)
        assert recovered.last_recovery["mode"] == "checkpoint"
        assert recovered.last_recovery["replayed"] == 5
        assert state(recovered) == state(full_replay(config, journal))
    finally:
        journal.close()


def test_leftover_partial_temporary_file_is_ignored(tmp_path: Path) -> None:
    config, journal, store, _ = seeded(tmp_path, 20)
    try:
        stream = start(config, journal, None).stream
        partial = store.path_for(stream).with_name(store.path_for(stream).name + ".999.tmp")
        partial.write_bytes(store.path_for(stream).read_bytes()[:100])
        recovered = start(config, journal, store)
        assert recovered.last_recovery["mode"] == "checkpoint"
    finally:
        journal.close()


def test_failed_checkpoint_save_never_fails_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, journal, store, _ = seeded(tmp_path, 10)
    try:
        runtime = start(config, journal, store, interval=1)

        def broken(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk_full")

        monkeypatch.setattr(store, "save", broken)
        for event in stream_events(13)[10:]:
            runtime.ingest(event)
        assert runtime.error is None and len(runtime.events()) == 13
        monkeypatch.undo()
        recovered = start(config, journal, store)
        assert recovered.last_recovery["replayed"] == 3
        assert state(recovered) == state(full_replay(config, journal))
    finally:
        journal.close()


def test_interrupted_side_effect_is_repaired_from_journal_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A research row committed without its Experience writes stays in the replay delta,
    even when the in-process rebuild also fails and later live ingests succeed."""
    config, journal, store = new_journal(tmp_path)
    try:
        runtime = start(config, journal, store, interval=1)
        stream = stream_events(80)
        for event in stream[:70]:
            runtime.ingest(event)
        path = store.path_for(runtime.stream)
        anchored_at = json.loads(path.read_bytes().split(b"\n", 1)[0])["last_event_key"]
        assert anchored_at == "event:70"
        original = journal.append

        def interrupted(stream_name: str, key: str, payload: Any, **kwargs: Any) -> bool:
            if stream_name.startswith("experience:"):
                raise OSError("synthetic_interruption")
            return original(stream_name, key, payload, **kwargs)

        monkeypatch.setattr(journal, "append", interrupted)
        # The research row commits; its Experience writes and the retry rebuild both fail,
        # this time inside the last journal row, which expected_count cannot detect.
        with pytest.raises(OSError, match="synthetic_interruption"):
            runtime.ingest(stream[70])
        # Nothing may be checkpointed from the partially rebuilt memory...
        runtime.checkpoint()
        header = json.loads(path.read_bytes().split(b"\n", 1)[0])
        assert header["last_event_key"] == "event:70"
        # ...and while it persists, live events are refused rather than written from it.
        with pytest.raises(OSError, match="synthetic_interruption"):
            runtime.ingest(stream[71])
        assert len(journal.read(runtime.stream)) == 71
        monkeypatch.undo()
        # The next live event first completes recovery (repairing event 71's side effects).
        runtime.ingest(stream[71])
        assert runtime.error is None and len(runtime.events()) == 72
        reference = full_replay(config, journal)  # Would raise journal_identity_conflict if
        assert state(runtime) == state(reference)  # outcomes had been written from bad memory.
        recovered = start(config, journal, store)
        assert recovered.last_recovery["mode"] == "checkpoint"
        assert state(recovered) == state(reference)
        for event in stream[72:]:
            recovered.ingest(event)
        assert state(recovered) == state(full_replay(config, journal))
    finally:
        journal.close()


# Policy -------------------------------------------------------------------------------
def test_periodic_checkpoint_bounds_the_restart_delta(tmp_path: Path) -> None:
    config, journal, store = new_journal(tmp_path)
    try:
        runtime = start(config, journal, store, interval=10)
        for event in stream_events(47):
            runtime.ingest(event)
        recovered = start(config, journal, store)
        assert recovered.last_recovery["checkpoint_sequence"] is not None
        assert recovered.last_recovery["replayed"] == 7
    finally:
        journal.close()


def test_checkpoints_disabled_preserve_full_replay(tmp_path: Path) -> None:
    config, journal, _, _ = seeded(tmp_path, 20)
    try:
        # Existing callers construct the runtime without a store: behavior is unchanged.
        disabled = ResearchRuntime(config, journal)
        assert disabled.last_recovery["mode"] == "full"
        assert disabled.last_recovery["reason"] == "checkpoints_disabled"
        assert disabled.last_recovery["replayed"] == 20
    finally:
        journal.close()


def test_journal_without_row_anchors_uses_full_replay(tmp_path: Path) -> None:
    class PlainJournal:
        """Only the base Journal protocol, like a backend without row anchors."""

        def __init__(self, inner: SQLiteJournal) -> None:
            self.inner, self.backend = inner, "plain"

        def append(self, *args: Any, **kwargs: Any) -> bool:
            return self.inner.append(*args, **kwargs)

        def read(self, stream: str) -> tuple[dict[str, Any], ...]:
            return self.inner.read(stream)

        def iter_read(self, stream: str) -> Any:
            return self.inner.iter_read(stream)

        def close(self) -> None:
            self.inner.close()

    config, journal, store, _ = seeded(tmp_path, 15)
    try:
        recovered = start(config, PlainJournal(journal), store)
        assert recovered.last_recovery["reason"] == "journal_not_anchored"
        assert recovered.last_recovery["replayed"] == 15
    finally:
        journal.close()


def test_deleting_checkpoint_never_touches_history(tmp_path: Path) -> None:
    config, journal, store, _ = seeded(tmp_path, 25)
    try:
        rows = journal_rows(tmp_path / "r.sqlite")
        expected = state(start(config, journal, store))
        for path in store.directory.iterdir():
            path.unlink()
        recovered = start(config, journal, store)
        assert recovered.last_recovery["reason"] == "no_checkpoint"
        assert state(recovered) == expected
        assert journal_rows(tmp_path / "r.sqlite") == rows
    finally:
        journal.close()


# API wiring ---------------------------------------------------------------------------
def _api_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    import nexora_api.environment as environment

    for key in [k for k in os.environ if k.startswith("NEXORA_")]:
        monkeypatch.delenv(key)
    root, code = tmp_path / name / "runtime", tmp_path / name / "code"
    code.mkdir(parents=True)
    config_file = code / "research.json"
    config_file.write_text(json.dumps(canonical_serialize(runtime_config())), encoding="utf-8")
    monkeypatch.setattr(environment, "REPOSITORY", code)
    monkeypatch.setenv("NEXORA_ENV", name)
    monkeypatch.setenv("NEXORA_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("NEXORA_RESEARCH_CONFIG", str(config_file))
    return root


def _api_session(events: list[NormalizedPriceEvent] | None = None) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from nexora_api.main import create_app

    app = create_app(start_worker=False)
    with TestClient(app):
        runtime = app.state.runtime
        for event in events or []:
            runtime.ingest(event)
        return dict(runtime.last_recovery)


@pytest.mark.parametrize("name", ["development", "production"])
def test_api_checkpoints_in_its_own_environment_and_at_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    root = _api_environment(tmp_path, monkeypatch, name)
    assert _api_session(stream_events(12))["reason"] == "no_checkpoint"
    files = list((root / "checkpoints").iterdir())
    assert [f.suffix for f in files] == [".checkpoint"]
    header = json.loads(files[0].read_bytes().split(b"\n", 1)[0])
    assert header["environment"] == name and header["event_count"] == 12
    # Graceful shutdown checkpointed every event: the warm restart replays nothing.
    warm = _api_session()
    assert (warm["mode"], warm["replayed"]) == ("checkpoint", 0)
    other = "production" if name == "development" else "development"
    assert not (tmp_path / other).exists()


def test_api_checkpoint_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _api_environment(tmp_path, monkeypatch, "development")
    monkeypatch.setenv("NEXORA_RESEARCH_CHECKPOINTS", "off")
    assert _api_session(stream_events(5))["reason"] == "checkpoints_disabled"
    assert not any((root / "checkpoints").iterdir())
    monkeypatch.setenv("NEXORA_RESEARCH_CHECKPOINTS", "maybe")
    with pytest.raises(ValueError, match="invalid_research_checkpoints"):
        _api_session()


def test_invalid_checkpoint_interval_is_rejected(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "r.sqlite")
    try:
        with pytest.raises(ValueError, match="invalid_checkpoint_interval"):
            ResearchRuntime(runtime_config(), journal, checkpoint_interval=-1)
    finally:
        journal.close()
