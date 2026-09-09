from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app import plugin_vercel
from app.plugin_vercel import VercelCredentialStore


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _response(status: int, payload: object, url: str = "https://api.vercel.com/test") -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status, json=payload, request=request)


def test_vercel_credentials_are_encrypted_and_owner_scoped(tmp_path: Path) -> None:
    store = VercelCredentialStore(tmp_path / "vercel.db", tmp_path / "vercel.key")
    saved = store.put(
        "usr_phase751_owner",
        "vercel-secret-token-value",
        {"user_id": "u1", "username": "alice", "email": "a@example.com", "project_count": 3},
    )
    assert saved["username"] == "alice"
    assert saved["token_configured"] is True
    assert store.token("usr_phase751_owner") == "vercel-secret-token-value"
    raw = (tmp_path / "vercel.db").read_bytes()
    assert b"vercel-secret-token-value" not in raw
    assert store.get("usr_other_owner") is None


def test_connect_verifies_identity_and_projects_before_persisting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = VercelCredentialStore(tmp_path / "vercel.db", tmp_path / "vercel.key")
    monkeypatch.setattr(plugin_vercel, "vercel_credential_store", lambda: store)
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        headers = dict(kwargs.get("headers") or {})
        params = dict(kwargs.get("params") or {})
        calls.append((url, headers, params))
        if url.endswith("/v2/user"):
            return _response(200, {"user": {"uid": "u_1", "username": "alice", "email": "a@example.com"}}, url)
        if "/v2/teams/" in url:
            return _response(200, {"id": "team_abcdef123", "slug": "acme", "name": "Acme"}, url)
        if url.endswith("/v10/projects"):
            return _response(200, {"projects": [{"id": "prj_1", "name": "web"}], "pagination": {"count": 1}}, url)
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "request", fake_request)
    saved = plugin_vercel.connect_vercel("usr_phase751_owner", "secret-token-123", team_id="team_abcdef123")
    assert saved["team_name"] == "Acme"
    assert saved["project_count"] == 1
    assert store.token("usr_phase751_owner") == "secret-token-123"
    assert all(call[1].get("Authorization") == "Bearer secret-token-123" for call in calls)
    project_call = next(call for call in calls if call[0].endswith("/v10/projects"))
    assert project_call[2]["teamId"] == "team_abcdef123"


def test_failed_remote_verification_never_persists_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = VercelCredentialStore(tmp_path / "vercel.db", tmp_path / "vercel.key")
    monkeypatch.setattr(plugin_vercel, "vercel_credential_store", lambda: store)
    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: _response(401, {"error": {"message": "Not authorized"}}))
    with pytest.raises(plugin_vercel.VercelPluginError):
        plugin_vercel.connect_vercel("usr_phase751_owner", "bad-token-123")
    assert store.get("usr_phase751_owner") is None


def test_deployment_projection_strips_provider_private_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_vercel,
        "_request",
        lambda *args, **kwargs: {
            "id": "dpl_1",
            "name": "web",
            "projectId": "prj_1",
            "url": "web-preview.vercel.app",
            "readyState": "READY",
            "target": None,
            "env": {"DATABASE_URL": "must-not-leak"},
            "buildEnv": {"SECRET": "must-not-leak"},
            "gitSource": {"ref": "feature/x", "sha": "abc123"},
        },
    )
    payload = plugin_vercel.read_vercel_deployment("usr_phase751_owner", "dpl_1")
    text = json.dumps(payload)
    assert payload["deployment"]["state"] == "READY"
    assert payload["deployment"]["target"] == "preview"
    assert "DATABASE_URL" not in text
    assert "must-not-leak" not in text


def test_preview_redeploy_validates_project_and_never_requests_production(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(owner_id: str, method: str, path: str, **kwargs: object) -> object:
        body = kwargs.get("json_body") if isinstance(kwargs.get("json_body"), dict) else None
        calls.append((method, path, body))
        if method == "GET":
            return {"id": "dpl_old", "name": "web", "projectId": "prj_1", "readyState": "READY"}
        return {"id": "dpl_new", "name": "web", "projectId": "prj_1", "readyState": "QUEUED", "target": None}

    monkeypatch.setattr(plugin_vercel, "_request", fake_request)
    result = plugin_vercel.redeploy_vercel_preview("usr_phase751_owner", "prj_1", "dpl_old")
    assert result["action"] == "preview_redeployment_created"
    post = next(call for call in calls if call[0] == "POST")
    assert post[1] == "/v13/deployments"
    assert post[2] == {"name": "web", "project": "prj_1", "deploymentId": "dpl_old"}
    assert "production" not in json.dumps(post[2]).lower()


def test_production_promote_requires_ready_and_uses_documented_project_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_vercel,
        "read_vercel_deployment",
        lambda *args, **kwargs: {"plugin_id": "vercel", "deployment": {"project_id": "prj_1", "state": "READY"}},
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        plugin_vercel,
        "_request",
        lambda owner_id, method, path, **kwargs: calls.append((method, path)) or {},
    )
    result = plugin_vercel.promote_vercel_production("usr_phase751_owner", "prj_1", "dpl_1")
    assert result["action"] == "production_promote_requested"
    assert calls == [("POST", "/v10/projects/prj_1/promote/dpl_1")]


def test_phase751_runtime_mcp_portal_and_lifecycle_wiring() -> None:
    runtime = _read("app/plugin_vercel_runtime.py")
    mcp = _read("app/plugin_vercel_mcp.py")
    codex = _read("app/codex_remote_mcp_install.py")
    template = _read("app/templates/user_plugins.html")
    cleanup = _read("app/account_cleanup.py")
    export = _read("app/account_data_export.py")

    assert '"vercel",\n        "Vercel"' in runtime
    assert 'PluginTool("vercel.production.promote", "dangerous"' in runtime
    assert 'PluginTool("vercel.production.rollback", "dangerous"' in runtime
    assert '"vercel_promote_production"' in mcp
    assert '"vercel_rollback_production"' in mcp
    assert 'risk in {"write", "dangerous"} and mode != "write"' in mcp
    assert '"destructiveHint": True' in mcp
    assert 'confirmed=bool(dangerous)' in mcp
    assert 'default_tools_approval_mode="writes"' in mcp
    assert "install_vercel_runtime()" in codex
    assert "install_vercel_mcp_tools()" in codex
    assert "plugin.id == 'vercel'" in template
    assert "/account/plugins/vercel/connect" in template
    assert "生产 Promote/Rollback" in template
    assert "vercel_credential_store().delete_owner(clean)" in cleanup
    assert '"plugin_vercel_connections"' in cleanup
    assert '"plugin_id": "vercel"' in export
    assert '"vercel_access_token"' in export


def test_vercel_provider_requests_are_fixed_origin_and_bearer_only() -> None:
    source = _read("app/plugin_vercel.py")
    assert '_API_ROOT = "https://api.vercel.com"' in source
    assert '"Authorization": f"Bearer {token}"' in source
    assert "follow_redirects=False" in source
    assert "env" not in " ".join(plugin_vercel._deployment_summary({}).keys()).lower()
