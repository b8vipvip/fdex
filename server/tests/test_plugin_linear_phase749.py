from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import plugin_linear, plugin_linear_mcp, plugin_runtime


ROOT = Path(__file__).resolve().parents[2]
OWNER = "usr_linear_plugin_test_001"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        linear_oauth_ready=True,
        public_base_url="https://fdex.example",
        fdex_linear_oauth_client_id="linear-client",
        fdex_linear_oauth_client_secret="linear-secret",
        fdex_linear_oauth_scope="read,write",
        fdex_linear_oauth_flow_minutes=10,
    )


def _store(tmp_path: Path) -> plugin_linear.LinearStore:
    store = plugin_linear.LinearStore(tmp_path / "linear.db", tmp_path / "linear.key")
    store.init()
    return store


def _identity() -> dict:
    return {
        "data": {
            "viewer": {"id": "viewer-1", "name": "Alice", "email": "alice@example.com"},
            "organization": {"id": "org-1", "name": "FDEX Lab", "urlKey": "fdex-lab"},
        }
    }


def test_linear_runtime_is_native_and_declares_read_write_tools() -> None:
    from app.plugin_linear_runtime import install_linear_runtime

    install_linear_runtime()
    definition = plugin_runtime.plugin_definition("linear")
    assert definition.implementation == "native"
    assert definition.connect_path.endswith("#plugin-linear")
    assert {tool.name for tool in definition.tools} >= {
        "linear.workspace.read",
        "linear.issue.read",
        "linear.issue.write",
        "linear.comment.write",
    }
    assert next(tool for tool in definition.tools if tool.name == "linear.issue.write").risk == "write"


def test_linear_credentials_are_owner_scoped_and_encrypted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = store.save_connection(
        OWNER,
        access_token="access-token-abcdefghijklmnopqrstuvwxyz",
        refresh_token="refresh-token-abcdefghijklmnopqrstuvwxyz",
        expires_in=3600,
        scope="read write",
        identity=_identity(),
    )
    assert saved["organization_name"] == "FDEX Lab"
    assert "access_token" not in saved and "refresh_token" not in saved
    raw = (tmp_path / "linear.db").read_bytes()
    assert b"access-token-abcdefghijklmnopqrstuvwxyz" not in raw
    assert b"refresh-token-abcdefghijklmnopqrstuvwxyz" not in raw
    secret = store.get(OWNER, secret=True)
    assert secret is not None
    assert secret["access_token"].startswith("access-token-")
    assert secret["refresh_token"].startswith("refresh-token-")


