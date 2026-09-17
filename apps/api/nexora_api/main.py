"""Process liveness only; no dependency readiness claims."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI(title="NEXORA", version="0.1.0", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    mode: Literal["research"] = "research"
    database: Literal["not_configured"] = "not_configured"
    broker: Literal["not_configured"] = "not_configured"
    engine: Literal["not_implemented"] = "not_implemented"


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health()
