from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]


def test_version() -> None:
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json()["service"] == "FDEX Server"
