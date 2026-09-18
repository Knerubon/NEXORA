from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from nexora.adaptive_box import AdaptiveBoxConfig
from nexora.artifacts import canonical_hash, canonical_serialize, decode
from nexora.backtest import BacktestRunner, fixture_config
from nexora.backtest.datasets import load_dataset, manifest_for, save_dataset
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_data.quality import MarketDataQualityMonitor, QualityConfig
from nexora.market_regime import RegimeConfig
from nexora.paper import PaperInputError, PaperSimulator
from nexora.paper.session import PaperSession, PaperSessionConfig
from nexora.pnf import PnfConfig
from nexora.research import PipelineConfig, ResearchPipeline, ResolutionConfig
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.risk import RiskEngine, proposal_fixture, risk_policy_fixture
from nexora.signals import SignalConfig
from nexora.storage import SQLiteJournal


def pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        version="audit-v2",
        resolutions=tuple(
            ResolutionConfig(
                name,
                PnfConfig("XAUUSD", D(size), 2, 1, "close", f"{name}-v2"),
                AdaptiveBoxConfig(
                    "fixed",
                    D(size),
                    1,
                    f"{name}-v2",
                    atr_period=2,
                    min_box_size=D("0.1"),
                    max_box_size=D("5"),
                ),
            )
            for name, size in (("fast", "0.5"), ("medium", "1"), ("slow", "2"))
        ),
        structure_resolution="fast",
        regime=RegimeConfig("XAUUSD", 4, D("2"), D("8"), D("0.5"), "regime-v2"),
        signals=SignalConfig("XAUUSD", 1, 20, "signal-v2"),
        stale_after_events=100,
    )


def events() -> tuple[NormalizedPriceEvent, ...]:
    start = datetime(2026, 3, 1, 9, tzinfo=UTC)
    prices = (100, 104, 99, 106, 98, 108, 102, 110, 104, 112)
    return tuple(
        NormalizedPriceEvent(
            1,
            f"event:{i}",
            "recorded-test",
            "XAUUSD",
            "bar",
            start + timedelta(seconds=i * 10),
            start + timedelta(seconds=i * 10),
            i,
            i,
            f"event:{i}",
            "close",
            "USD/oz",
            1,
            D(price),
            close=D(price),
        )
        for i, price in enumerate(prices, 1)
    )


def test_risk_release_is_idempotent_and_keeps_other_reservations() -> None:
    risk = RiskEngine(risk_policy_fixture())
    risk.evaluate(proposal_fixture(proposal_id="a", requested_size="100"))
    risk.evaluate(proposal_fixture(proposal_id="b", requested_size="100"))
    risk.release("a", realized_pnl=D("-10"))
    risk.release("a", realized_pnl=D("-10"))
    assert risk.state().reserved_exposure == D("100")
    assert risk.state().daily_pnl == D("-10")
    with pytest.raises(ValueError, match="release_identity_conflict"):
        risk.release("a", realized_pnl=D("10"))


def test_risk_overnight_reservations_and_timezone() -> None:
    risk = RiskEngine(risk_policy_fixture())
    for name in ("a", "b"):
        risk.evaluate(proposal_fixture(proposal_id=name, requested_size="150"))
    p = proposal_fixture(proposal_id="c", requested_size="150")
    tomorrow = p.signal.decision_time + timedelta(days=1)
    p = replace(
        p,
        signal=replace(p.signal, decision_time=tomorrow),
        price=replace(p.price, observed_at=tomorrow),
        account=replace(p.account, observed_at=tomorrow),
    )
    assert risk.evaluate(p).reason == "exposure_limit"
    assert risk.state().reserved_exposure == D("300")
    local = RiskEngine(replace(risk_policy_fixture(), timezone="Asia/Bangkok"))
    when = datetime(2026, 3, 5, 18, tzinfo=UTC)
    local.evaluate(
        replace(
            p,
            requested_size=D("1"),
            signal=replace(p.signal, decision_time=when),
            account=replace(p.account, observed_at=when),
            price=replace(p.price, observed_at=when),
        )
    )
    assert local.state().trading_day == "2026-03-06"


