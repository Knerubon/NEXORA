"""Recovery must retain every verified event without loading all stored outputs."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal

from tests.test_readiness_regressions import events, pipeline_config


def test_bounded_recovery_order_and_fixed_boundary(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "stream.sqlite")
    try:
        for i in range(70):
            journal.append("a", str(i), {"index": i})
            journal.append("other", str(i), {"index": -i})
        stream = journal.iter_read("a")
        assert next(stream) == {"index": 0}
        journal.append("a", "later", {"index": 70})
        assert list(stream) == [{"index": i} for i in range(1, 70)]
        assert list(journal.iter_read("empty")) == []
        assert tuple(journal.iter_read("a")) == journal.read("a")
    finally:
        journal.close()


def test_verified_json_equivalence_and_corruption(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "verify.sqlite")
    try:
        value = {
            "z": [Decimal("1.2300"), datetime(2026, 1, 1, tzinfo=UTC)],
            "a": {"ไทย": True, "empty": None, "float": 1.5, "zero": -0.0},
        }
        journal.append("a", "first", value)
        assert tuple(journal.iter_read("a")) == journal.read("a")
        journal.connection.execute("UPDATE research_journal SET payload=?", ('{"changed":true}',))
        journal.connection.commit()
        with pytest.raises(ValueError, match="journal_corrupt"):
            list(journal.iter_read("a"))
        with pytest.raises(ValueError, match="journal_corrupt"):
            journal.read("a")
    finally:
        journal.close()


def test_runtime_recovery_does_not_materialize_entire_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SQLiteJournal(tmp_path / "runtime.sqlite")
    try:
        config = RuntimeConfig(pipeline_config(), "USD/oz")
        runtime = ResearchRuntime(config, journal)
        for event in events():
            runtime.ingest(event)
        expected = runtime.snapshot()

        def reject_bulk_read(stream: str) -> tuple[dict[str, object], ...]:
            raise AssertionError("recovery must stream persisted events")

        monkeypatch.setattr(journal, "read", reject_bulk_read)
        recovered = ResearchRuntime(config, journal)
        assert recovered.snapshot() == expected
        assert recovered.events() == runtime.events()
    finally:
        journal.close()


def test_replay_preserves_every_prefix_and_following_live_event() -> None:
    from nexora.research import ResearchPipeline

    config = pipeline_config()
    normal, replayed = ResearchPipeline(config), ResearchPipeline(config)
    stream = events()
    for event in stream[:-1]:
        expected = normal.process(event)
        replayed.replay(event)
        assert replayed.snapshot() == expected
        assert replayed.research_signals() == normal.research_signals()
    assert replayed.process(stream[-1]) == normal.process(stream[-1])
