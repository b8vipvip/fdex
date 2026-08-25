from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent_runtime import AgentTask, FdexAgentRuntime
from app.agent_sandbox import SystemdExecutionSandbox
from app.agent_tasks import AgentTaskStore, TaskRunBusy


def _store(tmp_path: Path) -> AgentTaskStore:
    return AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")


def test_task_store_round_trip_is_owner_scoped_and_durable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = AgentTask(
        id="a" * 32,
        prompt="fix the project",
        owner_id="usr_1234567890abcdef12345678",
        project_id=7,
        project_name="demo",
        repository="owner/repo",
        _persist=store.save,
    )
    task.changed_files.add("server/app/demo.py")
    task.emit("task.created", "created")
    task.emit("agent.progress", "working")

    row = store.get(task.owner_id, task.id)
    assert row is not None
    assert row["prompt"] == "fix the project"
    assert row["changed_files"] == ["server/app/demo.py"]
    assert [item["type"] for item in row["events"]] == ["task.created", "agent.progress"]
    assert store.get("usr_abcdef1234567890abcdef12", task.id) is None

    reopened = AgentTaskStore(store.path, store.lock_root)
    restored = reopened.get(task.owner_id, task.id)
    assert restored is not None
    assert restored["repository"] == "owner/repo"


def test_task_run_lock_rejects_second_worker(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task_id = "b" * 32
    with store.run_lock(task_id):
        with pytest.raises(TaskRunBusy):
            with store.run_lock(task_id):
                pass


def test_runtime_can_restore_cancel_and_retry_task_after_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _store(tmp_path)

    first = FdexAgentRuntime(workspace=workspace)
    first.task_store = store
    first.enabled = True
    created = asyncio.run(first.create_task("inspect durable task", owner_id="usr_1234567890abcdef12345678"))

    second = FdexAgentRuntime(workspace=workspace)
    second.task_store = store
    second.enabled = True
    restored = asyncio.run(second.get_task(created.id))
    assert restored is not None
    assert restored.prompt == "inspect durable task"

    canceled = asyncio.run(second.request_cancel(restored.owner_id, restored.id))
    assert canceled.status == "canceled"
    assert canceled.cancel_requested is True

    retried = asyncio.run(second.retry_task(restored.owner_id, restored.id))
    assert retried.status == "queued"
    assert retried.parent_task_id == restored.id
    assert retried.id != restored.id


def test_sandbox_usage_and_cache_cleanup_are_owner_scoped(tmp_path: Path) -> None:
    sandbox = SystemdExecutionSandbox()
    sandbox.sandbox_root = (tmp_path / "sandboxes").resolve()
    sandbox.account_disk_mb = 1
    owner = "usr_1234567890abcdef12345678"
    cache = sandbox.account_cache_root(owner)
    (cache / "pip" / "cache.bin").write_bytes(b"x" * 1024)
    project = sandbox._owner_root(owner) / "projects" / "1" / "repository"
    project.mkdir(parents=True)
    (project / "README.md").write_bytes(b"y" * 2048)

    usage = sandbox.account_usage(owner)
    assert usage["used_bytes"] >= 3072
    assert usage["cache_bytes"] >= 1024
    assert usage["workspace_bytes"] >= 2048
    assert usage["limit_mb"] == 1

    removed = sandbox.clear_account_cache(owner)
    assert removed >= 1024
    assert sandbox.account_usage(owner)["cache_bytes"] == 0
    assert (project / "README.md").exists()
