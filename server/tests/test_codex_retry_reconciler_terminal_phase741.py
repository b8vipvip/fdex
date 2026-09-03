from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.agent_loop import FdexAgentLoop
from app.agent_runtime import FdexAgentRuntime
from app.agent_tasks import AgentTaskStore
from app.codex_retry_chain_store import CodexRetryChainStore

OWNER = "usr_phase741_terminal_owner"


def _env(tmp_path: Path, monkeypatch):
    from app import agent_loop
    from app import codex_retry_reconciler as reconciler
    from app import codex_retry_chain_store as chain_module

    runtime = FdexAgentRuntime(workspace=tmp_path / "repo", worktree_root=tmp_path / "worktrees")
    runtime.enabled = True
    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    tasks.init()
    runtime.task_store = tasks
    monkeypatch.setattr(chain_module, "agent_task_store", lambda: tasks)
    chain = CodexRetryChainStore()
    chain.init()
    monkeypatch.setattr(agent_loop, "codex_retry_chain_store", lambda: chain)
    monkeypatch.setattr(reconciler, "agent_task_store", lambda: tasks)
    monkeypatch.setattr(reconciler, "agent_runtime", lambda: runtime)
    monkeypatch.setattr(reconciler, "codex_retry_chain_store", lambda: chain)

    class NoHostState:
        def task_state(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(reconciler, "codex_host_store", lambda: NoHostState())
    return runtime, tasks, chain, reconciler


async def _chain(runtime: FdexAgentRuntime, chain: CodexRetryChainStore):
    root = await runtime.create_task("durable terminal projection", owner_id=OWNER)
    root.status = "running"
    root.emit("test.running", "root running")
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=root.id,
        attempt_task_id=root.id,
        parent_task_id="",
        attempt_index=0,
    )
    chain.record_started(
        owner_id=OWNER,
        root_task_id=root.id,
        attempt_task_id=root.id,
        parent_task_id="",
        attempt_index=0,
        provider_id=11,
        provider_name="source",
        model="codex",
    )
    chain.record_retry_plan(
        owner_id=OWNER,
        root_task_id=root.id,
        source_attempt_task_id=root.id,
        source_attempt_index=0,
        next_attempt_index=1,
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="structured retry",
        error="source failed",
        backoff_seconds=0,
        excluded_provider_ids={11},
    )
    child = await runtime.create_task(
        root.prompt,
        owner_id=OWNER,
        parent_task_id=root.id,
        task_kind="auto_retry",
        logical_root_id=root.id,
        attempt_index=1,
    )
    chain.attach_transition_child(owner_id=OWNER, source_attempt_task_id=root.id, child_task_id=child.id)
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=root.id,
        attempt_task_id=child.id,
        parent_task_id=root.id,
        attempt_index=1,
        trigger_code="PROVIDER_UNREACHABLE",
        trigger_reason="structured retry",
        backoff_seconds=0,
        excluded_provider_ids={11},
    )
    chain.record_started(
        owner_id=OWNER,
        root_task_id=root.id,
        attempt_task_id=child.id,
        parent_task_id=root.id,
        attempt_index=1,
        provider_id=12,
        provider_name="retry",
        model="codex",
    )
    return root, child


def test_phase741_projects_durable_child_success_to_root_without_rerunning_codex(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _env(tmp_path, monkeypatch)
    root, child = asyncio.run(_chain(runtime, chain))
    asyncio.run(runtime.complete_task(child.id, "durable successful retry result"))
    chain.record_terminal(owner_id=OWNER, attempt_task_id=child.id, state="succeeded")

    async def forbidden_run(*_args, **_kwargs):
        raise AssertionError("terminal child must never be rerun")

    monkeypatch.setattr(FdexAgentLoop, "run_from_retry_child", forbidden_run)
    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["recovered"] == 1
    root_row = tasks.get(OWNER, root.id)
    assert root_row is not None
    assert root_row["status"] == "succeeded"
    assert root_row["result"] == "durable successful retry result"
    assert tasks.get(OWNER, child.id)["status"] == "succeeded"  # type: ignore[index]
    transition = chain.get_transition_for_source(OWNER, root.id)
    assert transition is not None and transition["state"] == "settled"


def test_phase741_projects_terminal_child_failure_to_root_without_replay(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _env(tmp_path, monkeypatch)
    root, child = asyncio.run(_chain(runtime, chain))
    asyncio.run(runtime.fail_task(child.id, "durable retry failure"))
    chain.record_decision(
        owner_id=OWNER,
        attempt_task_id=child.id,
        state="failed",
        decision_code="RETRY_LIMIT_REACHED",
        decision_reason="bounded retry budget exhausted",
        error="durable retry failure",
    )

    async def forbidden_run(*_args, **_kwargs):
        raise AssertionError("terminal failed child must never be replayed")

    monkeypatch.setattr(FdexAgentLoop, "run_from_retry_child", forbidden_run)
    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["blocked"] == 1
    root_row = tasks.get(OWNER, root.id)
    assert root_row is not None and root_row["status"] == "failed"
    assert "durable retry failure" in root_row["error"]
    transition = chain.get_transition_for_source(OWNER, root.id)
    assert transition is not None and transition["state"] == "blocked"


def test_phase741_transition_journal_is_additive_on_phase740_database(monkeypatch, tmp_path: Path) -> None:
    from app import codex_retry_chain_store as chain_module

    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    tasks.init()
    # Write an old Phase 7.40-style task/attempt before the 7.41 store initializes.
    with tasks.db() as conn:
        now = "2026-09-03T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO agent_tasks(
                id,owner_id,project_id,project_name,repository,base_branch,prompt,status,result,error,
                branch,worktree,commit_sha,pushed,pr_url,changed_files_json,cancel_requested,parent_task_id,
                task_kind,logical_root_id,attempt_index,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "a" * 32, OWNER, None, "Local FDEX", "", "main", "old", "failed", "", "old failure",
                "", "", "", 0, "", "[]", 0, "", "user", "a" * 32, 0, now, now,
            ),
        )
        conn.execute(
            """
            INSERT INTO codex_retry_attempts(
                owner_id,root_task_id,attempt_task_id,parent_task_id,attempt_index,state,
                trigger_code,trigger_reason,decision_code,decision_reason,provider_id,provider_name,model,
                backoff_seconds,excluded_provider_ids_json,error,started_at,completed_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                OWNER, "a" * 32, "a" * 32, "", 0, "failed", "", "", "HARD_BLOCK", "old proof",
                7, "provider", "codex", 0, "[]", "old failure", now, now, now, now,
            ),
        )

    monkeypatch.setattr(chain_module, "agent_task_store", lambda: tasks)
    chain = CodexRetryChainStore()
    chain.init()

    old = chain.get_attempt(OWNER, "a" * 32)
    assert old is not None and old["decision_code"] == "HARD_BLOCK"
    with sqlite3.connect(tasks.path) as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='codex_retry_transitions'"
        ).fetchone()
    assert table is not None
    assert chain.get_transition_for_source(OWNER, "a" * 32) is None
