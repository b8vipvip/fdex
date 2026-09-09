from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app import plugin_code_host_workflow, plugin_code_hosts, plugin_mcp_gateway


OWNER = "usr_plugin_workflow_745"
ROOT = Path(__file__).resolve().parents[2]


def _connection(plugin_id: str) -> dict[str, str]:
    return {
        "plugin_id": plugin_id,
        "base_url": "https://gitlab.com" if plugin_id == "gitlab" else "https://gitee.com",
        "token": "provider-secret",
    }


def test_workflow_branch_namespace_fails_closed_against_direct_main_writes() -> None:
    assert plugin_code_host_workflow._workflow_branch("fdex/task-123") == "fdex/task-123"
    with pytest.raises(ValueError, match="工作分支"):
        plugin_code_host_workflow._workflow_branch("main")
    with pytest.raises(ValueError, match="工作分支"):
        plugin_code_host_workflow._workflow_branch("fdex/../main")
    with pytest.raises(ValueError, match="工作分支"):
        plugin_code_host_workflow._workflow_branch("fdex/a//b")


def test_gitlab_tree_uses_repository_tree_api_with_bounded_provider_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_code_hosts, "_connection", lambda owner_id, plugin_id: _connection("gitlab"))
    calls: list[dict[str, Any]] = []

    def fake_request(plugin_id: str, base_url: str, token: str, method: str, path: str, **kwargs: Any):
        calls.append({"plugin_id": plugin_id, "method": method, "path": path, **kwargs})
        return httpx.Response(
            200,
            json=[
                {"id": "tree-sha", "name": "app", "type": "tree", "path": "src/app", "mode": "040000"},
                {"id": "blob-sha", "name": "main.py", "type": "blob", "path": "src/main.py", "mode": "100644"},
            ],
        )

    monkeypatch.setattr(plugin_code_hosts, "_request", fake_request)
    result = plugin_code_host_workflow.list_code_host_tree(
        OWNER,
        "gitlab",
        "group/project",
        path="src",
        ref="main",
        recursive=False,
    )
    assert result["count"] == 2
    assert result["entries"][0]["type"] == "tree"
    assert result["entries"][1]["type"] == "file"
    assert calls == [
        {
            "plugin_id": "gitlab",
            "method": "GET",
            "path": "/projects/group%2Fproject/repository/tree",
            "params": {"ref": "main", "path": "src", "recursive": "false", "per_page": 100, "page": 1},
        }
    ]


def test_gitee_tree_filters_requested_directory_without_leaking_other_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_code_hosts, "_connection", lambda owner_id, plugin_id: _connection("gitee"))

    def fake_request(plugin_id: str, base_url: str, token: str, method: str, path: str, **kwargs: Any):
        return httpx.Response(
            200,
            json={
                "sha": "root",
                "truncated": False,
                "tree": [
                    {"path": "README.md", "type": "blob", "sha": "1", "mode": "100644"},
                    {"path": "src/a.py", "type": "blob", "sha": "2", "mode": "100644"},
                    {"path": "src/lib", "type": "tree", "sha": "3", "mode": "040000"},
                    {"path": "src/lib/b.py", "type": "blob", "sha": "4", "mode": "100644"},
                ],
            },
        )

    monkeypatch.setattr(plugin_code_hosts, "_request", fake_request)
    shallow = plugin_code_host_workflow.list_code_host_tree(
        OWNER,
        "gitee",
        "alice/project",
        path="src",
        ref="master",
        recursive=False,
    )
    assert [item["path"] for item in shallow["entries"]] == ["src/a.py", "src/lib"]

    recursive = plugin_code_host_workflow.list_code_host_tree(
        OWNER,
        "gitee",
        "alice/project",
        path="src",
        ref="master",
        recursive=True,
    )
    assert [item["path"] for item in recursive["entries"]] == ["src/a.py", "src/lib", "src/lib/b.py"]


