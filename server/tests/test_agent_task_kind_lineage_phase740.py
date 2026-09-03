from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app import codex_retry_chain_store as retry_chain_module
from app import codex_retry_controller as retry_controller
from app.agent_runtime import AgentRuntimeError, AgentTask, FdexAgentRuntime
from app.agent_tasks import AgentTaskStore
from app.codex_retry_chain_store import CodexRetryChainStore
from app.codex_retry_controller import RetryDecision, create_auto_retry_child


OWNER = "usr_1234567890abcdef12345678"


def _store(tmp_path: Path) -> AgentTaskStore:
    return AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")


def _runtime(tmp_path: Path) -> FdexAgentRuntime:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = FdexAgentRuntime(workspace=workspace, worktree_root=tmp_path / "worktrees")
    runtime.enabled = True
    runtime.task_store = _store(tmp_path / "store")
    return runtime


def test_auto_retry_identity_is_durable_before_retry_ledger_exists(monkeypatch, tmp_path: Path) -> None:
    """The exact Phase 7.40 crash window must be safe without a retry-ledger row."""

    runtime = _runtime(tmp_path)
    root = asyncio.run(runtime.create_task("repair transient failure", owner_id=OWNER))
    decision = RetryDecision(
        True,
        "HOST_UNAVAILABLE",
        "native Codex Host was transiently unavailable",
        delay_seconds=2.0,
    )
    monkeypatch.setattr(retry_controller, "_safe_retry_checkpoint", lambda _source: None)

    child = asyncio.run(
        create_auto_retry_child(
            runtime,
            root,
            retry_number=1,
            decision=decision,
        )
    )

    stored = runtime.task_store.get(OWNER, child.id)
    assert stored is not None
    assert stored["task_kind"] == "auto_retry"
    assert stored["logical_root_id"] == root.id
    assert stored["attempt_index"] == 1
    assert stored["parent_task_id"] == root.id

    # create_auto_retry_child deliberately does not own Phase 7.39 audit-ledger insertion; the
    # Agent loop writes that immediately afterwards. Simulate a worker dying between those writes.
    with runtime.task_store.db() as conn:
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM codex_retry_attempts WHERE attempt_task_id=?",
                (child.id,),
            ).fetchone()[0]
        )
    assert count == 0

    visible = runtime.task_store.list(OWNER, include_internal=False)
    all_rows = runtime.task_store.list(OWNER, include_internal=True)
    assert [row["id"] for row in visible] == [root.id]
    assert {row["id"] for row in all_rows} == {root.id, child.id}

    lineage = runtime.task_store.list_execution_lineage(OWNER, root.id)
    assert [(row["id"], row["task_kind"], row["attempt_index"]) for row in lineage] == [
        (root.id, "user", 0),
        (child.id, "auto_retry", 1),
    ]

    monkeypatch.setattr(retry_chain_module, "agent_task_store", lambda: runtime.task_store)
    chain = CodexRetryChainStore().chain_for_task(OWNER, child.id)
    assert chain is not None
    assert chain["root_task_id"] == root.id
    assert chain["requested_is_internal"] is True
    assert chain["active_attempt_task_id"] == child.id
    assert chain["retry_count"] == 1
    assert chain["attempts"][1]["audit_pending"] is True


