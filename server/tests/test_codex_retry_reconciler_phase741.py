from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent_loop import FdexAgentLoop
from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.agent_tasks import AgentTaskStore
from app.codex_retry_chain_store import CodexRetryChainStore

OWNER = "usr_phase741_owner_1234567890"


def _runtime(tmp_path: Path, monkeypatch):
    from app import agent_loop
    from app import codex_retry_controller as retry
    from app import codex_retry_reconciler as reconciler

    runtime = FdexAgentRuntime(workspace=tmp_path / "repo", worktree_root=tmp_path / "worktrees")
    runtime.enabled = True
    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    tasks.init()
    runtime.task_store = tasks

    # Phase 7.39 chain store shares the AgentTask database in production. Preserve that topology in
    # the regression instead of creating a second synthetic retry database.
    from app import codex_retry_chain_store as chain_module

    monkeypatch.setattr(chain_module, "agent_task_store", lambda: tasks)
    chain = CodexRetryChainStore()
    chain.init()

    monkeypatch.setattr(retry, "agent_task_store", lambda: tasks)
    monkeypatch.setattr(agent_loop, "codex_retry_chain_store", lambda: chain)
    monkeypatch.setattr(reconciler, "agent_task_store", lambda: tasks)
    monkeypatch.setattr(reconciler, "agent_runtime", lambda: runtime)
    monkeypatch.setattr(reconciler, "codex_retry_chain_store", lambda: chain)

    class NoHostState:
        def task_state(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(reconciler, "codex_host_store", lambda: NoHostState())
    return runtime, tasks, chain, reconciler


async def _root_and_child(runtime: FdexAgentRuntime):
    root = await runtime.create_task("perform durable work", owner_id=OWNER)
    root.status = "running"
    root.emit("test.root_running", "root execution lease was active")
    child = await runtime.create_task(
        root.prompt,
        owner_id=OWNER,
        parent_task_id=root.id,
        task_kind="auto_retry",
        logical_root_id=root.id,
        attempt_index=1,
    )
    return root, child


def _queue(chain: CodexRetryChainStore, root_id: str, child_id: str, *, backoff: float = 0.0) -> None:
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=root_id,
        attempt_task_id=child_id,
        parent_task_id=root_id,
        attempt_index=1,
        trigger_code="PROVIDER_UNREACHABLE",
        trigger_reason="structured transient health evidence",
        backoff_seconds=backoff,
        excluded_provider_ids={41},
    )


def test_phase741_reclaims_due_queued_retry_only_after_root_lease_is_free(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))
    _queue(chain, root.id, child.id)
    calls: list[str] = []

    async def fake_resume(self: FdexAgentLoop, task_id: str):
        calls.append(task_id)
        current = await self.runtime.get_task(task_id)
        assert current is not None
        await self.runtime.complete_task(current.id, "recovered child")
        logical = await self.runtime.get_task(current.logical_root_id)
        assert logical is not None
        return await self.runtime.complete_task(logical.id, "recovered logical root")

    monkeypatch.setattr(FdexAgentLoop, "run_from_retry_child", fake_resume)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["scanned"] == 1
    assert stats["recovered"] == 1
    assert calls == [child.id]
    durable_root = tasks.get(OWNER, root.id)
    durable_child = tasks.get(OWNER, child.id)
    assert durable_root is not None and durable_root["status"] == "succeeded"
    assert durable_child is not None and durable_child["status"] == "succeeded"
    assert any(item["type"] == "retry.reconcile_claimed" for item in durable_child["events"])