@pytest.mark.parametrize("equity", ["0", "-1", "NaN", "Infinity"])
def test_risk_invalid_equity_fails_closed(equity: str) -> None:
    assert (
        RiskEngine(risk_policy_fixture()).evaluate(proposal_fixture(equity=equity)).action
        == "reject"
    )


def test_risk_rejects_old_inputs_and_price_symbol_mismatch() -> None:
    p = proposal_fixture()
    old = replace(
        p, account=replace(p.account, observed_at=p.account.observed_at - timedelta(days=3))
    )
    assert RiskEngine(risk_policy_fixture()).evaluate(old).action == "reject"
    wrong = replace(p, price=replace(p.price, symbol="EURUSD"))
    assert RiskEngine(risk_policy_fixture()).evaluate(wrong).action == "reject"


def test_paper_binding_and_approval_time() -> None:
    p = proposal_fixture()
    decision = RiskEngine(risk_policy_fixture()).evaluate(p)
    simulator = PaperSimulator(
        namespace="paper-test", starting_cash=D("10000"), fee_per_unit=D("0"), slippage=D("0")
    )
    for signal, when in (
        (replace(p.signal, symbol="EURUSD", side="short"), p.signal.decision_time),
        (p.signal, p.signal.decision_time - timedelta(seconds=1)),
        (p.signal, p.signal.decision_time + timedelta(seconds=11)),
    ):
        with pytest.raises(PaperInputError, match="approval"):
            simulator.apply_decision(
                decision=decision, signal=signal, market_price=D("100"), event_time=when
            )
    assert not simulator.fills()


def test_pipeline_restart_rebuild_and_duplicate(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "research.sqlite")
    config = RuntimeConfig(pipeline_config(), "USD/oz")
    runtime = ResearchRuntime(config, journal)
    stream = events()
    for event in stream[:5]:
        runtime.ingest(event, completeness="complete")
    restarted = ResearchRuntime(config, journal)
    assert runtime.snapshot() == restarted.snapshot()
    restarted.ingest(stream[0], completeness="complete")
    assert len(restarted.events()) == 5
    for event in stream[5:]:
        restarted.ingest(event, completeness="complete")
    independent = ResearchPipeline(config.pipeline)
    for event in stream:
        independent.process(event)
    assert restarted.snapshot()["output"] == independent.snapshot()
    assert decode(RuntimeConfig, canonical_serialize(config)) == config
    journal.close()


def test_real_prices_delay_partitions_and_run_identity(tmp_path: Path) -> None:
    stream = events()
    dataset = manifest_for(stream, quality="complete")
    p = proposal_fixture()
    signal = replace(
        p.signal,
        decision_time=stream[0].received_at,
        confirmation_time=stream[0].received_at,
        occurrence_time=stream[0].event_time,
    )
    cfg = fixture_config("fixed_pnf")
    run = BacktestRunner().run(
        dataset=dataset,
        config=cfg,
        expected_dataset_hash=canonical_hash(dataset),
        signals=(signal,),
        events=stream,
    )
    # Decision at 10s, first eligible entry at 20s (104), first exit at 30s (99).
    assert run.trades[0].entry_price == D("104")
    assert run.trades[0].exit_price == D("99")
    assert run.trades[0].gross_pnl == D("-5")
    assert run.metrics.average_entry_delay_seconds == D("10")
    empty = BacktestRunner().run(
        dataset=dataset,
        config=cfg,
        expected_dataset_hash=canonical_hash(dataset),
        signals=(),
        events=stream,
    )
    assert empty.run_id != run.run_id
    with pytest.raises(ValueError, match="partitions"):
        missing = replace(dataset, partitions=())
        BacktestRunner().run(
            dataset=missing,
            config=cfg,
            expected_dataset_hash=canonical_hash(missing),
            signals=(signal,),
            events=stream,
        )
    save_dataset(tmp_path / "dataset", dataset, stream)
    assert load_dataset(tmp_path / "dataset") == (dataset, stream)
    file = tmp_path / "dataset" / "normalized.json"
    file.write_text(file.read_text().replace('"price": "100"', '"price": "999"'))
    with pytest.raises(ValueError, match="hash"):
        load_dataset(tmp_path / "dataset")


