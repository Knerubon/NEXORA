from pathlib import Path

from fastapi.testclient import TestClient
from nexora.experience import ExperienceRepository
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal
from nexora_api.main import create_app
from nexora_api.quotes import QuoteService

from tests.test_dashboard_api import _FailingSource
from tests.test_experience import event
from tests.test_readiness_regressions import pipeline_config


def test_experience_reads_pagination_summary_and_guards(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "api.sqlite")
    runtime = ResearchRuntime(RuntimeConfig(pipeline_config(), "USD/oz"), journal)
    app = create_app(
        QuoteService(_FailingSource(), "XAUUSD"),
        start_worker=False,
        journal=journal,
        runtime=runtime,
    )
    try:
        with TestClient(app, base_url="http://localhost") as client:
            assert client.get("/experiences").json()["experiences"] == []
            assert client.get("/experiences/summary").json()["total"] == 0
            assert client.get("/experiences/missing").status_code == 404
            assert client.get("/experiences/missing/outcomes").status_code == 404
            assert client.get("/experiences?limit=201").status_code == 422
            assert client.get("/experiences?offset=-1").status_code == 422
            runtime.ingest(event(0))
            runtime.ingest(event(60))
            recent = client.get("/experiences?limit=1").json()["experiences"]
            assert len(recent) == 1
            eid = ExperienceRepository(journal).all()[0].experience_id
            detail = client.get(f"/experiences/{eid}").json()
            assert detail["context"]["decision"]["action"] == "WAIT"
            outcomes = client.get(f"/experiences/{eid}/outcomes?limit=1").json()
            assert len(outcomes["outcomes"]) == 4
            assert len(outcomes["raw_observations"]) == 1
            summary = client.get("/experiences/summary").json()
            assert summary["total"] == 1 and summary["completed"] == 1
            assert summary["by_action"] == {"BUY": 0, "SELL": 0, "WAIT": 1}
            for route in (
                "/experiences",
                "/experiences/summary",
                f"/experiences/{eid}",
                f"/experiences/{eid}/outcomes",
            ):
                assert (
                    client.get(route, headers={"origin": "https://untrusted.invalid"}).status_code
                    == 403
                )
                assert client.post(route, json={}).status_code == 405
        with TestClient(app, base_url="http://localhost", client=("203.0.113.10", 1234)) as remote:
            assert remote.get("/experiences").status_code == 403
        # Historical records are queryable even with no active runtime/config.
        read_only = create_app(
            QuoteService(_FailingSource(), "XAUUSD"),
            start_worker=False,
            journal=journal,
        )
        with TestClient(read_only, base_url="http://localhost") as client:
            assert client.get("/experiences/summary").json()["completed"] == 1
    finally:
        journal.close()