def test_create_branch_uses_provider_native_payloads_and_fdex_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    current = {"plugin": "gitlab"}
    monkeypatch.setattr(plugin_code_hosts, "_connection", lambda owner_id, plugin_id: _connection(current["plugin"]))
    calls: list[dict[str, Any]] = []

    def fake_request(plugin_id: str, base_url: str, token: str, method: str, path: str, **kwargs: Any):
        calls.append({"plugin_id": plugin_id, "method": method, "path": path, **kwargs})
        return httpx.Response(201, json={"name": "fdex/task-1", "commit": {"id": "abc123"}, "protected": False})

    monkeypatch.setattr(plugin_code_hosts, "_request", fake_request)
    gitlab = plugin_code_host_workflow.create_code_host_branch(
        OWNER,
        "gitlab",
        "group/project",
        branch="fdex/task-1",
        source_ref="main",
    )
    assert gitlab["commit_sha"] == "abc123"
    assert calls[-1]["path"] == "/projects/group%2Fproject/repository/branches"
    assert calls[-1]["json_body"] == {"branch": "fdex/task-1", "ref": "main"}

    current["plugin"] = "gitee"
    gitee = plugin_code_host_workflow.create_code_host_branch(
        OWNER,
        "gitee",
        "alice/project",
        branch="fdex/task-2",
        source_ref="master",
    )
    assert gitee["branch"] == "fdex/task-1"
    assert calls[-1]["path"] == "/repos/alice/project/branches"
    assert calls[-1]["json_body"] == {"refs": "master", "branch_name": "fdex/task-2"}


def test_coding_agent_write_and_pr_wrappers_require_isolated_work_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[dict[str, Any]] = []
    prs: list[dict[str, Any]] = []

    def fake_write(owner_id: str, plugin_id: str, repository: str, path: str, content: str, **kwargs: Any):
        writes.append({"branch": kwargs["branch"], "repository": repository, "path": path})
        return {"plugin_id": plugin_id, "repository": repository, "path": path, "branch": kwargs["branch"]}

    def fake_pr(owner_id: str, plugin_id: str, repository: str, **kwargs: Any):
        prs.append(dict(kwargs))
        return {"plugin_id": plugin_id, "repository": repository, **kwargs}

    monkeypatch.setattr(plugin_code_hosts, "write_code_host_file", fake_write)
    monkeypatch.setattr(plugin_code_hosts, "create_code_host_pull_request", fake_pr)

    with pytest.raises(ValueError, match="fdex/"):
        plugin_code_host_workflow.workflow_write_code_host_file(
            OWNER, "gitlab", "group/project", "a.txt", "x", branch="main", message="unsafe"
        )
    plugin_code_host_workflow.workflow_write_code_host_file(
        OWNER, "gitlab", "group/project", "a.txt", "x", branch="fdex/task-3", message="safe"
    )
    assert writes == [{"branch": "fdex/task-3", "repository": "group/project", "path": "a.txt"}]

    with pytest.raises(ValueError, match="fdex/"):
        plugin_code_host_workflow.workflow_create_code_host_pull_request(
            OWNER,
            "gitlab",
            "group/project",
            source_branch="main",
            target_branch="release",
            title="unsafe",
        )
    with pytest.raises(ValueError, match="目标分支"):
        plugin_code_host_workflow.workflow_create_code_host_pull_request(
            OWNER,
            "gitlab",
            "group/project",
            source_branch="fdex/task-3",
            target_branch="fdex/review",
            title="unsafe target",
        )
    plugin_code_host_workflow.workflow_create_code_host_pull_request(
        OWNER,
        "gitlab",
        "group/project",
        source_branch="fdex/task-3",
        target_branch="main",
        title="safe",
    )
    assert prs[-1]["source_branch"] == "fdex/task-3"
    assert prs[-1]["target_branch"] == "main"


def test_phase745_installs_tree_and_branch_tools_into_existing_plugin_mcp_authorization_path() -> None:
    plugin_code_host_workflow.install_code_host_workflow_tools()
    assert "gitlab_list_tree" in plugin_mcp_gateway._TOOL_DEFINITIONS
    assert "gitlab_create_branch" in plugin_mcp_gateway._TOOL_DEFINITIONS
    assert "gitee_list_tree" in plugin_mcp_gateway._TOOL_DEFINITIONS
    assert "gitee_create_branch" in plugin_mcp_gateway._TOOL_DEFINITIONS
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["gitlab_list_tree"]["runtime_tool"] == "gitlab.repository.read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["gitlab_create_branch"]["runtime_tool"] == "gitlab.repository.write"
    assert "fdex/*" in plugin_mcp_gateway._TOOL_DEFINITIONS["gitlab_write_file"]["description"]

    install_source = (ROOT / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")
    assert "install_code_host_workflow_tools()" in install_source
    assert "Phase 7.45" in install_source
