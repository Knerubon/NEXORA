"""Process liveness only; no dependency readiness claims."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from nexora_api.quotes import QuoteService, Snapshot, configured_service
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

LOCAL_CLIENTS = {"127.0.0.1", "::1"}
LOCAL_ORIGINS = {
    f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (3000, 3100)
}


def create_app(service: QuoteService | None = None, *, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        feed = service or configured_service()
        application.state.quotes = feed
        if start_worker:
            feed.start()
        try:
            yield
        finally:
            if start_worker:
                await asyncio.to_thread(feed.stop)

    application = FastAPI(
        title="NEXORA", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @application.get("/health", response_model=Health)
    def health() -> Health:
        return Health()

    @application.get("/quotes", response_model=Snapshot)
    def quotes(request: Request, response: Response) -> Snapshot:
        if (
            request.client is None or request.client.host not in LOCAL_CLIENTS
            or request.headers.get("origin") not in (None, *LOCAL_ORIGINS)
        ):
            raise HTTPException(status_code=403, detail="Local access only")
        response.headers["Cache-Control"] = "no-store"
        feed: QuoteService = request.app.state.quotes
        return feed.snapshot()

    @application.websocket("/ws/quotes")
    async def stream(websocket: WebSocket) -> None:
        if (
            websocket.client is None or websocket.client.host not in LOCAL_CLIENTS
            or websocket.headers.get("origin") not in LOCAL_ORIGINS
        ):
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
                # Receive detects disconnect even while the worker has no new snapshot.
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                    if message["type"] == "websocket.disconnect":
                        break
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, OSError):
            pass

    return application


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    mode: Literal["research"] = "research"
    database: Literal["not_configured"] = "not_configured"
    broker: Literal["not_configured"] = "not_configured"
    engine: Literal["not_implemented"] = "not_implemented"


app = create_app()