def test_task_identity_is_immutable_against_stale_worker_save(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root_id = "a" * 32
    child_id = "b" * 32
    root = AgentTask(
        id=root_id,
        prompt="root",
        owner_id=OWNER,
        logical_root_id=root_id,
        _persist=store.save,
    )
    root.emit("task.created", "root")
    child = AgentTask(
        id=child_id,
        prompt="retry",
        owner_id=OWNER,
        parent_task_id=root_id,
        task_kind="auto_retry",
        logical_root_id=root_id,
        attempt_index=1,
        _persist=store.save,
    )
    child.emit("task.created", "child")

    # Simulate a stale pre-7.40-shaped in-memory view trying to write the same row as a normal task.
    stale = AgentTask(
        id=child_id,
        prompt="retry",
        owner_id=OWNER,
        parent_task_id=root_id,
        task_kind="user",
        logical_root_id=child_id,
        attempt_index=0,
        _persist=store.save,
    )
    stale.emit("agent.progress", "late stale worker event")

    restored = store.get(OWNER, child_id)
    assert restored is not None
    assert restored["task_kind"] == "auto_retry"
    assert restored["logical_root_id"] == root_id
    assert restored["attempt_index"] == 1
    assert {event["message"] for event in restored["events"]} == {"child", "late stale worker event"}


def test_runtime_assigns_visible_task_kinds_and_new_logical_roots(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    root = asyncio.run(runtime.create_task("first task", owner_id=OWNER))
    assert root.task_kind == "user"
    assert root.logical_root_id == root.id
    assert root.attempt_index == 0

    root.status = "failed"
    root.error = "failed"
    root.emit("task.failed", "failed")
    manual = asyncio.run(runtime.retry_task(OWNER, root.id))
    assert manual.task_kind == "manual_retry"
    assert manual.parent_task_id == root.id
    assert manual.logical_root_id == manual.id
    assert manual.attempt_index == 0

    resume = asyncio.run(
        runtime.create_task(
            "continue",
            owner_id=OWNER,
            parent_task_id=root.id,
            task_kind="resume",
        )
    )
    fork = asyncio.run(
        runtime.create_task(
            "try another path",
            owner_id=OWNER,
            parent_task_id=root.id,
            task_kind="fork",
        )
    )
    assert (resume.task_kind, resume.logical_root_id, resume.attempt_index) == ("resume", resume.id, 0)
    assert (fork.task_kind, fork.logical_root_id, fork.attempt_index) == ("fork", fork.id, 0)

    with pytest.raises(AgentRuntimeError, match="logical root"):
        asyncio.run(
            runtime.create_task(
                "broken retry",
                owner_id=OWNER,
                parent_task_id=root.id,
                task_kind="auto_retry",
                attempt_index=1,
            )
        )
    with pytest.raises(AgentRuntimeError, match="attempt index"):
        asyncio.run(
            runtime.create_task(
                "broken user task",
                owner_id=OWNER,
                attempt_index=2,
            )
        )


def test_phase739_database_migrates_internal_retry_identity_without_text_heuristics(tmp_path: Path) -> None:
    path = tmp_path / "legacy-phase739.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE agent_tasks (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            prompt TEXT NOT NULL,
            project_id INTEGER,
            project_name TEXT NOT NULL DEFAULT '',
            repository TEXT NOT NULL DEFAULT '',
            base_branch TEXT NOT NULL DEFAULT 'main',
            status TEXT NOT NULL DEFAULT 'queued',
            result TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT '',
            worktree TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '',
            pushed INTEGER NOT NULL DEFAULT 0,
            pr_url TEXT NOT NULL DEFAULT '',
            changed_files_json TEXT NOT NULL DEFAULT '[]',
            events_json TEXT NOT NULL DEFAULT '[]',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            parent_task_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE codex_retry_attempts (
            attempt_task_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            root_task_id TEXT NOT NULL,
            parent_task_id TEXT NOT NULL DEFAULT '',
            attempt_index INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'queued',
            provider_id INTEGER NOT NULL DEFAULT 0,
            provider_name TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            trigger_code TEXT NOT NULL DEFAULT '',
            trigger_reason TEXT NOT NULL DEFAULT '',
            decision_code TEXT NOT NULL DEFAULT '',
            decision_reason TEXT NOT NULL DEFAULT '',
            backoff_seconds REAL NOT NULL DEFAULT 0,
            excluded_provider_ids_json TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    root_id = "c" * 32
    child_id = "d" * 32
    now = "2026-09-03T00:00:00+00:00"
    base_values = (
        OWNER,
        "prompt",
        None,
        "",
        "",
        "main",
        "running",
        "",
        "",
        "",
        "",
        "",
        0,
        "",
        "[]",
        "[]",
        0,
    )
    conn.execute(
        """
        INSERT INTO agent_tasks(
            id,owner_id,prompt,project_id,project_name,repository,base_branch,status,result,error,
            branch,worktree,commit_sha,pushed,pr_url,changed_files_json,events_json,cancel_requested,
            parent_task_id,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (root_id, *base_values, "", now, now),
    )
    conn.execute(
        """
        INSERT INTO agent_tasks(
            id,owner_id,prompt,project_id,project_name,repository,base_branch,status,result,error,
            branch,worktree,commit_sha,pushed,pr_url,changed_files_json,events_json,cancel_requested,
            parent_task_id,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (child_id, *base_values, root_id, now, now),
    )
    conn.execute(
        """
        INSERT INTO codex_retry_attempts(
            attempt_task_id,owner_id,root_task_id,parent_task_id,attempt_index,state,
            created_at,updated_at
        ) VALUES(?,?,?,?,?,'queued',?,?)
        """,
        (child_id, OWNER, root_id, root_id, 1, now, now),
    )
    conn.commit()
    conn.close()

    store = AgentTaskStore(path, tmp_path / "locks")
    store.init()
    root = store.get(OWNER, root_id)
    child = store.get(OWNER, child_id)
    assert root is not None and child is not None
    assert (root["task_kind"], root["logical_root_id"], root["attempt_index"]) == ("user", root_id, 0)
    assert (child["task_kind"], child["logical_root_id"], child["attempt_index"]) == (
        "auto_retry",
        root_id,
        1,
    )
    assert [row["id"] for row in store.list(OWNER)] == [root_id]


def test_task_kind_wiring_is_explicit_for_retry_resume_and_fork() -> None:
    root = Path(__file__).parents[1] / "app"
    runtime_source = (root / "agent_runtime.py").read_text(encoding="utf-8")
    retry_source = (root / "codex_retry_controller.py").read_text(encoding="utf-8")
    host_source = (root / "codex_host_runtime.py").read_text(encoding="utf-8")
    store_source = (root / "agent_tasks.py").read_text(encoding="utf-8")

    assert 'task_kind="manual_retry"' in runtime_source
    assert 'task_kind="auto_retry"' in retry_source
    assert 'task_kind="fork" if fork else "resume"' in host_source
    assert "task_kind<>'auto_retry'" in store_source
    assert "NOT EXISTS" not in store_source.split("def list(", 1)[1].split("def list_execution_lineage", 1)[0]
