from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import account_cleanup
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


def test_cross_worker_cancel_flag_cannot_be_overwritten_by_stale_runner(tmp_path: Path) -> None:
    store = _store(tmp_path)
    owner = "usr_1234567890abcdef12345678"
    stale_runner = AgentTask(
        id="c" * 32,
        prompt="long task",
        owner_id=owner,
        status="running",
        _persist=store.save,
    )
    stale_runner.emit("agent.started", "running")

    canceled = store.request_cancel(owner, stale_runner.id)
    assert canceled["cancel_requested"] is True
    # Simulate an older worker emitting progress after another worker requested cancel.
    stale_runner.emit("agent.progress", "stale progress")
    latest = store.get(owner, stale_runner.id)
    assert latest is not None
    assert latest["cancel_requested"] is True


def test_orphan_cancel_is_terminal_and_stale_worker_cannot_revive_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    owner = "usr_1234567890abcdef12345678"
    stale_runner = AgentTask(
        id="d" * 32,
        prompt="orphaned task",
        owner_id=owner,
        status="running",
        _persist=store.save,
    )
    stale_runner.emit("agent.started", "running")

    canceled = store.request_cancel(owner, stale_runner.id, force_terminal=True)
    assert canceled["status"] == "canceled"
    stale_runner.emit("agent.progress", "late worker event")

    latest = store.get(owner, stale_runner.id)
    assert latest is not None
    assert latest["status"] == "canceled"
    assert latest["cancel_requested"] is True
    assert {item["message"] for item in latest["events"]} == {"running", "late worker event"}


def test_releasable_worktree_is_not_hidden_by_newer_clean_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    owner = "usr_1234567890abcdef12345678"
    oldest = AgentTask(
        id=f"{1:032x}",
        prompt="old workspace",
        owner_id=owner,
        status="succeeded",
        worktree=str(tmp_path / "old-worktree"),
        _persist=store.save,
    )
    oldest.emit("task.completed", "old")
    for index in range(2, 152):
        clean = AgentTask(
            id=f"{index:032x}",
            prompt="already cleaned",
            owner_id=owner,
            status="succeeded",
            _persist=store.save,
        )
        clean.emit("task.completed", f"clean-{index}")

    rows = store.list_releasable(owner, limit=100)
    assert [row["id"] for row in rows] == [oldest.id]


def test_cleanup_releases_worktree_but_preserves_local_commit_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "FDEX Test"], cwd=repo, check=True)
    (repo / "app.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, stdout=subprocess.PIPE)

    runtime = FdexAgentRuntime(workspace=repo, worktree_root=tmp_path / "worktrees")
    runtime.task_store = _store(tmp_path / "task-store")
    runtime.enabled = True
    task = asyncio.run(runtime.create_task("update app"))
    asyncio.run(runtime.execute_tool(task.id, "replace_text", args={"path": "app.txt", "old": "old", "new": "new"}))
    asyncio.run(runtime.execute_tool(task.id, "git_commit", args={"message": "Update app"}))
    asyncio.run(runtime.complete_task(task.id, "done"))
    worktree = Path(task.worktree)
    branch = task.branch

    runtime._release_worktree(task)

    assert not worktree.exists()
    subprocess.run(["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=repo, check=True)
    stored = runtime.task_store.get(task.owner_id, task.id)
    assert stored is not None
    assert stored["worktree"] == ""


def test_authenticated_accounts_cannot_use_shared_legacy_workspace() -> None:
    from app import agent_routes

    with pytest.raises(HTTPException) as exc:
        agent_routes._require_scoped_project("central", None)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        agent_routes._require_scoped_project("agent-account-legacy", None)
    agent_routes._require_scoped_project("bootstrap-legacy", None)


def test_task_payload_does_not_expose_server_worktree_path() -> None:
    from app import agent_routes

    payload = agent_routes._task_payload(
        AgentTask(id="e" * 32, prompt="safe response", worktree="/opt/fdex/server/data/secret-path")
    )
    assert "worktree" not in payload


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


def test_account_deletion_is_blocked_before_memory_erasure_when_agent_task_is_active(monkeypatch) -> None:
    calls: list[str] = []

    class FakeTasks:
        def active_count(self, owner_id: str) -> int:
            calls.append("active")
            return 1

    async def fake_memory(owner_id: str):
        calls.append("memory")
        return {"completed": True}

    monkeypatch.setattr(account_cleanup, "agent_task_store", lambda: FakeTasks())
    monkeypatch.setattr(account_cleanup, "erase_account_memory", fake_memory)

    with pytest.raises(ValueError, match="Coding Agent"):
        account_cleanup.purge_owned_agent_resources("usr_1234567890abcdef12345678")
    assert calls == ["active"]
