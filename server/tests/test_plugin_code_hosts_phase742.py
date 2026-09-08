from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from app import plugin_code_hosts, plugin_runtime


ROOT = Path(__file__).resolve().parents[2]
OWNER = "usr_plugin_code_host_742"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _store(tmp_path: Path) -> plugin_code_hosts.CodeHostCredentialStore:
    return plugin_code_hosts.CodeHostCredentialStore(
        tmp_path / "code-hosts.db",
        tmp_path / "code-hosts.key",
    )


def test_gitlab_and_gitee_are_native_connectable_plugins() -> None:
    gitlab = plugin_runtime.plugin_definition("gitlab")
    gitee = plugin_runtime.plugin_definition("gitee")
    assert gitlab.implementation == "native"
    assert gitee.implementation == "native"
    assert gitlab.connect_path == "/account/plugins#plugin-gitlab"
    assert gitee.connect_path == "/account/plugins#plugin-gitee"
    assert any(tool.name == "gitlab.repositories.list" and tool.risk == "read" for tool in gitlab.tools)
    assert any(tool.name == "gitlab.repository.write" and tool.risk == "write" for tool in gitlab.tools)
    assert any(tool.name == "gitee.repositories.list" and tool.risk == "read" for tool in gitee.tools)
    assert any(tool.name == "gitee.pull_request.write" and tool.risk == "write" for tool in gitee.tools)


def test_code_host_credentials_are_encrypted_and_owner_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = store.save_verified(
        OWNER,
        "gitlab",
        base_url="https://gitlab.com",
        token="glpat-super-secret-token",
        profile={"id": 42, "username": "alice", "name": "Alice"},
        repository_count=3,
    )
    assert saved["account_login"] == "alice"
    assert saved["repository_count"] == 3
    assert "token" not in saved
    raw_db = (tmp_path / "code-hosts.db").read_bytes()
    assert b"glpat-super-secret-token" not in raw_db
    secret = store.get(OWNER, "gitlab", secret=True)
    assert secret is not None and secret["token"] == "glpat-super-secret-token"
    assert store.get("usr_plugin_code_host_other", "gitlab") is None
    assert store.delete_owner(OWNER) == 1
    assert store.get(OWNER, "gitlab") is None


def test_gitlab_base_url_is_fail_closed_against_arbitrary_ssrf_targets() -> None:
    assert plugin_code_hosts._base_url("gitlab", "https://gitlab.com/") == "https://gitlab.com"
    with pytest.raises(ValueError, match="仅开放"):
        plugin_code_hosts._base_url("gitlab", "http://127.0.0.1:8080")
    with pytest.raises(ValueError, match="仅开放"):
        plugin_code_hosts._base_url("gitlab", "https://internal.example.test")
    assert plugin_code_hosts._base_url("gitee", "https://attacker.invalid") == "https://gitee.com"


def test_auth_headers_keep_tokens_out_of_urls_and_use_provider_native_headers() -> None:
    assert plugin_code_hosts._headers("gitlab", "secret")["PRIVATE-TOKEN"] == "secret"
    assert plugin_code_hosts._headers("gitee", "secret")["Authorization"] == "Bearer secret"


def test_connect_verifies_remote_before_persisting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    verified: list[tuple[str, str, str]] = []

    def fake_verify(plugin_id: str, token: str, *, base_url: str = "") -> dict[str, Any]:
        verified.append((plugin_id, token, base_url))
        return {"id": 99, "username": "gitlab-user", "name": "GitLab User"}

    monkeypatch.setattr(plugin_code_hosts, "code_host_credential_store", lambda: store)
    monkeypatch.setattr(plugin_code_hosts, "verify_code_host_token", fake_verify)
    monkeypatch.setattr(
        plugin_code_hosts,
        "_list_with_connection",
        lambda connection: [{"full_name": "group/a"}, {"full_name": "group/b"}],
    )

    saved = plugin_code_hosts.connect_code_host(
        OWNER,
        "gitlab",
        "glpat-verified-token",
        base_url="https://gitlab.com",
    )
    assert verified == [("gitlab", "glpat-verified-token", "https://gitlab.com")]
    assert saved["account_login"] == "gitlab-user"
    assert saved["repository_count"] == 2
    assert plugin_code_hosts.code_host_connection_status(OWNER, "gitlab")["connected"] is True


