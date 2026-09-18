from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from nexora_api.main import create_app
from nexora_api.quotes import FeedError, Quote, QuoteService


class _StaticSource:
    def __init__(self) -> None:
        self._calls = 0

    def read(self) -> Quote:
        self._calls += 1
        now = datetime(2026, 3, 3, 10, 0, self._calls, tzinfo=UTC)
        return Quote(
            symbol="XAUUSD",
            bid="2000.1",
            ask="2000.3",
            spread="0.2",
            digits=1,
            event_time=now,
            received_at=now,
        )

    def close(self) -> None:
        return


class _FailingSource:
    def read(self) -> Quote:
        raise FeedError("terminal_not_running", "disconnected")

    def close(self) -> None:
        return


def test_dashboard_state_config_quality_and_history_endpoints() -> None:
    service = QuoteService(_StaticSource(), "XAUUSD")
    service.poll(now=datetime(2026, 3, 3, 10, 0, 1, tzinfo=UTC))
    service.poll(now=datetime(2026, 3, 3, 10, 0, 2, tzinfo=UTC))
    app = create_app(service, start_worker=False)

    with TestClient(app, base_url="http://localhost") as client:
        state = client.get("/state", headers={"origin": "http://localhost:3000"})
        config = client.get("/config", headers={"origin": "http://localhost:3000"})
        quality = client.get("/quality", headers={"origin": "http://localhost:3000"})
        history = client.get("/history?limit=2", headers={"origin": "http://localhost:3000"})

    assert state.status_code == 200
    assert config.status_code == 200
    assert quality.status_code == 200
    assert history.status_code == 200
    assert state.json()["backtest_lab_status"] == "pending_p10"
    assert config.json()["local_only"] is True
    assert len(history.json()["quote_history"]) == 2
    assert len(history.json()["quality_history"]) == 2


def test_dashboard_blocks_non_local_origin() -> None:
    service = QuoteService(_StaticSource(), "XAUUSD")
    app = create_app(service, start_worker=False)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/state", headers={"origin": "http://evil.example"})
    assert response.status_code == 403


def test_dashboard_event_stream_emits_quote_and_quality_snapshots() -> None:
    service = QuoteService(_FailingSource(), "XAUUSD")
    service.poll(now=datetime(2026, 3, 3, 10, 0, 1, tzinfo=UTC))
    app = create_app(service, start_worker=False)

    with TestClient(app, base_url="http://localhost") as client:
        with client.websocket_connect(
            "/ws/events",
            headers={"origin": "http://localhost:3000"},
        ) as ws:
            quote_event = ws.receive_json()
            quality_event = ws.receive_json()

    assert quote_event["event_type"] == "quote_snapshot"
    assert quality_event["event_type"] == "quality_snapshot"
    assert quality_event["payload"]["status"] in {"disconnected", "error", "unavailable", "unknown"}
