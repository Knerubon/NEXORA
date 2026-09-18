"""Local-only research API for realtime observation and state views."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from nexora.backtest import BacktestLabService
from nexora.market_data import QualitySnapshot
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nexora_api.quotes import QuoteService, Snapshot, configured_service

LOCAL_CLIENTS = {"127.0.0.1", "::1", "testclient"}
LOCAL_ORIGINS = {
    f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (3000, 3100)
}


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    mode: Literal["research"] = "research"
    database: Literal["not_configured"] = "not_configured"
    broker: Literal["not_configured"] = "not_configured"
    engine: Literal["not_implemented"] = "not_implemented"


class DashboardConfig(BaseModel):
    symbol: str | None
    source: Literal["MT5"] = "MT5"
    local_only: Literal[True] = True
    websocket_channels: tuple[str, ...] = ("quotes", "events")


class DashboardState(BaseModel):
    schema_version: Literal[1] = 1
    sequence: int
    quote: Snapshot
    quality: QualitySnapshot
    matrix_status: Literal["ready", "unavailable"] = "ready"
    structure_status: Literal["ready", "unavailable"] = "ready"
    regime_status: Literal["ready", "unavailable"] = "ready"
    signals_status: Literal["ready", "unavailable"] = "ready"
    backtest_lab_status: Literal["ready", "pending_p10"] = "pending_p10"


class DashboardHistory(BaseModel):
    schema_version: Literal[1] = 1
    quote_history: tuple[Snapshot, ...]
    quality_history: tuple[QualitySnapshot, ...]


class EventEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    sequence: int
    event_type: Literal["quote_snapshot", "quality_snapshot"]
    payload: dict[str, object]


def create_app(service: QuoteService | None = None, *, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        feed = service or configured_service()
        application.state.quotes = feed
        application.state.backtest = BacktestLabService.bootstrap()
        if start_worker:
            feed.start()
        try:
            yield
        finally:
            if start_worker:
                await asyncio.to_thread(feed.stop)

    application = FastAPI(
        title="NEXORA",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(LOCAL_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @application.get("/health", response_model=Health)
    def health() -> Health:
        return Health()

    @application.get("/config", response_model=DashboardConfig)
    def config(request: Request) -> DashboardConfig:
        _assert_local_http(request)
        feed: QuoteService = request.app.state.quotes
        return DashboardConfig(symbol=feed.snapshot().symbol)

    @application.get("/quotes", response_model=Snapshot)
    def quotes(request: Request, response: Response) -> Snapshot:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        feed: QuoteService = request.app.state.quotes
        return feed.snapshot()

    @application.get("/quality", response_model=QualitySnapshot)
    def quality(request: Request, response: Response) -> QualitySnapshot:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        feed: QuoteService = request.app.state.quotes
        return feed.quality_snapshot()

    @application.get("/state", response_model=DashboardState)
    def state(request: Request, response: Response) -> DashboardState:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        feed: QuoteService = request.app.state.quotes
        snapshot = feed.snapshot()
        return DashboardState(
            sequence=snapshot.sequence,
            quote=snapshot,
            quality=feed.quality_snapshot(),
            backtest_lab_status="ready",
        )

    @application.get("/history", response_model=DashboardHistory)
    def history(request: Request, response: Response, limit: int = 60) -> DashboardHistory:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        bounded_limit = max(1, min(limit, 240))
        feed: QuoteService = request.app.state.quotes
        return DashboardHistory(
            quote_history=feed.history(limit=bounded_limit),
            quality_history=feed.quality_monitor.history(limit=bounded_limit),
        )

    @application.get("/backtest/runs")
    def backtest_runs(request: Request, response: Response) -> dict[str, object]:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        backtest: BacktestLabService = request.app.state.backtest
        runs = backtest.list_runs()
        return {"schema_version": 1, "runs": runs}

    @application.get("/backtest/compare")
    def backtest_compare(request: Request, response: Response, run_ids: str) -> dict[str, object]:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        selected = tuple(item.strip() for item in run_ids.split(",") if item.strip())
        backtest: BacktestLabService = request.app.state.backtest
        compared = backtest.compare(selected)
        return {"schema_version": 1, "runs": compared}

    @application.get("/risk/replay")
    def risk_replay(request: Request, response: Response) -> dict[str, object]:
        _assert_local_http(request)
        response.headers["Cache-Control"] = "no-store"
        backtest: BacktestLabService = request.app.state.backtest
        return {"schema_version": 1, **backtest.risk_replay()}

    @application.websocket("/ws/quotes")
    async def stream_quotes(websocket: WebSocket) -> None:
        if not _is_local_ws(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        feed: QuoteService = websocket.app.state.quotes
        last_sequence = -1
        try:
            while True:
                snapshot = feed.snapshot()
                if snapshot.sequence != last_sequence:
                    await websocket.send_json(snapshot.model_dump(mode="json"))
                    last_sequence = snapshot.sequence
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                    if message["type"] == "websocket.disconnect":
                        break
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, OSError):
            pass

    @application.websocket("/ws/events")
    async def stream_events(websocket: WebSocket) -> None:
        if not _is_local_ws(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        feed: QuoteService = websocket.app.state.quotes
        last_sequence = -1
        try:
            while True:
                snapshot = feed.snapshot()
                if snapshot.sequence != last_sequence:
                    quality_snapshot = feed.quality_snapshot()
                    quote_event = EventEnvelope(
                        sequence=snapshot.sequence,
                        event_type="quote_snapshot",
                        payload=snapshot.model_dump(mode="json"),
                    )
                    quality_event = EventEnvelope(
                        sequence=quality_snapshot.sequence,
                        event_type="quality_snapshot",
                        payload=asdict(quality_snapshot),
                    )
                    await websocket.send_json(quote_event.model_dump(mode="json"))
                    await websocket.send_json(quality_event.model_dump(mode="json"))
                    last_sequence = snapshot.sequence
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                    if message["type"] == "websocket.disconnect":
                        break
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, OSError):
            pass

    return application


def _is_local_http(request: Request) -> bool:
    return (
        request.client is not None
        and request.client.host in LOCAL_CLIENTS
        and request.headers.get("origin") in (None, *LOCAL_ORIGINS)
    )


def _assert_local_http(request: Request) -> None:
    if not _is_local_http(request):
        raise HTTPException(status_code=403, detail="Local access only")


def _is_local_ws(websocket: WebSocket) -> bool:
    return (
        websocket.client is not None
        and websocket.client.host in LOCAL_CLIENTS
        and websocket.headers.get("origin") in LOCAL_ORIGINS
    )


app = create_app()
