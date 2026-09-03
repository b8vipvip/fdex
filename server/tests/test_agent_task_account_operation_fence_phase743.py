from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.account_operations import (
    AccountOperationBusy,
    account_operation,
    account_operation_status,
    mark_account_deleted,
)
from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.agent_tasks import AgentTaskStore

OWNER = "usr_phase743_owner_1234567890"


def _runtime(tmp_path: Path, monkeypatch) -> tuple[FdexAgentRuntime, AgentTaskStore]:
    from app import account_operations

    lock_root = tmp_path / "account-operations"
    lock_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(account_operations, "_lock_dir", lambda: lock_root)

    runtime = FdexAgentRuntime(workspace=tmp_path / "repo", worktree_root=tmp_path / "worktrees")
    runtime.enabled = True
    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "task-locks")
    tasks.init()
    runtime.task_store = tasks
    return runtime, tasks


def test_phase743_account_delete_lock_blocks_first_durable_task_write(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks = _runtime(tmp_path, monkeypatch)

    with account_operation(OWNER, "account_delete"):
        with pytest.raises(AgentRuntimeError, match="account data operation is in progress: account_delete"):
            asyncio.run(runtime.create_task("must not survive deletion fence", owner_id=OWNER))

    assert tasks.list(OWNER, limit=20) == []
    assert tasks.active_count(OWNER) == 0


def test_phase743_first_task_persist_occurs_while_account_flock_is_held(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks = _runtime(tmp_path, monkeypatch)
    original_save = tasks.save
    observed_busy: list[bool] = []
    observed_operation: list[str] = []

    def guarded_save(task) -> None:
        status = account_operation_status(task.owner_id)
        observed_busy.append(status.busy)
        observed_operation.append(status.operation)
        original_save(task)

    monkeypatch.setattr(tasks, "save", guarded_save)

    created = asyncio.run(runtime.create_task("durable first write", owner_id=OWNER))

    assert created.owner_id == OWNER
    assert observed_busy == [True]
    assert observed_operation == ["agent_task_create"]
    row = tasks.get(OWNER, created.id)
    assert row is not None and row["status"] == "queued"


def test_phase743_deleted_account_tombstone_rejects_stale_task_creation(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks = _runtime(tmp_path, monkeypatch)
    mark_account_deleted(OWNER)

    with pytest.raises(AgentRuntimeError, match="FDEX account has been deleted"):
        asyncio.run(runtime.create_task("stale response after account deletion", owner_id=OWNER))

    assert tasks.list(OWNER, limit=20) == []


def test_phase743_auto_retry_child_uses_same_account_operation_fence(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(runtime.create_task("root task", owner_id=OWNER))

    with account_operation(OWNER, "memory_clear"):
        with pytest.raises(AgentRuntimeError, match="account data operation is in progress: memory_clear"):
            asyncio.run(
                runtime.create_task(
                    root.prompt,
                    owner_id=OWNER,
                    parent_task_id=root.id,
                    task_kind="auto_retry",
                    logical_root_id=root.id,
                    attempt_index=1,
                )
            )

    lineage = tasks.list_execution_lineage(OWNER, root.id)
    assert [row["id"] for row in lineage] == [root.id]
    assert all(row["task_kind"] != "auto_retry" for row in lineage)


def test_phase743_bootstrap_owner_keeps_non_account_task_creation_semantics(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks = _runtime(tmp_path, monkeypatch)

    task = asyncio.run(runtime.create_task("bootstrap local task", owner_id="local"))

    assert task.owner_id == "local"
    row = tasks.get("local", task.id)
    assert row is not None and row["status"] == "queued"


def test_phase743_source_contract_uses_runtime_first_write_fence() -> None:
    source = (Path(__file__).parents[1] / "app" / "agent_runtime.py").read_text(encoding="utf-8")

    assert 'account_operation(owner, "agent_task_create")' in source
    assert 'account_deleted_by_hash(account_hash(owner))' in source
    assert 'task.emit("task.created"' in source
    assert 'account_operation_task_create_fence": True' in source
    assert "AccountOperationBusy" in source
    assert "client_ai" not in source
