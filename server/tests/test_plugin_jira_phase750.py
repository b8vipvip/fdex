from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app import plugin_jira, plugin_jira_mcp, plugin_runtime


ROOT = Path(__file__).resolve().parents[2]
OWNER = "usr_jira_plugin_test_001"
CLOUD_A = "1324a887-45db-1bf4-1e99-ef0ff456d421"
CLOUD_B = "8594f221-9797-5f78-1fa4-485e198d7cd0"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        jira_oauth_ready=True,
        public_base_url="https://fdex.example",
        fdex_jira_oauth_client_id="jira-client",
        fdex_jira_oauth_client_secret="jira-secret",
        fdex_jira_oauth_scope="read:jira-work write:jira-work offline_access",
        fdex_jira_oauth_flow_minutes=10,
    )


def _store(tmp_path: Path) -> plugin_jira.JiraStore:
    store = plugin_jira.JiraStore(tmp_path / "jira.db", tmp_path / "jira.key")
    store.init()
    return store


def _resources(*cloud_ids: str) -> list[dict]:
    return [
        {
            "id": cloud_id,
            "name": f"Site {index}",
            "url": f"https://fdex-{index}.atlassian.net",
            "scopes": ["read:jira-work", "write:jira-work"],
        }
        for index, cloud_id in enumerate(cloud_ids, start=1)
    ]


def test_jira_runtime_is_native_and_declares_read_write_tools() -> None:
    from app.plugin_jira_runtime import install_jira_runtime

    install_jira_runtime()
    definition = plugin_runtime.plugin_definition("jira")
    assert definition.implementation == "native"
    assert definition.connect_path.endswith("#plugin-jira")
    assert {tool.name for tool in definition.tools} >= {
        "jira.workspace.read",
        "jira.issue.read",
        "jira.issue.write",
        "jira.comment.write",
    }
    assert next(tool for tool in definition.tools if tool.name == "jira.issue.write").risk == "write"


def test_jira_credentials_are_owner_scoped_encrypted_and_sites_are_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = store.save_connection(
        OWNER,
        access_token="access-token-abcdefghijklmnopqrstuvwxyz",
        refresh_token="refresh-token-abcdefghijklmnopqrstuvwxyz",
        expires_in=3600,
        scope="read:jira-work write:jira-work offline_access",
        resources=_resources(CLOUD_A),
    )
    assert saved["site_count"] == 1
    assert saved["sites"][0]["cloud_id"] == CLOUD_A
    assert "access_token" not in saved and "refresh_token" not in saved
    raw = (tmp_path / "jira.db").read_bytes()
    assert b"access-token-abcdefghijklmnopqrstuvwxyz" not in raw
    assert b"refresh-token-abcdefghijklmnopqrstuvwxyz" not in raw
    secret = store.get(OWNER, secret=True)
    assert secret is not None
    assert secret["access_token"].startswith("access-token-")
    assert secret["refresh_token"].startswith("refresh-token-")


