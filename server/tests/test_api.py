import json
import os
import re
from pathlib import Path

import pytest

os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "test-password-12345"
os.environ["ADMIN_SESSION_SECRET"] = "test-session-secret-that-is-longer-than-32-characters"
os.environ["ADMIN_COOKIE_SECURE"] = "false"
os.environ["APP_DIR"] = "/tmp/fdex-test"
os.environ["SERVICE_NAME"] = "fdex-test"
os.environ["RELEASE_CACHE_DIR"] = "/tmp/fdex-test/releases"

from fastapi.testclient import TestClient  # noqa: E402

import app.client_update as client_update_module  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app, base_url="http://testserver")


@pytest.fixture(autouse=True)
def _stable_latest_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_update_module,
        "latest_release_tag",
        lambda timeout_seconds=6.0: "v1.1.0",
    )
    monkeypatch.setattr(client_update_module, "sync_latest_release", lambda: {})


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _login(test_client: TestClient = client) -> None:
    response = test_client.get("/admin", follow_redirects=False)
    if response.status_code == 200:
        return
    login_page = test_client.get("/admin/login")
    token = _csrf(login_page.text)
    response = test_client.post(
        "/admin/login",
        data={
            "username": "testadmin",
            "password": "test-password-12345",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _write_cached_release(version: str) -> Path:
    release_dir = Path(os.environ["RELEASE_CACHE_DIR"])
    release_dir.mkdir(parents=True, exist_ok=True)
    for child in release_dir.iterdir():
        if child.is_file():
            child.unlink()
    apk = release_dir / f"fdex-{version}.apk"
    apk.write_bytes(f"fake-apk-{version}".encode())
    manifest = {
        "tag_name": f"v{version}",
        "version": version,
        "name": f"FDEX {version}",
        "body": "test release",
        "published_at": "2026-07-28T00:00:00Z",
        "filename": apk.name,
        "sha256": "abc123",
        "size": apk.stat().st_size,
        "synced_at": "2026-07-28T00:01:00Z",
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return apk


def test_root_redirects_to_admin() -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/admin"


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


def test_info_includes_admin_without_secrets() -> None:
    response = client.get("/api/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["admin"].endswith("/admin")
    assert "test-password" not in response.text
    assert "session-secret" not in response.text


def test_public_config_never_exposes_secrets() -> None:
    response = client.get("/api/public-config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["public_base_url"]
    assert "api_key" not in payload
    assert "ai_base_url" not in payload
    assert "admin_password" not in payload
    assert "admin_session_secret" not in payload
    assert "github_token" not in payload


def test_default_server_port_is_isolated() -> None:
    assert get_settings().fdex_host == "127.0.0.1"
    assert get_settings().fdex_port == 18080


def test_update_endpoint_waits_when_cache_is_empty() -> None:
    release_dir = Path(os.environ["RELEASE_CACHE_DIR"])
    release_dir.mkdir(parents=True, exist_ok=True)
    for child in release_dir.iterdir():
        if child.is_file():
            child.unlink()
    response = client.get("/api/client/update?current_version=1.0.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["status"] == "waiting_for_server_cache"
    assert payload["strategy"] == "latest_only"


def test_update_endpoint_serves_cached_release() -> None:
    apk = _write_cached_release("1.1.0")

    response = client.get("/api/client/update?current_version=1.0.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["latest_version"] == "1.1.0"
    assert payload["strategy"] == "latest_only"
    assert payload["apk_url"].endswith("/downloads/fdex-1.1.0.apk")

    download = client.get("/downloads/fdex-1.1.0.apk")
    assert download.status_code == 200
    assert download.content == apk.read_bytes()


def test_update_endpoint_skips_intermediate_cached_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cached_release("1.1.1")
    monkeypatch.setattr(
        client_update_module,
        "latest_release_tag",
        lambda timeout_seconds=6.0: "v1.1.3",
    )

    response = client.get("/api/client/update?current_version=1.0.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["status"] == "waiting_for_server_cache"
    assert payload["strategy"] == "latest_only"
    assert payload["cached_version"] == "1.1.1"
    assert payload["latest_version"] == "1.1.3"
    assert "apk_url" not in payload


def test_update_endpoint_jumps_directly_to_latest_cached_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cached_release("1.1.3")
    monkeypatch.setattr(
        client_update_module,
        "latest_release_tag",
        lambda timeout_seconds=6.0: "v1.1.3",
    )

    response = client.get("/api/client/update?current_version=1.0.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["latest_version"] == "1.1.3"
    assert payload["apk_url"].endswith("/downloads/fdex-1.1.3.apk")


def test_update_endpoint_reports_up_to_date() -> None:
    _write_cached_release("1.1.0")
    response = client.get("/api/client/update?current_version=1.1.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["latest_version"] == "1.1.0"


def test_update_endpoint_does_not_offer_unverified_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_cached_release("1.1.0")

    def fail_latest(*, timeout_seconds: float = 6.0) -> str:
        raise TimeoutError("github unavailable")

    monkeypatch.setattr(client_update_module, "latest_release_tag", fail_latest)
    response = client.get("/api/client/update?current_version=1.0.0")
    payload = response.json()
    assert payload["available"] is False
    assert payload["status"] == "latest_version_unverified"
    assert "apk_url" not in payload


def test_admin_requires_login() -> None:
    isolated = TestClient(app, base_url="http://testserver")
    response = isolated.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_login_rejects_invalid_csrf() -> None:
    isolated = TestClient(app, base_url="http://testserver")
    isolated.get("/admin/login")
    response = isolated.post(
        "/admin/login",
        data={"username": "testadmin", "password": "test-password-12345", "csrf_token": "invalid"},
    )
    assert response.status_code == 403


def test_admin_login_and_dashboard() -> None:
    _login()
    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "服务运行状态" in dashboard.text
    assert "多供应商模式" in dashboard.text
    assert "test-password-12345" not in dashboard.text
    assert "test-session-secret" not in dashboard.text


def test_all_admin_pages_render_without_secrets() -> None:
    _login()
    expected = {
        "/admin/providers": "AI 供应商与模型",
        "/admin/settings": "AI 供应商配置",
        "/admin/logs?lines=100": "后台审计日志",
        "/admin/maintenance": "版本与维护",
    }
    for path, text in expected.items():
        response = client.get(path)
        assert response.status_code == 200
        assert text in response.text
        assert "test-password-12345" not in response.text
        assert "test-session-secret" not in response.text


def test_static_admin_assets() -> None:
    css = client.get("/static/admin.css")
    favicon = client.get("/static/favicon.svg")
    assert css.status_code == 200
    assert ".app-shell" in css.text
    assert favicon.status_code == 200
    assert "<svg" in favicon.text
