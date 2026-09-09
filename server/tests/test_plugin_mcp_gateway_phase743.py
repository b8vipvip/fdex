from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import plugin_agent_principals, plugin_mcp_gateway


OWNER = "usr_plugin_mcp_743"
ROOT_TASK = "a" * 32
CHILD_TASK = "b" * 32
EMPLOYEE = {"id": 7, "name": "代码助手", "active": True, "coding_agent": True}


class FakeWorkspace:
    def get(self, owner_id: str, kind: str, record_id: int, **_kwargs: Any) -> dict[str, Any]:
        assert owner_id == OWNER
        assert kind == "employee"
        if record_id != 7:
            raise KeyError(record_id)
        return dict(EMPLOYEE)


class FakeTasks:
    def __init__(self) -> None:
        self.rows = {
            ROOT_TASK: {"id": ROOT_TASK, "owner_id": OWNER, "parent_task_id": ""},
            CHILD_TASK: {"id": CHILD_TASK, "owner_id": OWNER, "parent_task_id": ROOT_TASK},
        }

    def get(self, owner_id: str, task_id: str):
        assert owner_id == OWNER
        return self.rows.get(task_id)


def test_principal_binding_is_immutable_and_retry_child_inherits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = plugin_agent_principals.PluginAgentPrincipalStore(tmp_path / "principals.db")
    monkeypatch.setattr(plugin_agent_principals, "web_workspace_store", lambda: FakeWorkspace())
    monkeypatch.setattr(plugin_agent_principals, "agent_task_store", lambda: FakeTasks())

    bound = store.bind(OWNER, ROOT_TASK, EMPLOYEE)
    assert bound["employee_id"] == 7
    inherited = store.resolve(OWNER, CHILD_TASK)
    assert inherited is not None
    assert inherited["employee_id"] == 7
    assert inherited["source_task_id"] == ROOT_TASK
    assert inherited["resolved_for_task_id"] == CHILD_TASK

    with pytest.raises(ValueError, match="其它智体"):
        store.bind(OWNER, ROOT_TASK, {"id": 8, "name": "伪造"})


def test_api_task_without_principal_gets_no_plugin_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoPrincipal:
        def resolve(self, owner_id: str, task_id: str):
            return None

        def active_employee(self, owner_id: str, task_id: str):
            return None

    revoked: list[tuple[str, str]] = []

    class Leases:
        def revoke_task(self, owner_id: str, task_id: str):
            revoked.append((owner_id, task_id))
            return 0

    monkeypatch.setattr(plugin_mcp_gateway, "plugin_agent_principal_store", lambda: NoPrincipal())
    monkeypatch.setattr(plugin_mcp_gateway, "plugin_mcp_lease_store", lambda: Leases())
    assert plugin_mcp_gateway.build_codex_plugin_mcp_config(OWNER, ROOT_TASK) == {}
    assert revoked == [(OWNER, ROOT_TASK)]


def test_plugin_mcp_config_contains_only_connected_granted_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    class Principal:
        def resolve(self, owner_id: str, task_id: str):
            return {"employee_id": 7}

        def active_employee(self, owner_id: str, task_id: str):
            return dict(EMPLOYEE)

    class Leases:
        def issue(self, owner_id: str, task_id: str, employee_id: int):
            return ({"id": "pml_1234567890abcdef", "employee_id": employee_id}, "capability-token-abcdefghijklmnopqrstuvwxyz")

        def revoke_task(self, owner_id: str, task_id: str):
            return 0

    monkeypatch.setattr(plugin_mcp_gateway, "plugin_agent_principal_store", lambda: Principal())
    monkeypatch.setattr(plugin_mcp_gateway, "plugin_mcp_lease_store", lambda: Leases())
    monkeypatch.setattr(
        plugin_mcp_gateway,
        "plugin_connection_status",
        lambda owner_id, plugin_id: {"connected": plugin_id in {"gitlab", "gitee"}},
    )
    monkeypatch.setattr(
        plugin_mcp_gateway,
        "effective_agent_grant",
        lambda owner_id, employee, plugin_id: {"mode": "write" if plugin_id == "gitlab" else "read"},
    )
    monkeypatch.setattr(plugin_mcp_gateway, "fresh_settings", lambda: SimpleNamespace(fdex_port=8000))

    config = plugin_mcp_gateway.build_codex_plugin_mcp_config(OWNER, ROOT_TASK)
    server = config["fdex_plugins"]
    assert server["url"].startswith("http://127.0.0.1:8000/internal/fdex-plugin-mcp/pml_")
    assert server["default_tools_approval_mode"] == "writes"
    assert "gitlab_write_file" in server["enabled_tools"]
    assert "gitlab_create_merge_request" in server["enabled_tools"]
    assert "gitee_list_repositories" in server["enabled_tools"]
    assert "gitee_read_file" in server["enabled_tools"]
    assert "gitee_write_file" not in server["enabled_tools"]
    assert "gitee_create_pull_request" not in server["enabled_tools"]
    # Phase 7.45 adds workflow tools through the same grant/risk filter when the extension is loaded.
    if "gitlab_create_branch" in plugin_mcp_gateway._TOOL_DEFINITIONS:
        assert "gitlab_create_branch" in server["enabled_tools"]
        assert "gitlab_list_tree" in server["enabled_tools"]
        assert "gitee_list_tree" in server["enabled_tools"]
        assert "gitee_create_branch" not in server["enabled_tools"]
    # The capability is an opaque localhost header, never a third-party credential.
    assert set(server["http_headers"]) == {"X-FDEX-Plugin-Capability"}


