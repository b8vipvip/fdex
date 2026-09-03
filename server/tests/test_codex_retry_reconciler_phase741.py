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

    # Attempts, transitions and AgentTask identity share one SQLite file in production. Preserve
    # that topology so transaction/crash tests exercise the real durability boundary.
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


async def _running_root(runtime: FdexAgentRuntime, chain: CodexRetryChainStore):
    root = await runtime.create_task("perform durable work", owner_id=OWNER)
    root.status = "running"
    root.emit("test.root_running", "root execution lease was active")
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
        provider_id=41,
        provider_name="failed-provider",
        model="codex-model",
    )
    root.error = "opaque failed Host transport"
    root.emit("retry.attempt_failed", "attempt failed before the original worker exited")
    return root


def _plan(
    chain: CodexRetryChainStore,
    root_id: str,
    *,
    source_id: str | None = None,
    source_index: int = 0,
    next_index: int = 1,
    backoff: float = 0.0,
):
    return chain.record_retry_plan(
        owner_id=OWNER,
        root_task_id=root_id,
        source_attempt_task_id=source_id or root_id,
        source_attempt_index=source_index,
        next_attempt_index=next_index,
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="structured transient health evidence",
        error="opaque failed Host transport",
        backoff_seconds=backoff,
        excluded_provider_ids={41},
    )


async def _raw_child(runtime: FdexAgentRuntime, root_id: str, *, parent_id: str | None = None, index: int = 1):
    return await runtime.create_task(
        "perform durable work",
        owner_id=OWNER,
        parent_task_id=parent_id or root_id,
        task_kind="auto_retry",
        logical_root_id=root_id,
        attempt_index=index,
    )


def _queue_child(
    chain: CodexRetryChainStore,
    root_id: str,
    child_id: str,
    *,
    parent_id: str | None = None,
    index: int = 1,
    backoff: float = 0.0,
) -> None:
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=root_id,
        attempt_task_id=child_id,
        parent_task_id=parent_id or root_id,
        attempt_index=index,
        trigger_code="PROVIDER_UNREACHABLE",
        trigger_reason="structured transient health evidence",
        backoff_seconds=backoff,
        excluded_provider_ids={41},
    )


def _successful_reclaimer(monkeypatch):
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
    return calls


def test_phase741_retry_decision_and_next_intent_are_one_durable_transaction(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, chain, _reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))

    transition = _plan(chain, root.id, backoff=8.0)
    source_audit = chain.get_attempt(OWNER, root.id)

    assert source_audit is not None
    assert source_audit["state"] == "failed"
    assert source_audit["decision_code"] == "PROVIDER_UNREACHABLE"
    assert transition["source_attempt_task_id"] == root.id
    assert transition["source_attempt_index"] == 0
    assert transition["next_attempt_index"] == 1
    assert transition["backoff_seconds"] == 8.0
    assert transition["excluded_provider_ids"] == [41]
    assert transition["child_task_id"] == ""
    assert transition["state"] == "planned"


