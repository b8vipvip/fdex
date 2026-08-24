from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "FDEX Test"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_capabilities_do_not_expose_arbitrary_shell(tmp_path: Path) -> None:
    runtime = FdexAgentRuntime(workspace=tmp_path)
    caps = runtime.capabilities()
    assert caps["arbitrary_shell"] is False
    assert caps["direct_main_write"] is False
    assert caps["github_connector"] is True
    assert caps["ai_source"] == "shared_provider_pool"
    assert caps["account_project_task_isolation"] is True
    assert caps["ephemeral_execution_sandbox"] == "systemd"
    assert caps["sandbox_max_concurrent"] >= 1
    assert "git_status" in caps["tools"]


def test_create_task_requires_enablement(tmp_path: Path) -> None:
    runtime = FdexAgentRuntime(workspace=tmp_path)
    runtime.enabled = False
    with pytest.raises(AgentRuntimeError, match="disabled"):
        asyncio.run(runtime.create_task("inspect project"))


def test_allowlisted_git_status_runs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    runtime = FdexAgentRuntime(workspace=tmp_path)
    runtime.enabled = True
    task = asyncio.run(runtime.create_task("inspect project"))
    task = asyncio.run(runtime.run_inspection(task.id, "git_status"))
    assert task.status == "succeeded"
    assert "No commits yet" not in task.result
    assert any(event.type == "tool.completed" for event in task.events)


def test_rejects_unknown_tool(tmp_path: Path) -> None:
    runtime = FdexAgentRuntime(workspace=tmp_path)
    runtime.enabled = True
    task = asyncio.run(runtime.create_task("inspect project"))
    with pytest.raises(AgentRuntimeError, match="tool not allowed"):
        asyncio.run(runtime.run_inspection(task.id, "shell"))