def test_phase741_never_scans_user_visible_tasks(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, _chain, reconciler = _runtime(tmp_path, monkeypatch)
    user = asyncio.run(runtime.create_task("ordinary queued task", owner_id=OWNER))

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["scanned"] == 0
    latest = asyncio.run(runtime.get_task(user.id))
    assert latest is not None and latest.status == "queued"


def test_phase741_missing_retry_audit_fails_closed_instead_of_guessing_provider_intent(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["blocked"] == 1
    durable_root = tasks.get(OWNER, root.id)
    durable_child = tasks.get(OWNER, child.id)
    assert durable_root is not None and durable_root["status"] == "failed"
    assert durable_child is not None and durable_child["status"] == "failed"
    repaired = chain.get_attempt(OWNER, child.id)
    assert repaired is not None
    assert repaired["decision_code"] == "RECOVERY_METADATA_MISSING"
    assert "Provider/backoff intent cannot be proven" in durable_root["error"]


def test_phase741_started_provider_boundary_is_not_replayed(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))
    _queue(chain, root.id, child.id)
    chain.record_started(
        owner_id=OWNER,
        root_task_id=root.id,
        attempt_task_id=child.id,
        parent_task_id=root.id,
        attempt_index=1,
        provider_id=77,
        provider_name="already-selected",
        model="codex-model",
    )
    called = [False]

    async def forbidden_resume(*_args, **_kwargs):
        called[0] = True
        raise AssertionError("started retry must never be replayed")

    monkeypatch.setattr(FdexAgentLoop, "run_from_retry_child", forbidden_resume)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["blocked"] == 1
    assert called == [False]
    assert tasks.get(OWNER, root.id)["status"] == "failed"  # type: ignore[index]
    assert tasks.get(OWNER, child.id)["status"] == "failed"  # type: ignore[index]
    audit = chain.get_attempt(OWNER, child.id)
    assert audit is not None and audit["decision_code"] == "ATTEMPT_ALREADY_STARTED"


def test_phase741_durable_turn_evidence_is_side_effect_unknown(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))
    _queue(chain, root.id, child.id)
    child.status = "running"
    child.emit("test.host_running", "host entered running state")

    class HostState:
        def task_state(self, owner_id: str, task_id: str, **_kwargs):
            assert owner_id == OWNER and task_id == child.id
            return {
                "turns": [
                    {
                        "task_id": child.id,
                        "turn_id": "turn-orphan",
                        "status": "inProgress",
                    }
                ]
            }

    monkeypatch.setattr(reconciler, "codex_host_store", lambda: HostState())

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["blocked"] == 1
    audit = chain.get_attempt(OWNER, child.id)
    assert audit is not None and audit["decision_code"] == "SIDE_EFFECT_UNKNOWN"


def test_phase741_respects_original_retry_backoff(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))
    _queue(chain, root.id, child.id, backoff=300.0)
    called = [False]

    async def forbidden_resume(*_args, **_kwargs):
        called[0] = True
        raise AssertionError("backoff retry must not run early")

    monkeypatch.setattr(FdexAgentLoop, "run_from_retry_child", forbidden_resume)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["backoff"] == 1
    assert called == [False]
    assert tasks.get(OWNER, root.id)["status"] == "running"  # type: ignore[index]
    assert tasks.get(OWNER, child.id)["status"] == "queued"  # type: ignore[index]


def test_phase741_direct_logical_run_rejects_internal_retry_child(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, chain, _reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))
    _queue(chain, root.id, child.id)

    with pytest.raises(AgentRuntimeError, match="cannot be run as logical roots"):
        asyncio.run(FdexAgentLoop(runtime).run(child.id))


def test_phase741_recovered_child_uses_existing_phase738_driver_and_completes_root(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime, tasks, chain, _reconciler = _runtime(tmp_path, monkeypatch)
    root, child = asyncio.run(_root_and_child(runtime))
    _queue(chain, root.id, child.id)
    monkeypatch.setattr(
        codex_engine,
        "codex_runtime_status",
        lambda: {
            "ready": True,
            "provider_id": 42,
            "provider_name": "recovery-provider",
            "model": "codex-model",
            "reason": "",
        },
    )
    calls: list[str] = []

    async def fake_host(runtime_arg: FdexAgentRuntime, task_id: str) -> None:
        calls.append(task_id)
        await runtime_arg.complete_task(task_id, "official Codex recovered the queued attempt")

    monkeypatch.setattr(codex_host_entry, "run_codex_task", fake_host)

    with tasks.run_lock(root.id):
        final = asyncio.run(FdexAgentLoop(runtime).run_from_retry_child(child.id))

    assert calls == [child.id]
    assert final.id == root.id
    assert final.status == "succeeded"
    assert final.result == "official Codex recovered the queued attempt"
    durable_child = tasks.get(OWNER, child.id)
    assert durable_child is not None and durable_child["status"] == "succeeded"
    audit = chain.get_attempt(OWNER, child.id)
    assert audit is not None and audit["provider_id"] == 42 and audit["state"] == "succeeded"


def test_phase741_source_contract_is_codex_only_and_wired_to_lifecycle() -> None:
    root = Path(__file__).parents[1] / "app"
    reconciler = (root / "codex_retry_reconciler.py").read_text(encoding="utf-8")
    loop = (root / "agent_loop.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")

    assert "task_kind='auto_retry'" in reconciler
    assert "with store.run_lock(root_id)" in reconciler
    assert "ATTEMPT_ALREADY_STARTED" in reconciler
    assert "SIDE_EFFECT_UNKNOWN" in reconciler
    assert "Provider/backoff intent cannot be proven" in reconciler
    assert "client_ai" not in reconciler
    assert "legacy" not in reconciler.lower()

    assert "run_from_retry_child" in loop
    assert "_drive_chain" in loop
    assert "internal automatic retry tasks cannot be run as logical roots" in loop
    assert "client_ai" not in loop
    assert "engine.fallback" not in loop

    assert "start_codex_retry_chain_reconciler" in main
    assert "stop_codex_retry_chain_reconciler" in main
