"""Local-only API backed by actual research state and durable artifacts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.backtest import BacktestLabService
from nexora.backtest.datasets import manifest_for
from nexora.backtest.models import BacktestConfig
from nexora.experience import ExperienceRepository
from nexora.research.runtime import ResearchRuntime
from nexora.storage import Journal
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nexora_api.environment import Environment
from nexora_api.quotes import QuoteService, configured_service
from nexora_api.research import (
    configured_backtests,
    configured_journal,
    configured_runtime,
    observe_quote,
)

LOCAL_CLIENTS = {"127.0.0.1", "::1", "testclient"}


def _local_origins() -> set[str]:
    port = Environment.resolve().web_port
    return {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost")}


def _local_host(request: Request | WebSocket) -> str | None:
    host = request.headers.get("host")
    if not host:
        client = getattr(request, "client", None)
        if client is not None:
            return str(client.host)
        return None
    return host.split(":", 1)[0]


def _is_local_origin(headers: Any) -> bool:
    origin = headers.get("origin")
    if origin is not None:
        return origin in _local_origins()
    host = headers.get("host")
    if host:
        hostname = host.split(":", 1)[0]
        return hostname in {"127.0.0.1", "localhost", "testserver"}
    return False


def _assert_local_http(request: Request, *, mutation: bool = False) -> None:
    if (
        request.client is None
        or request.client.host not in LOCAL_CLIENTS
        or not _is_local_origin(request.headers)
    ):
        raise HTTPException(status_code=403, detail="Local access only")
    if mutation and (
        request.headers.get("origin") is None or not _is_local_origin(request.headers)
    ):
        raise HTTPException(status_code=403, detail="Local access only")


def _is_local_ws(ws: WebSocket) -> bool:
    return (
        ws.client is not None and ws.client.host in LOCAL_CLIENTS and _is_local_origin(ws.headers)
    )


def create_app(
    service: QuoteService | None = None,
    *,
    start_worker: bool = True,
    journal: Journal | None = None,
    runtime: ResearchRuntime | None = None,
    backtest_configs: dict[str, BacktestConfig] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store = journal or configured_journal()
        engine = runtime or configured_runtime(store)
        feed = service or configured_service()
        application.state.quotes = feed
        application.state.journal = store
        application.state.experiences = ExperienceRepository(store)
        application.state.runtime = engine
        application.state.backtest = BacktestLabService.bootstrap(store, engine)
        application.state.configs = (
            backtest_configs if backtest_configs is not None else configured_backtests()
        )
        if engine is not None:
            feed.on_quote = lambda quote: observe_quote(
                engine,
                quote,
                metadata={
                    "quality": canonical_serialize(feed.quality_snapshot()),
                    "status": feed.snapshot().model_dump(mode="json"),
                },
            )
        if start_worker:
            feed.start()
        application.state.published = await asyncio.to_thread(state_payload)
        application.state.publication = 0

        async def publish_state() -> None:
            while True:
                # One shared, sequential job. Never hold the ASGI event loop or
                # quote delivery behind research locks, serialization or storage.
                await asyncio.sleep(1)
                pending = asyncio.create_task(asyncio.to_thread(state_payload))
                try:
                    application.state.published = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    # A cancelled to_thread await does not stop its worker.
                    # Finish that single read before closing the journal.
                    await pending
                    raise
                application.state.publication += 1

        publisher = asyncio.create_task(publish_state())
        try:
            yield
        finally:
            publisher.cancel()
            try:
                await publisher
            except asyncio.CancelledError:
                pass
            if start_worker:
                await asyncio.to_thread(feed.stop)
            feed.on_quote = None
            if journal is None:
                store.close()

    app = FastAPI(title="NEXORA", version="0.3.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(_local_origins()),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def no_cache(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "research",
            "readiness": "/operations/readiness",
            "environment": Environment.resolve().name,
        }

    def state_payload() -> dict[str, Any]:
        feed: QuoteService = app.state.quotes
        engine: ResearchRuntime | None = app.state.runtime
        lab: BacktestLabService = app.state.backtest
        quote = feed.snapshot()
        research = engine.snapshot() if engine else {"output": {}, "error": None, "event_count": 0}
        output = research["output"]
        observed = output.get("event", {}).get("received_at")
        age = (
            (datetime.now(UTC) - datetime.fromisoformat(observed)).total_seconds()
            if observed
            else None
        )
        fresh = (
            quote.status == "live" and age is not None and 0 <= age <= 10 and not research["error"]
        )
        matrix = output.get("matrix", {})
        structure = output.get("structure", {})
        regime = output.get("regime", {}).get("state", {})
        try:
            backtest_status = "ready" if lab.list_runs() else "empty"
            app.state.journal.read("readiness-probe")
            storage_available = True
        except Exception:
            backtest_status = "unavailable"
            storage_available = False
            fresh = False
        return {
            "schema_version": 2,
            "sequence": quote.sequence,
            "quote": quote.model_dump(mode="json"),
            "quality": canonical_serialize(feed.quality_snapshot()),
            "matrix_status": "ready"
            if fresh and matrix.get("alignment") not in (None, "unavailable")
            else "unavailable",
            "structure_status": "ready" if fresh and structure.get("pivots") else "unavailable",
            "regime_status": "ready"
            if fresh and regime.get("label") not in (None, "unknown")
            else "unavailable",
            "signals_status": "ready" if fresh and output.get("signals") else "unavailable",
            "backtest_lab_status": backtest_status,
            "paper_trading_status": lab.paper_snapshot()["status"],
            "research": research,
            "research_mode": "live_observation" if fresh else "recorded_or_unavailable",
            "storage_backend": app.state.journal.backend,
            "storage_available": storage_available,
        }

    @app.get("/config")
    def config(request: Request) -> dict[str, Any]:
        _assert_local_http(request)
        engine: ResearchRuntime | None = app.state.runtime
        return {
            "local_only": True,
            "environment": Environment.resolve().name,
            "symbol": app.state.quotes.snapshot().symbol,
            "websocket_channels": ["quotes", "events"],
            "pipeline": canonical_serialize(engine.config) if engine else None,
            "parameter_sets": canonical_serialize(app.state.configs),
        }

    @app.get("/state")
    def state(request: Request) -> dict[str, Any]:
        _assert_local_http(request)
        return state_payload()

    @app.get("/quotes")
    def quotes(request: Request) -> Any:
        _assert_local_http(request)
        return app.state.quotes.snapshot()

    @app.get("/quality")
    def quality(request: Request) -> Any:
        _assert_local_http(request)
        return app.state.quotes.quality_snapshot()

    @app.get("/history")
    def history(request: Request, limit: int = 60) -> dict[str, Any]:
        _assert_local_http(request)
        bounded = max(1, min(limit, 240))
        engine: ResearchRuntime | None = app.state.runtime
        output = engine.snapshot()["output"] if engine else {}
        return {
            "schema_version": 2,
            "quote_history": app.state.quotes.history(limit=bounded),
            "quality_history": app.state.quotes.quality_monitor.history(limit=bounded),
            "transitions": output.get("transitions", [])[-bounded:],
            "signals": output.get("signals", {}).get("history", [])[-bounded:],
        }

    @app.get("/backtest/runs")
    def backtest_runs(request: Request) -> dict[str, Any]:
        _assert_local_http(request)
        return {"schema_version": 2, "runs": app.state.backtest.list_runs()}

    @app.get("/experiences/summary")
    def experience_summary(request: Request) -> dict[str, Any]:
        _assert_local_http(request)
        repository: ExperienceRepository = app.state.experiences
        return {"schema_version": 1, "hypothetical_only": True, **repository.summary()}

    @app.get("/experiences")
    def recent_experiences(
        request: Request,
        limit: int = Query(20, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        _assert_local_http(request)
        repository: ExperienceRepository = app.state.experiences
        return {
            "schema_version": 1,
            "limit": limit,
            "offset": offset,
            "experiences": canonical_serialize(repository.recent(limit=limit, offset=offset)),
        }

    @app.get("/experiences/{experience_id}")
    def experience_detail(request: Request, experience_id: str) -> dict[str, Any]:
        _assert_local_http(request)
        repository: ExperienceRepository = app.state.experiences
        result = repository.detail(experience_id)
        if result is None:
            raise HTTPException(404, "Experience not found")
        return result

    @app.get("/experiences/{experience_id}/outcomes")
    def experience_outcomes(
        request: Request,
        experience_id: str,
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        _assert_local_http(request)
        repository: ExperienceRepository = app.state.experiences
        if repository.get(experience_id) is None:
            raise HTTPException(404, "Experience not found")
        return {
            "schema_version": 1,
            "experience_id": experience_id,
            "outcomes": repository.outcomes(experience_id),
            "raw_observations": repository.raw_observations(
                experience_id,
                limit=limit,
                offset=offset,
            ),
            "raw_limit": limit,
            "raw_offset": offset,
        }

    @app.post("/backtest/runs")
    def run_backtest(request: Request, payload: dict[str, str]) -> dict[str, Any]:
        _assert_local_http(request, mutation=True)
        engine: ResearchRuntime | None = app.state.runtime
        cfg = app.state.configs.get(payload.get("parameter_set", ""))
        if engine is None or cfg is None or not engine.events():
            raise HTTPException(409, "Configured research data and parameter set required")
        events = engine.events()
        # Quote observation is partial, not a claim of lossless market capture.
        dataset = manifest_for(events, quality="partial")
        try:
            run = app.state.backtest.runner.run(
                dataset=dataset,
                config=cfg,
                events=events,
                expected_dataset_hash=canonical_hash(dataset),
            )
            app.state.backtest.store.append(run)
        except ValueError:
            raise HTTPException(422, "Invalid dataset or research configuration") from None
        return cast(dict[str, Any], canonical_serialize(run))

    @app.get("/backtest/compare")
    def compare(request: Request, run_ids: str = "") -> dict[str, Any]:
        _assert_local_http(request)
        return {"runs": app.state.backtest.compare(tuple(run_ids.split(",")))}

    @app.get("/risk/replay")
    def risk(request: Request) -> Any:
        _assert_local_http(request)
        return app.state.backtest.risk_replay()

    @app.get("/paper/replay")
    def paper(request: Request) -> Any:
        _assert_local_http(request)
        return app.state.backtest.paper_replay()

    @app.post("/paper/control")
    def paper_control(request: Request, payload: dict[str, str]) -> Any:
        _assert_local_http(request, mutation=True)
        engine: ResearchRuntime | None = app.state.runtime
        if engine is None or engine.paper is None:
            raise HTTPException(409, "Paper not configured")
        try:
            engine.paper.control(payload.get("action", ""))
        except ValueError:
            raise HTTPException(422, "Invalid paper control") from None
        return engine.paper.snapshot()

    def readiness_payload() -> dict[str, Any]:
        payload = state_payload()
        reasons = []
        if payload["research"]["event_count"] == 0:
            reasons.append("research_not_initialized")
        if payload["quote"]["status"] != "live":
            reasons.append("feed_not_live")
        if payload["research_mode"] != "live_observation":
            reasons.append("research_not_fresh")
        if payload["research"]["error"]:
            reasons.append(payload["research"]["error"])
        if not payload["storage_available"]:
            reasons.append("storage_unavailable")
        if payload["storage_backend"] != "postgresql":
            reasons.append("postgres_not_configured")
        return {
            "status": "degraded" if reasons else "ready",
            "reasons": reasons,
            "scope": "local_observation",
            "remote_access": "disabled",
            "production_hardening": "unverified",
        }

    @app.get("/operations/readiness")
    def readiness(request: Request) -> dict[str, Any]:
        _assert_local_http(request)
        return readiness_payload()

    @app.get("/operations/alerts")
    def alerts(request: Request) -> dict[str, Any]:
        _assert_local_http(request)
        value = readiness_payload()
        return {"alerts": [{"code": reason, "severity": "warning"} for reason in value["reasons"]]}

    async def stream(ws: WebSocket, quotes_only: bool) -> None:
        if not _is_local_ws(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        publication = -1
        app.state.active_sockets = getattr(app.state, "active_sockets", 0) + 1
        try:
            while True:
                if not quotes_only and publication != app.state.publication:
                    payload = app.state.published
                    publication = app.state.publication
                    if payload is not None:
                        # Full snapshots recover from reconnects and missed deltas.
                        await asyncio.wait_for(
                            ws.send_json(
                                {
                                    "schema_version": 2,
                                    "event_type": "state_snapshot",
                                    "sequence": payload["sequence"],
                                    "stream_id": payload["quote"]["stream_id"],
                                    "payload": payload,
                                }
                            ),
                            timeout=5,
                        )
                # A lightweight heartbeat carries the current observation,
                # including unchanged/stale event_time; it is not a market tick.
                quote = app.state.quotes.snapshot().model_dump(mode="json")
                if quotes_only:
                    await asyncio.wait_for(ws.send_json(quote), timeout=5)
                else:
                    await asyncio.wait_for(
                        ws.send_json(
                            {
                                "schema_version": 2,
                                "event_type": "quote_snapshot",
                                "sequence": quote["sequence"],
                                "stream_id": quote["stream_id"],
                                "payload": quote,
                            }
                        ),
                        timeout=5,
                    )
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=0.25)
                    if message["type"] == "websocket.disconnect":
                        break
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, OSError, TimeoutError):
            return
        finally:
            app.state.active_sockets -= 1

    @app.websocket("/ws/events")
    async def events(ws: WebSocket) -> None:
        await stream(ws, False)

    @app.websocket("/ws/quotes")
    async def quote_stream(ws: WebSocket) -> None:
        await stream(ws, True)

    return app


app = create_app()