def test_strategy_modes_use_actual_pipeline() -> None:
    stream = events()
    dataset = manifest_for(stream, quality="complete")
    for mode in ("fixed_pnf", "adaptive_pnf", "baseline"):
        cfg = replace(fixture_config(mode), pipeline=pipeline_config())
        run = BacktestRunner().run(
            dataset=dataset,
            config=cfg,
            expected_dataset_hash=canonical_hash(dataset),
            events=stream,
        )
        assert "shared_pipeline" in run.notes
        assert all(trade.entry_price in {e.price for e in stream} for trade in run.trades)


def test_quality_and_history_do_not_claim_complete_capture() -> None:
    quality = MarketDataQualityMonitor(QualityConfig("XAUUSD", history_limit=4))
    for _ in range(20):
        snapshot = quality.observe_transport(status="live", code="ok")
    assert snapshot.completeness == "unknown"
    assert len(quality.history(limit=100)) == 4
    event = replace(events()[0], is_gap=True)
    snapshot = quality.observe_event(event, observed_at=event.received_at)
    assert snapshot.completeness == "partial"


def test_durable_paper_session_restart_and_backup(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "paper.sqlite")
    cfg = PaperSessionConfig(
        "paper-test",
        "paper-account",
        D("10000"),
        D("0.05"),
        D("0.1"),
        risk_policy_fixture(),
        D("1"),
        D("1"),
    )
    session = PaperSession(cfg, journal)
    signal = proposal_fixture().signal
    session.submit(signal, price=D("100"), quality="complete")
    assert session.risk.state().reserved_exposure == D("1")
    restored = PaperSession(cfg, journal)
    restored.submit(signal, price=D("100"), quality="complete")
    assert len(restored.simulator.fills()) == 1
    assert restored.snapshot() == session.snapshot()
    journal.backup(tmp_path / "backup.sqlite")
    recovered_journal = SQLiteJournal(tmp_path / "backup.sqlite")
    assert PaperSession(cfg, recovered_journal).snapshot() == session.snapshot()
    recovered_journal.close()
    journal.close()


def test_journal_conflicting_identity_and_concurrent_writer(tmp_path: Path) -> None:
    a, b = SQLiteJournal(tmp_path / "journal.sqlite"), SQLiteJournal(tmp_path / "journal.sqlite")
    a.append("stream", "one", {"value": 1}, expected_count=0)
    with pytest.raises(ValueError, match="concurrent"):
        b.append("stream", "two", {"value": 2}, expected_count=0)
    with pytest.raises(ValueError, match="identity"):
        a.append("stream", "one", {"value": 3})
    assert a.read("stream") == ({"value": 1},)
    a.close()
    b.close()


def test_causal_signal_prefix_and_baseline_close_contract() -> None:
    stream = events()
    cfg = replace(fixture_config("fixed_pnf"), pipeline=pipeline_config())
    full = BacktestRunner.generate_signals(stream, cfg)
    assert full
    for index in range(1, len(stream) + 1):
        prefix = BacktestRunner.generate_signals(stream[:index], cfg)
        assert prefix == tuple(s for s in full if s.decision_time <= stream[index - 1].received_at)
    baseline = BacktestRunner.generate_signals(stream, fixture_config("baseline"))
    assert baseline and {s.signal_id for s in baseline}.isdisjoint(s.signal_id for s in full)
    with pytest.raises(ValueError, match="completed_bars"):
        BacktestRunner.generate_signals(
            (replace(stream[0], price_source="bid"),), fixture_config("baseline")
        )


def test_paper_storage_failure_rolls_back_and_kill_is_latched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SQLiteJournal(tmp_path / "paper.sqlite")
    cfg = PaperSessionConfig(
        "paper-test",
        "paper-account",
        D("10000"),
        D("0.05"),
        D("0.1"),
        risk_policy_fixture(),
        D("1"),
        D("1"),
    )
    session = PaperSession(cfg, journal)
    original = session.snapshot()
    append = journal.append

    def fail(*args: object, **kwargs: object) -> bool:
        raise OSError("simulated storage failure")

    monkeypatch.setattr(journal, "append", fail)
    with pytest.raises(OSError):
        session.submit(proposal_fixture().signal, price=D("100"), quality="complete")
    assert session.snapshot() == original
    monkeypatch.setattr(journal, "append", append)
    session.control("kill")
    with pytest.raises(ValueError, match="latched"):
        session.control("resume")
    assert PaperSession(cfg, journal).snapshot()["status"] == "kill_switch"
    journal.close()