def test_gitlab_write_uses_create_or_update_repository_file_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_code_hosts,
        "_connection",
        lambda owner_id, plugin_id: {"plugin_id": "gitlab", "base_url": "https://gitlab.com", "token": "secret"},
    )
    calls: list[dict[str, Any]] = []

    def fake_request(plugin_id: str, base_url: str, token: str, method: str, path: str, **kwargs: Any):
        calls.append({"method": method, "path": path, **kwargs})
        if method == "GET":
            return None
        return httpx.Response(200, json={"file_path": "README.md", "branch": "main"})

    monkeypatch.setattr(plugin_code_hosts, "_request", fake_request)
    result = plugin_code_hosts.write_code_host_file(
        OWNER,
        "gitlab",
        "group/project",
        "README.md",
        "hello",
        branch="main",
        message="docs: update readme",
    )
    assert result["repository"] == "group/project"
    assert calls[0]["method"] == "GET" and calls[0]["allow_404"] is True
    assert calls[1]["method"] == "POST"
    assert calls[1]["json_body"]["content"] == "hello"
    assert calls[1]["json_body"]["commit_message"] == "docs: update readme"


def test_gitee_update_uses_current_sha_and_base64_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_code_hosts,
        "_connection",
        lambda owner_id, plugin_id: {"plugin_id": "gitee", "base_url": "https://gitee.com", "token": "secret"},
    )
    calls: list[dict[str, Any]] = []

    def fake_request(plugin_id: str, base_url: str, token: str, method: str, path: str, **kwargs: Any):
        calls.append({"method": method, "path": path, **kwargs})
        if method == "GET":
            return httpx.Response(200, json={"sha": "old-sha", "content": base64.b64encode(b"old").decode()})
        return httpx.Response(200, json={"content": {"sha": "new-sha"}})

    monkeypatch.setattr(plugin_code_hosts, "_request", fake_request)
    plugin_code_hosts.write_code_host_file(
        OWNER,
        "gitee",
        "alice/project",
        "docs/a.txt",
        "new text",
        branch="master",
        message="update file",
    )
    assert calls[1]["method"] == "PUT"
    assert calls[1]["json_body"]["sha"] == "old-sha"
    assert base64.b64decode(calls[1]["json_body"]["content"]).decode() == "new text"


def test_plugin_center_exposes_real_connect_disconnect_and_repository_routes() -> None:
    routes = _read("server/app/plugin_portal_routes.py")
    page = _read("server/app/templates/user_plugins.html")
    runtime = _read("server/app/plugin_runtime.py")
    assert '@router.post("/{plugin_id}/connect"' in routes
    assert '@router.post("/{plugin_id}/disconnect"' in routes
    assert '@router.get("/{plugin_id}/repositories.json"' in routes
    assert 'name="access_token"' in page
    assert "验证并连接" in page
    assert "plugin.id in ['gitlab', 'gitee']" in page
    assert "code_host_connection_status" in runtime


def test_employee_tool_router_can_run_gitlab_and_gitee_inventory() -> None:
    source = _read("server/app/employee_agent_tools.py")
    assert "list_code_host_repositories" in source
    assert '"gitlab": ("gitlab", "git lab")' in source
    assert '"gitee": ("gitee", "码云")' in source
    assert 'f"{plugin_id}.repositories.list"' in source
    assert "run_plugin_tool(" in source


def test_account_erasure_includes_encrypted_code_host_credentials() -> None:
    source = _read("server/app/account_cleanup.py")
    assert "from app.plugin_code_hosts import code_host_credential_store" in source
    assert "code_host_credential_store().delete_owner(clean)" in source
    assert '"plugin_code_host_connections"' in source
