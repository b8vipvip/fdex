from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


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


def test_public_config_never_exposes_api_key() -> None:
    response = client.get("/api/public-config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["public_base_url"]
    assert "api_key" not in payload
    assert "ai_base_url" not in payload


def test_default_server_port_is_isolated() -> None:
    assert get_settings().fdex_host == "127.0.0.1"
    assert get_settings().fdex_port == 18080
