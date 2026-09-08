from pathlib import Path

import pytest

from app import plugin_runtime
from app.web_workspace import WebWorkspaceStore


ROOT = Path(__file__).resolve().parents[2]
OWNER = "usr_plugin_runtime_test_001"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_catalog_contains_first_generation_plugin_ecosystem() -> None:
    ids = {item.id for item in plugin_runtime.CATALOG}
    assert {
        "github", "gitlab", "gitee", "feishu", "notion", "google-drive", "linear", "jira",
        "vercel", "cloudflare", "ssh", "docker", "sentry", "datadog", "grafana",
    } <= ids
    github = plugin_runtime.plugin_definition("github")
    assert github.implementation == "native"
    assert any(tool.name == "github.installation.repositories" and tool.risk == "read" for tool in github.tools)
    assert any(tool.risk == "dangerous" for item in plugin_runtime.CATALOG for tool in item.tools)


def test_agent_grants_are_owner_and_employee_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = WebWorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(plugin_runtime, "web_workspace_store", lambda: store)
    employee = store.create(
        OWNER,
        "employee",
        {"name": "代码智体", "active": True, "coding_agent": True},
        sort_key="代码智体",
    )
    saved = plugin_runtime.save_agent_grant(OWNER, int(employee["id"]), "github", "read")
    assert saved["plugin_id"] == "github"
    assert saved["mode"] == "read"
    effective = plugin_runtime.effective_agent_grant(OWNER, employee, "github")
    assert effective == {"plugin_id": "github", "mode": "read", "source": "explicit"}


def test_github_legacy_default_preserves_existing_coding_agent_behavior(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = WebWorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(plugin_runtime, "web_workspace_store", lambda: store)
    employee = store.create(OWNER, "employee", {"name": "旧 Coding Agent", "coding_agent": True}, sort_key="old")
    grant = plugin_runtime.effective_agent_grant(OWNER, employee, "github")
    assert grant["mode"] == "write"
    assert grant["source"] == "legacy-default"


def test_dangerous_tools_require_per_execution_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = WebWorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(plugin_runtime, "web_workspace_store", lambda: store)
    monkeypatch.setattr(
        plugin_runtime,
        "plugin_connection_status",
        lambda owner_id, plugin_id: {"connected": True, "connection_count": 1, "state": "connected"},
    )
    employee = store.create(OWNER, "employee", {"name": "运维智体", "coding_agent": True}, sort_key="ops")
    plugin_runtime.save_agent_grant(OWNER, int(employee["id"]), "github", "write")
    with pytest.raises(PermissionError, match="明确确认"):
        plugin_runtime.authorize_plugin_tool(
            OWNER, employee, "github", "github.repository.delete", confirmed=False,
        )
    decision = plugin_runtime.authorize_plugin_tool(
        OWNER, employee, "github", "github.repository.delete", confirmed=True,
    )
    assert decision["tool"].risk == "dangerous"


def test_github_inventory_is_migrated_through_plugin_runtime() -> None:
    source = _read("server/app/employee_agent_tools.py")
    assert "from app.plugin_runtime import run_plugin_tool" in source
    assert '"github.installation.repositories"' in source
    assert "payload, event = run_plugin_tool(" in source


def test_plugin_center_is_mounted_and_replaces_sidebar_github_entry() -> None:
    main = _read("server/app/main.py")
    base = _read("server/app/templates/user_base.html")
    routes = _read("server/app/plugin_portal_routes.py")
    page = _read("server/app/templates/user_plugins.html")
    workspace = _read("server/app/web_workspace.py")
    assert "from app.plugin_portal_routes import router as plugin_portal_router" in main
    assert "app.include_router(plugin_portal_router)" in main
    assert 'href="/account/plugins"' in base
    assert '>插件</a>' in base
    assert '@router.get("", response_class=HTMLResponse' in routes
    assert "智体插件权限" in page
    assert "插件审计日志" in page
    assert '"plugin_grant"' in workspace and '"plugin_audit"' in workspace