def test_phase741_crash_after_plan_before_child_creation_is_recovered(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    _plan(chain, root.id)
    calls = _successful_reclaimer(monkeypatch)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["scanned"] == 1
    assert stats["recovered"] == 1
    assert len(calls) == 1
    child_id = calls[0]
    durable_child = tasks.get(OWNER, child_id)
    assert durable_child is not None
    assert durable_child["task_kind"] == "auto_retry"
    assert durable_child["logical_root_id"] == root.id
    assert durable_child["attempt_index"] == 1
    audit = chain.get_attempt(OWNER, child_id)
    assert audit is not None
    assert audit["trigger_code"] == "PROVIDER_UNREACHABLE"
    transition = chain.get_transition_for_source(OWNER, root.id)
    assert transition is not None and transition["child_task_id"] == child_id
    assert tasks.get(OWNER, root.id)["status"] == "succeeded"  # type: ignore[index]


def test_phase741_crash_after_child_main_row_before_attach_or_audit_is_recovered(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    _plan(chain, root.id)
    child = asyncio.run(_raw_child(runtime, root.id))
    assert chain.get_attempt(OWNER, child.id) is None
    assert chain.get_transition_for_source(OWNER, root.id)["child_task_id"] == ""  # type: ignore[index]
    calls = _successful_reclaimer(monkeypatch)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["recovered"] == 1
    assert calls == [child.id]
    repaired = chain.get_attempt(OWNER, child.id)
    assert repaired is not None
    assert repaired["trigger_code"] == "PROVIDER_UNREACHABLE"
    assert repaired["excluded_provider_ids"] == [41]
    transition = chain.get_transition_for_source(OWNER, root.id)
    assert transition is not None and transition["child_task_id"] == child.id
    assert tasks.get(OWNER, child.id)["status"] == "succeeded"  # type: ignore[index]


def test_phase741_adopts_pre741_fully_audited_queued_child_without_transition(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    chain.record_decision(
        owner_id=OWNER,
        attempt_task_id=root.id,
        state="failed",
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="old Phase 7.40 structured retry decision",
        error="opaque failed Host transport",
    )
    child = asyncio.run(_raw_child(runtime, root.id))
    _queue_child(chain, root.id, child.id)
    assert chain.get_transition_for_source(OWNER, root.id) is None
    calls = _successful_reclaimer(monkeypatch)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["recovered"] == 1
    assert calls == [child.id]
    adopted = chain.get_transition_for_source(OWNER, root.id)
    assert adopted is not None
    assert adopted["child_task_id"] == child.id
    assert adopted["decision_code"] == "PROVIDER_UNREACHABLE"


def test_phase741_running_root_without_transition_fails_closed_instead_of_hanging(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["blocked"] == 1
    durable = tasks.get(OWNER, root.id)
    assert durable is not None and durable["status"] == "failed"
    assert "before a durable retry transition was committed" in durable["error"]
    audit = chain.get_attempt(OWNER, root.id)
    assert audit is not None and audit["decision_code"] == "ORPHAN_ATTEMPT_NO_TRANSITION"


def test_phase741_never_replays_an_ordinary_queued_task(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, _chain, reconciler = _runtime(tmp_path, monkeypatch)
    user = asyncio.run(runtime.create_task("ordinary queued task", owner_id=OWNER))

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["scanned"] == 0
    latest = asyncio.run(runtime.get_task(user.id))
    assert latest is not None and latest.status == "queued"


def test_phase741_started_provider_boundary_is_not_replayed(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    _plan(chain, root.id)
    child = asyncio.run(_raw_child(runtime, root.id))
    chain.attach_transition_child(owner_id=OWNER, source_attempt_task_id=root.id, child_task_id=child.id)
    _queue_child(chain, root.id, child.id)
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
    root = asyncio.run(_running_root(runtime, chain))
    _plan(chain, root.id)
    child = asyncio.run(_raw_child(runtime, root.id))
    chain.attach_transition_child(owner_id=OWNER, source_attempt_task_id=root.id, child_task_id=child.id)
    _queue_child(chain, root.id, child.id)
    child.status = "running"
    child.emit("test.host_running", "host entered running state")

    class HostState:
        def task_state(self, owner_id: str, task_id: str, **_kwargs):
            if task_id != child.id:
                return None
            assert owner_id == OWNER
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


def test_phase741_respects_transition_backoff_and_materializes_child_without_running_early(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    _plan(chain, root.id, backoff=300.0)
    called = [False]

    async def forbidden_resume(*_args, **_kwargs):
        called[0] = True
        raise AssertionError("backoff retry must not run early")

    monkeypatch.setattr(FdexAgentLoop, "run_from_retry_child", forbidden_resume)

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["backoff"] == 1
    assert called == [False]
    lineage = tasks.list_execution_lineage(OWNER, root.id)
    children = [row for row in lineage if row["task_kind"] == "auto_retry"]
    assert len(children) == 1 and children[0]["status"] == "queued"
    transition = chain.get_transition_for_source(OWNER, root.id)
    assert transition is not None and transition["child_task_id"] == children[0]["id"]
    assert tasks.get(OWNER, root.id)["status"] == "running"  # type: ignore[index]


def test_phase741_transition_cannot_exceed_phase738_retry_budget(monkeypatch, tmp_path: Path) -> None:
    runtime, tasks, chain, reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    first = asyncio.run(_raw_child(runtime, root.id, index=1))
    _queue_child(chain, root.id, first.id, index=1)
    first.status = "failed"
    first.emit("test.failed", "first retry failed")
    second = asyncio.run(_raw_child(runtime, root.id, parent_id=first.id, index=2))
    _queue_child(chain, root.id, second.id, parent_id=first.id, index=2)
    second.status = "running"
    second.emit("test.running", "second retry failed before terminalization")
    chain.record_decision(
        owner_id=OWNER,
        attempt_task_id=second.id,
        state="failed",
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="transient but budget should be exhausted",
        error="opaque failure",
    )
    chain.record_retry_plan(
        owner_id=OWNER,
        root_task_id=root.id,
        source_attempt_task_id=second.id,
        source_attempt_index=2,
        next_attempt_index=3,
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="corrupt over-budget transition",
        error="opaque failure",
        backoff_seconds=0,
        excluded_provider_ids=(),
    )

    stats = asyncio.run(reconciler.reconcile_codex_retry_chains_once())

    assert stats["blocked"] == 1
    assert tasks.get(OWNER, root.id)["status"] == "failed"  # type: ignore[index]
    transition = chain.get_transition_for_source(OWNER, second.id)
    assert transition is not None and transition["state"] == "blocked"


def test_phase741_direct_logical_run_rejects_internal_retry_child(monkeypatch, tmp_path: Path) -> None:
    runtime, _tasks, chain, _reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    child = asyncio.run(_raw_child(runtime, root.id))
    _queue_child(chain, root.id, child.id)

    with pytest.raises(AgentRuntimeError, match="cannot be run as logical roots"):
        asyncio.run(FdexAgentLoop(runtime).run(child.id))


def test_phase741_recovered_child_uses_existing_phase738_driver_and_completes_root(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime, tasks, chain, _reconciler = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(_running_root(runtime, chain))
    _plan(chain, root.id)
    child = asyncio.run(_raw_child(runtime, root.id))
    chain.attach_transition_child(owner_id=OWNER, source_attempt_task_id=root.id, child_task_id=child.id)
    _queue_child(chain, root.id, child.id)
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


def test_phase741_source_contract_is_codex_only_transition_journal_and_lifecycle() -> None:
    root = Path(__file__).parents[1] / "app"
    reconciler = (root / "codex_retry_reconciler.py").read_text(encoding="utf-8")
    loop = (root / "agent_loop.py").read_text(encoding="utf-8")
    ledger = (root / "codex_retry_chain_store.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")

    assert "root.status='running'" in reconciler
    assert "with store.run_lock(root_id)" in reconciler
    assert "ATTEMPT_ALREADY_STARTED" in reconciler
    assert "SIDE_EFFECT_UNKNOWN" in reconciler
    assert "ORPHAN_ATTEMPT_NO_TRANSITION" in reconciler
    assert "record_transition_from_existing_child" in reconciler
    assert "client_ai" not in reconciler
    assert "legacy" not in reconciler.lower()

    assert "record_retry_plan" in loop
    assert "attach_transition_child" in loop
    assert "run_from_retry_child" in loop
    assert "_drive_chain" in loop
    assert "internal automatic retry tasks cannot be run as logical roots" in loop
    assert "MAX_AUTO_RETRIES" in loop
    assert "client_ai" not in loop
    assert "engine.fallback" not in loop

    assert "CREATE TABLE IF NOT EXISTS codex_retry_transitions" in ledger
    assert "BEGIN IMMEDIATE" in ledger
    assert "next_attempt_index" in ledger
    assert "excluded_provider_ids_json" in ledger

    assert "start_codex_retry_chain_reconciler" in main
    assert "stop_codex_retry_chain_reconciler" in main