def test_api_recorded_runs_reconnect_and_storage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from nexora_api.main import create_app
    from nexora_api.quotes import Quote, QuoteService

    journal = SQLiteJournal(tmp_path / "api.sqlite")
    runtime = ResearchRuntime(RuntimeConfig(pipeline_config(), "USD/oz"), journal)
    for event in events():
        runtime.ingest(event, completeness="complete")

    class OfflineSource:
        def read(self) -> Quote:
            raise RuntimeError("offline test")

        def close(self) -> None:
            pass

    app = create_app(
        QuoteService(OfflineSource(), "XAUUSD"),
        start_worker=False,
        journal=journal,
        runtime=runtime,
        backtest_configs={"baseline": fixture_config("baseline")},
    )
    origin = {"origin": "http://localhost:3000"}
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/backtest/runs", json={"parameter_set": "baseline"}).status_code == 403
        one = client.post("/backtest/runs", json={"parameter_set": "baseline"}, headers=origin)
        assert one.status_code == 200
        two = client.post("/backtest/runs", json={"parameter_set": "baseline"}, headers=origin)
        assert one.json() == two.json()
        run_id = one.json()["run_id"]
        assert len(client.get("/backtest/runs").json()["runs"]) == 1
        assert (
            client.get("/backtest/compare", params={"run_ids": run_id}).json()["runs"][0]["run_id"]
            == run_id
        )
        for _ in range(2):
            with client.websocket_connect("/ws/events", headers=origin) as ws:
                snapshot = ws.receive_json()
                assert snapshot["payload"]["research"]["event_count"] == len(events())
                assert snapshot["payload"]["research_mode"] == "recorded_or_unavailable"

        def fail_read(stream: str) -> tuple[dict[str, object], ...]:
            raise OSError("simulated unavailable storage")

        monkeypatch.setattr(journal, "read", fail_read)
        result = client.get("/operations/readiness")
        assert result.status_code == 200
        assert "storage_unavailable" in result.json()["reasons"]
    journal.close()


def test_paused_paper_does_not_leak_risk_reservation(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "paused.sqlite")
    cfg = PaperSessionConfig(
        "paper-paused",
        "paper-account",
        D("10000"),
        D("0"),
        D("0"),
        risk_policy_fixture(),
        D("1"),
        D("1"),
    )
    session = PaperSession(cfg, journal)
    session.control("pause")
    session.submit(proposal_fixture().signal, price=D("100"), quality="complete")
    assert not session.simulator.fills()
    assert session.risk.state().reserved_exposure == 0
    assert PaperSession(cfg, journal).snapshot() == session.snapshot()
    journal.close()


def test_quote_time_provenance_survives_journal_restart(tmp_path: Path) -> None:
    from nexora_api.quotes import make_quote
    from nexora_api.research import observe_quote

    cfg = pipeline_config()
    cfg = replace(
        cfg,
        resolutions=tuple(
            replace(r, pnf=replace(r.pnf, price_source="bid")) for r in cfg.resolutions
        ),
    )
    journal = SQLiteJournal(tmp_path / "quote.sqlite")
    config = RuntimeConfig(cfg, "USD/oz")
    runtime = ResearchRuntime(config, journal)
    now = datetime(2026, 9, 18, 10, tzinfo=UTC)
    quote = make_quote(
        "XAUUSD",
        100,
        101,
        1,
        int((now + timedelta(hours=3)).timestamp() * 1000),
        now,
        time_offset_seconds=10800,
    )
    observe_quote(runtime, quote)
    observe_quote(runtime, quote)
    assert len(runtime.events()) == 1
    event = runtime.events()[0]
    assert event.event_time == now
    assert "offset=10800" in event.source_event_id
    assert "13:00:00" in event.source_event_id
    assert ResearchRuntime(config, journal).events() == runtime.events()
    journal.close()