def test_tools_list_dynamically_rechecks_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    modes = {"gitlab": "read", "gitee": "none"}
    monkeypatch.setattr(plugin_mcp_gateway, "plugin_connection_status", lambda owner_id, plugin_id: {"connected": True})
    monkeypatch.setattr(
        plugin_mcp_gateway,
        "effective_agent_grant",
        lambda owner_id, employee, plugin_id: {"mode": modes[plugin_id]},
    )
    names = {row["name"] for row in plugin_mcp_gateway._tool_catalog(OWNER, EMPLOYEE)}
    assert {"gitlab_list_repositories", "gitlab_read_file"}.issubset(names)
    assert "gitlab_write_file" not in names
    assert "gitlab_create_merge_request" not in names
    assert not any(name.startswith("gitee_") for name in names)
    if "gitlab_list_tree" in plugin_mcp_gateway._TOOL_DEFINITIONS:
        assert "gitlab_list_tree" in names
        assert "gitlab_create_branch" not in names

    modes["gitlab"] = "write"
    modes["gitee"] = "read"
    names = {row["name"] for row in plugin_mcp_gateway._tool_catalog(OWNER, EMPLOYEE)}
    assert "gitlab_write_file" in names
    assert "gitlab_create_merge_request" in names
    assert "gitee_read_file" in names
    assert "gitee_write_file" not in names
    if "gitlab_create_branch" in plugin_mcp_gateway._TOOL_DEFINITIONS:
        assert "gitlab_create_branch" in names
        assert "gitee_list_tree" in names
        assert "gitee_create_branch" not in names


def test_tool_call_passes_through_plugin_runtime_authorization_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_mcp_gateway, "plugin_connection_status", lambda owner_id, plugin_id: {"connected": True})
    monkeypatch.setattr(plugin_mcp_gateway, "effective_agent_grant", lambda owner_id, employee, plugin_id: {"mode": "read"})
    seen: list[tuple[str, str]] = []

    def fake_run(owner_id: str, employee: dict[str, Any], plugin_id: str, tool_name: str, executor, **_kwargs: Any):
        seen.append((plugin_id, tool_name))
        return [{"full_name": "group/project", "private": True, "default_branch": "main", "can_push": False, "can_pr": False}]

    monkeypatch.setattr(plugin_mcp_gateway, "run_plugin_tool", fake_run)
    result = plugin_mcp_gateway._tool_call(
        {"owner_id": OWNER, "task_id": ROOT_TASK, "employee": dict(EMPLOYEE)},
        "gitlab_list_repositories",
        {},
    )
    assert result["isError"] is False
    assert seen == [("gitlab", "gitlab.repositories.list")]
    assert "group/project" in result["content"][0]["text"]


def test_loopback_route_and_codex_scope_are_wired_source_level() -> None:
    root = Path(__file__).resolve().parents[2]
    gateway = (root / "server/app/plugin_mcp_gateway.py").read_text(encoding="utf-8")
    install = (root / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    employee = (root / "server/app/employee_chat_runtime.py").read_text(encoding="utf-8")
    cleanup = (root / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    export = (root / "server/app/account_data_export.py").read_text(encoding="utf-8")

    assert 'router = APIRouter(prefix="/internal/fdex-plugin-mcp"' in gateway
    assert "_direct_loopback_client(request)" in gateway
    assert '"X-FDEX-Plugin-Capability"' in gateway
    assert '"default_tools_approval_mode": "writes"' in gateway
    assert "build_codex_plugin_mcp_config(owner_id, task_id)" in install
    assert "revoke_codex_plugin_mcp_task(owner_id, task_id)" in install
    assert "app.include_router(plugin_mcp_gateway_router)" in main
    assert "plugin_mcp_lease_store().purge_expired()" in main
    assert "plugin_agent_principal_store().bind" in employee
    assert "plugin_mcp_lease_store().delete_owner(clean)" in cleanup
    assert "plugin_agent_principal_store().delete_owner(clean)" in cleanup
    assert '"native_plugin_connections"' in export
    assert '"plugin_mcp_capability_tokens"' in export
