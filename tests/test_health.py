from fastapi.testclient import TestClient
from nexora_api.main import app


def test_health_reports_liveness_without_claiming_readiness() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "research",
        "database": "not_configured",
        "broker": "not_configured",
        "engine": "not_implemented",
    }


def test_untrusted_host_is_rejected() -> None:
    with TestClient(app, base_url="http://untrusted.example") as client:
        assert client.get("/health").status_code == 400


def test_health_is_read_only_and_no_order_route_exists() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/health").status_code == 405
        assert client.post("/orders", json={}).status_code == 404