def test_linear_oauth_start_uses_owner_bound_state_and_pkce(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(plugin_linear, "fresh_settings", _settings)
    store = _store(tmp_path)
    flow = store.start_flow(OWNER)
    parsed = urlparse(flow["authorize_url"])
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "linear.app"
    assert query["client_id"] == ["linear-client"]
    assert query["redirect_uri"] == ["https://fdex.example/account/plugins/linear/oauth/callback"]
    assert query["scope"] == ["read,write"]
    assert query["code_challenge_method"] == ["S256"]
    assert query.get("code_challenge") and query.get("state")
    assert "linear-secret" not in flow["authorize_url"]
    with sqlite3.connect(tmp_path / "linear.db") as conn:
        row = conn.execute("SELECT state_hash,verifier_cipher FROM linear_oauth_flows WHERE owner_id=?", (OWNER,)).fetchone()
    assert row is not None
    assert query["state"][0] not in row[0]
    assert row[1] and "code_verifier" not in row[1]


def test_linear_oauth_completion_verifies_identity_before_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(plugin_linear, "fresh_settings", _settings)
    store = _store(tmp_path)
    flow = store.start_flow(OWNER)
    state = parse_qs(urlparse(flow["authorize_url"]).query)["state"][0]
    seen: dict[str, object] = {}

    def fake_token(form: dict[str, str], *, context: str) -> dict:
        seen["form"] = dict(form)
        return {
            "access_token": "access-token-abcdefghijklmnopqrstuvwxyz",
            "refresh_token": "refresh-token-abcdefghijklmnopqrstuvwxyz",
            "expires_in": 86399,
            "scope": "read write",
        }

    def fake_graphql(token: str, query: str, variables: dict, *, context: str) -> dict:
        seen["token"] = token
        assert "viewer" in query and "organization" in query
        return _identity()

    monkeypatch.setattr(plugin_linear, "_oauth_token_request", fake_token)
    monkeypatch.setattr(plugin_linear, "_graphql_with_token", fake_graphql)
    saved = store.complete_flow(OWNER, state=state, code="linear-auth-code")
    assert saved["viewer_email"] == "alice@example.com"
    assert seen["token"] == "access-token-abcdefghijklmnopqrstuvwxyz"
    assert seen["form"]["code_verifier"]
    with sqlite3.connect(tmp_path / "linear.db") as conn:
        row = conn.execute("SELECT status,verifier_cipher FROM linear_oauth_flows WHERE owner_id=?", (OWNER,)).fetchone()
    assert row == ("authorized", "")


def test_linear_refresh_rotates_refresh_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_connection(
        OWNER,
        access_token="expired-access-token-abcdefghijklmnop",
        refresh_token="old-refresh-token-abcdefghijklmnopq",
        expires_in=60,
        scope="read write",
        identity=_identity(),
    )
    with store.db() as conn:
        conn.execute("UPDATE linear_connections SET token_expires_at='2000-01-01T00:00:00+00:00' WHERE owner_id=?", (OWNER,))
    monkeypatch.setattr(plugin_linear, "linear_store", lambda: store)
    monkeypatch.setattr(plugin_linear, "fresh_settings", _settings)
    monkeypatch.setattr(
        plugin_linear,
        "_oauth_token_request",
        lambda form, *, context: {
            "access_token": "new-access-token-abcdefghijklmnopqrstuvwxyz",
            "refresh_token": "new-refresh-token-abcdefghijklmnopqrstuvwxyz",
            "expires_in": 86399,
            "scope": "read write",
        },
    )
    assert plugin_linear._access_token(OWNER).startswith("new-access-token-")
    secret = store.get(OWNER, secret=True)
    assert secret is not None and secret["refresh_token"].startswith("new-refresh-token-")


def test_graphql_checks_error_array_even_on_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return httpx.Response(200, json={"data": {"issues": {"nodes": []}}, "errors": [{"message": "denied"}]})

    monkeypatch.setattr(plugin_linear.httpx, "Client", lambda **_kwargs: Client())
    with pytest.raises(plugin_linear.LinearPluginError, match="denied"):
        plugin_linear._graphql_with_token(
            "access-token-abcdefghijklmnopqrstuvwxyz", "query { issues { nodes { id } } }", {}, context="test"
        )


def test_linear_issue_read_search_and_write_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_graphql(owner_id: str, query: str, variables: dict, *, context: str) -> dict:
        calls.append((query, variables))
        if "FdexLinearIssues" in query:
            return {"data": {"issues": {"nodes": [{"id": "i1", "identifier": "ENG-1", "title": "Bug", "description": "fix me", "state": {"id": "s1", "name": "Todo", "type": "unstarted"}, "team": {"id": "t1", "key": "ENG", "name": "Engineering"}}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        if "FdexLinearIssueCreate" in query:
            return {"data": {"issueCreate": {"success": True, "issue": {"id": "i2", "identifier": "ENG-2", "title": variables["input"]["title"], "team": {"id": "t1", "key": "ENG", "name": "Engineering"}}}}}
        if "FdexLinearIssueUpdate" in query:
            return {"data": {"issueUpdate": {"success": True, "issue": {"id": "i2", "identifier": "ENG-2", "title": "Done", "state": {"id": "s2", "name": "Done", "type": "completed"}, "team": {"id": "t1", "key": "ENG", "name": "Engineering"}}}}}
        if "FdexLinearCommentCreate" in query:
            return {"data": {"commentCreate": {"success": True, "comment": {"id": "c1", "body": variables["input"]["body"], "createdAt": "2026-09-09T00:00:00Z", "user": {"id": "u1", "name": "Alice"}}}}}
        raise AssertionError(query)

    monkeypatch.setattr(plugin_linear, "_graphql", fake_graphql)
    found = plugin_linear.search_linear_issues(OWNER, "bug")
    assert found["issues"][0]["identifier"] == "ENG-1"
    assert calls[-1][1]["filter"] == {"or": [{"title": {"containsIgnoreCase": "bug"}}, {"description": {"containsIgnoreCase": "bug"}}]}

    created = plugin_linear.create_linear_issue(OWNER, "team-1", "Fix regression", description="details", priority=2)
    assert created["action"] == "created"
    assert calls[-1][1]["input"]["priority"] == 2

    updated = plugin_linear.update_linear_issue(OWNER, "ENG-2", state_id="state-2")
    assert updated["issue"]["state"]["name"] == "Done"
    assert calls[-1][1]["input"] == {"stateId": "state-2"}

    comment = plugin_linear.create_linear_comment(OWNER, "ENG-2", "Implemented in PR #42")
    assert comment["action"] == "commented"
    assert calls[-1][1]["input"]["issueId"] == "ENG-2"


def test_linear_mcp_uses_read_vs_write_risk_and_runtime_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    definitions = plugin_linear_mcp._tool_definitions()
    assert definitions["linear_search_issues"]["risk"] == "read"
    assert definitions["linear_read_issue"]["risk"] == "read"
    assert definitions["linear_create_issue"]["risk"] == "write"
    assert definitions["linear_update_issue"]["risk"] == "write"
    assert definitions["linear_add_comment"]["risk"] == "write"

    plugin_linear_mcp.install_linear_mcp_tools()
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        plugin_linear,
        "search_linear_issues",
        lambda owner_id, query, *, page_size, after: seen.update(owner_id=owner_id, query=query, page_size=page_size, after=after) or {"plugin_id": "linear", "issues": [], "count": 0},
    )
    executor = plugin_linear_mcp.plugin_mcp_gateway._tool_executor(
        OWNER, "linear_search_issues", {"query": "regression", "page_size": 10}
    )
    result = executor()
    assert result["plugin_id"] == "linear"
    assert seen == {"owner_id": OWNER, "query": "regression", "page_size": 10, "after": ""}


def test_phase749_portal_config_lifecycle_export_and_install_wiring() -> None:
    portal = (ROOT / "server/app/plugin_portal_routes.py").read_text(encoding="utf-8")
    page = (ROOT / "server/app/templates/user_plugins.html").read_text(encoding="utf-8")
    config = (ROOT / "server/app/config.py").read_text(encoding="utf-8")
    cleanup = (ROOT / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    export = (ROOT / "server/app/account_data_export.py").read_text(encoding="utf-8")
    installer = (ROOT / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")

    assert '"linear"' in portal
    assert "start_linear_oauth" in portal and "complete_linear_oauth" in portal
    assert 'tool_name = "linear_search_issues"' in portal
    assert "/account/plugins/linear/oauth/start" in page
    assert "/account/plugins/linear/issues.json" in page
    assert "FDEX_LINEAR_OAUTH_CLIENT_ID" in page
    assert "fdex_linear_oauth_client_id" in config and "linear_oauth_ready" in config
    assert "linear_store().delete_owner(clean)" in cleanup
    assert '"plugin_linear_connections"' in cleanup and '"plugin_linear_oauth_flows"' in cleanup
    assert "linear_store().get(user_id)" in export
    assert '"linear_access_token"' in export and '"linear_refresh_token"' in export and '"linear_oauth_pkce_verifier"' in export
    assert "install_linear_runtime()" in installer
    assert "install_linear_mcp_tools()" in installer
