"""Upgrade restart uses persisted decisions, never new historical interpretations."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from nexora.artifacts import canonical_hash, canonical_serialize, decode
from nexora.paper.session import PaperSession, PaperSessionConfig
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.risk import risk_policy_fixture
from nexora.signals import ResearchSignal, SignalEngine
from nexora.storage import SQLiteJournal

from tests.test_readiness_regressions import events, pipeline_config
from tests.test_signal_intelligence import inputs
from tests.test_signals import _config


@pytest.mark.parametrize("committed", [True, False])
def test_upgrade_replays_original_signal_and_recovers_pending_submission(
    tmp_path: Path, committed: bool
) -> None:
    paper_cfg = PaperSessionConfig(
        "paper-upgrade",
        "paper-account",
        Decimal("10000"),
        Decimal("0.05"),
        Decimal("0.1"),
        risk_policy_fixture(),
        Decimal("1"),
        Decimal("1"),
    )
    journal = SQLiteJournal(tmp_path / "upgrade.sqlite")
    try:
        config = RuntimeConfig(pipeline_config(), "USD/oz", paper_cfg)
        runtime = ResearchRuntime(config, journal)
        event = events()[0]
        signal = SignalEngine(_config()).evaluate(**inputs()).latest
        assert signal is not None and signal.decision is not None
        signal = replace(
            signal,
            occurrence_time=event.event_time,
            confirmation_time=event.event_time,
            decision_time=event.received_at,
            decision=replace(signal.decision, engine_version="legacy:p8a-v1"),
        )
        old_payload = canonical_serialize(signal)
        for key in ("buy_strength", "sell_strength", "strength_available"):
            old_payload["decision"].pop(key)
        recorded = {
            "event": event,
            "output": {"signals": {"latest": old_payload}},
            "completeness": "complete",
        }
        journal.append(runtime.stream, event.identity_key, recorded)
        if committed:
            # Construct a genuine legacy journal row without new default fields.
            source = SQLiteJournal(tmp_path / "legacy.sqlite")
            try:
                session = PaperSession(paper_cfg, source)
                session.submit(
                    decode(ResearchSignal, old_payload), price=event.price, quality="complete"
                )
                row = source.read(session.stream)[0]
                row["proposal"]["signal"] = old_payload
                row["signal_hash"] = canonical_hash(old_payload)
                journal.append(session.stream, f"signal:{signal.signal_id}", row)
            finally:
                source.close()
        before = journal.read(runtime.stream)
        restored = ResearchRuntime(config, journal)
        assert restored.paper is not None
        rows = journal.read(restored.paper.stream)
        assert len(rows) == 1
        assert rows[0]["proposal"]["signal"]["decision"]["engine_version"] == "legacy:p8a-v1"
        state = restored.paper.snapshot()
        again = ResearchRuntime(config, journal)
        assert again.paper is not None and again.paper.snapshot() == state
        assert journal.read(again.paper.stream) == rows
        assert journal.read(runtime.stream) == before
        assert restored.events() == (event,)
        # A new event can still be ingested after the upgrade.
        again.ingest(events()[1], completeness="complete")
        assert len(again.events()) == 2
    finally:
        journal.close()


def test_recorded_no_signal_never_submits_new_historical_interpretation(tmp_path: Path) -> None:
    paper_cfg = PaperSessionConfig(
        "paper-upgrade",
        "paper-account",
        Decimal("10000"),
        Decimal("0.05"),
        Decimal("0.1"),
        risk_policy_fixture(),
        Decimal("1"),
        Decimal("1"),
    )
    journal = SQLiteJournal(tmp_path / "empty.sqlite")
    try:
        runtime = ResearchRuntime(RuntimeConfig(pipeline_config(), "USD/oz", paper_cfg), journal)
        runtime.engine.signals = SignalEngine(_config())
        runtime.engine.signals.evaluate(**inputs())
        latest = runtime.engine.signals.snapshot().latest
        assert latest is not None
        event = replace(
            events()[0], event_time=latest.decision_time, received_at=latest.decision_time
        )
        runtime._paper_event(event, "complete", {"signals": {"latest": None}})
        assert runtime.paper is not None
        assert journal.read(runtime.paper.stream) == ()
    finally:
        journal.close()


def test_changed_recorded_signal_still_raises_identity_conflict(tmp_path: Path) -> None:
    paper_cfg = PaperSessionConfig(
        "paper-upgrade",
        "paper-account",
        Decimal("10000"),
        Decimal("0.05"),
        Decimal("0.1"),
        risk_policy_fixture(),
        Decimal("1"),
        Decimal("1"),
    )
    journal = SQLiteJournal(tmp_path / "conflict.sqlite")
    try:
        runtime = ResearchRuntime(RuntimeConfig(pipeline_config(), "USD/oz", paper_cfg), journal)
        event = events()[0]
        signal = SignalEngine(_config()).evaluate(**inputs()).latest
        assert signal is not None and runtime.paper is not None
        signal = replace(signal, decision_time=event.received_at)
        runtime.paper.submit(signal, price=event.price, quality="complete")
        changed = canonical_serialize(replace(signal, reasons=("Changed historical evidence",)))
        with pytest.raises(ValueError, match="signal_identity_conflict"):
            runtime._paper_event(event, "complete", {"signals": {"latest": changed}})
    finally:
        journal.close()