def test_jira_oauth_start_hashes_owner_state_and_requests_offline_access(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(plugin_jira, "fresh_settings", _settings)
    store = _store(tmp_path)
    flow = store.start_flow(OWNER)
    parsed = urlparse(flow["authorize_url"])
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "auth.atlassian.com"
    assert query["audience"] == ["api.atlassian.com"]
    assert query["client_id"] == ["jira-client"]
    assert query["redirect_uri"] == ["https://fdex.example/account/plugins/jira/oauth/callback"]
    assert set(query["scope"][0].split()) >= {"read:jira-work", "write:jira-work", "offline_access"}
    assert query["prompt"] == ["consent"]
    state = query["state"][0]
    with sqlite3.connect(tmp_path / "jira.db") as conn:
        row = conn.execute("SELECT state_hash,status FROM jira_oauth_flows WHERE owner_id=?", (OWNER,)).fetchone()
    assert row == (hashlib.sha256(state.encode()).hexdigest(), "pending")
    assert "jira-secret" not in flow["authorize_url"]


def test_jira_oauth_completion_verifies_accessible_resources_before_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(plugin_jira, "fresh_settings", _settings)
    store = _store(tmp_path)
    flow = store.start_flow(OWNER)
    state = parse_qs(urlparse(flow["authorize_url"]).query)["state"][0]
    seen: dict[str, object] = {}

    def fake_token(payload: dict[str, str], *, context: str) -> dict:
        seen["payload"] = dict(payload)
        return {
            "access_token": "access-token-abcdefghijklmnopqrstuvwxyz",
            "refresh_token": "refresh-token-abcdefghijklmnopqrstuvwxyz",
            "expires_in": 3600,
            "scope": "read:jira-work write:jira-work offline_access",
        }

    def fake_resources(token: str) -> list[dict]:
        seen["token"] = token
        return _resources(CLOUD_A)

    monkeypatch.setattr(plugin_jira, "_token_request", fake_token)
    monkeypatch.setattr(plugin_jira, "_accessible_resources_with_token", fake_resources)
    saved = store.complete_flow(OWNER, state=state, code="jira-auth-code")
    assert saved["sites"][0]["name"] == "Site 1"
    assert seen["token"] == "access-token-abcdefghijklmnopqrstuvwxyz"
    assert seen["payload"]["client_secret"] == "jira-secret"
    with sqlite3.connect(tmp_path / "jira.db") as conn:
        status = conn.execute("SELECT status FROM jira_oauth_flows WHERE owner_id=?", (OWNER,)).fetchone()[0]
    assert status == "authorized"


def test_jira_refresh_requires_and_persists_rotated_refresh_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_connection(
        OWNER,
        access_token="expired-access-token-abcdefghijklmnop",
        refresh_token="old-refresh-token-abcdefghijklmnopq",
        expires_in=60,
        scope="read:jira-work write:jira-work offline_access",
        resources=_resources(CLOUD_A),
    )
    with store.db() as conn:
        conn.execute("UPDATE jira_connections SET token_expires_at='2000-01-01T00:00:00+00:00' WHERE owner_id=?", (OWNER,))
    monkeypatch.setattr(plugin_jira, "jira_store", lambda: store)
    monkeypatch.setattr(plugin_jira, "fresh_settings", _settings)
    monkeypatch.setattr(
        plugin_jira,
        "_token_request",
        lambda payload, *, context: {
            "access_token": "new-access-token-abcdefghijklmnopqrstuvwxyz",
            "refresh_token": "new-refresh-token-abcdefghijklmnopqrstuvwxyz",
            "expires_in": 3600,
            "scope": "read:jira-work write:jira-work offline_access",
        },
    )
    assert plugin_jira._access_token(OWNER).startswith("new-access-token-")
    secret = store.get(OWNER, secret=True)
    assert secret is not None and secret["refresh_token"].startswith("new-refresh-token-")


def test_jira_multi_site_resolution_fails_closed_without_cloud_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_connection(
        OWNER,
        access_token="access-token-abcdefghijklmnopqrstuvwxyz",
        refresh_token="refresh-token-abcdefghijklmnopqrstuvwxyz",
        expires_in=3600,
        scope="read:jira-work write:jira-work offline_access",
        resources=_resources(CLOUD_A, CLOUD_B),
    )
    monkeypatch.setattr(plugin_jira, "jira_store", lambda: store)
    with pytest.raises(ValueError, match="cloud_id"):
        plugin_jira._resolve_site(OWNER)
    assert plugin_jira._resolve_site(OWNER, CLOUD_B)["cloud_id"] == CLOUD_B


def test_jira_enhanced_search_and_adf_write_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    monkeypatch.setattr(plugin_jira, "_resolve_site", lambda owner_id, cloud_id="": {"cloud_id": CLOUD_A})

    def fake_request(owner_id: str, cloud_id: str, method: str, path: str, *, params=None, json_body=None, context=""):
        calls.append((method, path, json_body))
        if path == "/rest/api/3/search/jql":
            return {"issues": [], "isLast": True}
        if path == "/rest/api/3/issue":
            return {"id": "10001", "key": "ENG-1", "self": "https://api.atlassian.com/example"}
        if path.endswith("/comment"):
            return {"id": "20001", "body": plugin_jira.text_to_adf("done"), "author": {"accountId": "u1", "displayName": "Alice"}}
        raise AssertionError(path)

    monkeypatch.setattr(plugin_jira, "_request", fake_request)
    found = plugin_jira.search_jira_issues(OWNER, "project = ENG ORDER BY updated DESC", cloud_id=CLOUD_A, max_results=25)
    assert found["is_last"] is True
    assert calls[-1][0:2] == ("POST", "/rest/api/3/search/jql")
    assert calls[-1][2]["maxResults"] == 25

    created = plugin_jira.create_jira_issue(
        OWNER,
        "Fix regression",
        cloud_id=CLOUD_A,
        project_key="ENG",
        issue_type_name="Task",
        description="line one\nline two",
    )
    assert created["issue"]["key"] == "ENG-1"
    fields = calls[-1][2]["fields"]
    assert fields["project"] == {"key": "ENG"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["description"]["type"] == "doc"
    assert plugin_jira.adf_to_text(fields["description"]) == "line one\nline two"

    comment = plugin_jira.add_jira_comment(OWNER, "ENG-1", "done", cloud_id=CLOUD_A)
    assert comment["action"] == "commented"
    assert calls[-1][2]["body"]["type"] == "doc"


def test_jira_mcp_uses_read_vs_write_risk_and_runtime_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    definitions = plugin_jira_mcp._tool_definitions()
    assert definitions["jira_list_sites"]["risk"] == "read"
    assert definitions["jira_search_issues"]["risk"] == "read"
    assert definitions["jira_create_issue"]["risk"] == "write"
    assert definitions["jira_transition_issue"]["risk"] == "write"
    assert definitions["jira_add_comment"]["risk"] == "write"

    plugin_jira_mcp.install_jira_mcp_tools()
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        plugin_jira,
        "list_jira_sites",
        lambda owner_id, *, refresh: seen.update(owner_id=owner_id, refresh=refresh) or {"plugin_id": "jira", "sites": [], "count": 0},
    )
    executor = plugin_jira_mcp.plugin_mcp_gateway._tool_executor(OWNER, "jira_list_sites", {})
    result = executor()
    assert result["plugin_id"] == "jira"
    assert seen == {"owner_id": OWNER, "refresh": True}


def test_phase750_portal_config_lifecycle_export_and_codex_wiring() -> None:
    portal = (ROOT / "server/app/plugin_portal_routes.py").read_text(encoding="utf-8")
    page = (ROOT / "server/app/templates/user_plugins.html").read_text(encoding="utf-8")
    config = (ROOT / "server/app/config.py").read_text(encoding="utf-8")
    cleanup = (ROOT / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    export = (ROOT / "server/app/account_data_export.py").read_text(encoding="utf-8")
    installer = (ROOT / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")

    assert '"jira"' in portal
    assert "start_jira_oauth" in portal and "complete_jira_oauth" in portal
    assert 'tool_name = "jira_list_sites"' in portal
    assert "/account/plugins/jira/oauth/start" in page
    assert "/account/plugins/jira/sites.json" in page
    assert "FDEX_JIRA_OAUTH_CLIENT_ID" in page
    assert "fdex_jira_oauth_client_id" in config and "jira_oauth_ready" in config
    assert "jira_store().delete_owner(clean)" in cleanup
    assert '"plugin_jira_connections"' in cleanup and '"plugin_jira_oauth_flows"' in cleanup
    assert "jira_store().get(user_id)" in export
    assert '"jira_access_token"' in export and '"jira_refresh_token"' in export and '"jira_oauth_state"' in export
    assert "install_jira_runtime()" in installer
    assert "install_jira_mcp_tools()" in installer
