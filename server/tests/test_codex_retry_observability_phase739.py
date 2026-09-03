from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.agent_runtime import AgentTask
from app.agent_tasks import AgentTaskStore

OWNER = "usr_phase739_owner_1234567890"
OTHER = "usr_phase739_other_1234567890"
ROOT = "1" * 32
CHILD = "2" * 32


def _stores(monkeypatch, tmp_path: Path):
    from app import codex_retry_chain_store as chain_module

    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    tasks.init()
    monkeypatch.setattr(chain_module, "agent_task_store", lambda: tasks)
    chain = chain_module.CodexRetryChainStore()
    chain.init()
    return tasks, chain


def _task(
    store: AgentTaskStore,
    task_id: str,
    *,
    parent: str = "",
    status: str = "running",
    worktree: str = "",
) -> AgentTask:
    task = AgentTask(
        id=task_id,
        prompt="inspect and fix the project",
        owner_id=OWNER,
        project_id=7,
        project_name="fdex",
        repository="b8vipvip/fdex",
        parent_task_id=parent,
        status=status,  # type: ignore[arg-type]
        worktree=worktree,
        _persist=store.save,
    )
    task.emit("task.created", "created")
    return task


def test_default_task_history_hides_internal_retry_but_accounting_keeps_it(monkeypatch, tmp_path: Path) -> None:
    tasks, chain = _stores(monkeypatch, tmp_path)
    root = _task(tasks, ROOT)
    child = _task(tasks, CHILD, parent=ROOT, worktree=str(tmp_path / "retry-worktree"))

    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=ROOT,
        parent_task_id="",
        attempt_index=0,
    )
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=CHILD,
        parent_task_id=ROOT,
        attempt_index=1,
        trigger_code="PROVIDER_RATE_LIMITED",
        trigger_reason="structured provider health reported rate limit",
        backoff_seconds=2,
        excluded_provider_ids={41},
    )

    assert [row["id"] for row in tasks.list(OWNER)] == [root.id]
    assert {row["id"] for row in tasks.list(OWNER, include_internal=True)} == {ROOT, CHILD}
    assert tasks.get(OWNER, CHILD) is not None
    assert tasks.active_count(OWNER) == 2

    child.status = "failed"
    child.emit("task.failed", "attempt failed")
    releasable = tasks.list_releasable(OWNER)
    assert [row["id"] for row in releasable] == [CHILD]


def test_retry_chain_is_owner_scoped_ordered_and_structured(monkeypatch, tmp_path: Path) -> None:
    _tasks, chain = _stores(monkeypatch, tmp_path)

    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=ROOT,
        parent_task_id="",
        attempt_index=0,
    )
    chain.record_started(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=ROOT,
        parent_task_id="",
        attempt_index=0,
        provider_id=41,
        provider_name="primary",
        model="gpt-codex-a",
    )
    chain.record_decision(
        owner_id=OWNER,
        attempt_task_id=ROOT,
        state="failed",
        decision_code="PROVIDER_RATE_LIMITED",
        decision_reason="structured transient signal",
        error="opaque upstream failure text",
    )
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=CHILD,
        parent_task_id=ROOT,
        attempt_index=1,
        trigger_code="PROVIDER_RATE_LIMITED",
        trigger_reason="structured transient signal",
        backoff_seconds=2,
        excluded_provider_ids={41},
    )
    chain.record_started(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=CHILD,
        parent_task_id=ROOT,
        attempt_index=1,
        provider_id=42,
        provider_name="alternate",
        model="gpt-codex-b",
    )

    projection = chain.chain_for_task(OWNER, ROOT)
    assert projection is not None
    assert projection["root_task_id"] == ROOT
    assert projection["attempt_count"] == 2
    assert projection["retry_count"] == 1
    assert projection["active_attempt_task_id"] == CHILD
    assert [item["attempt_index"] for item in projection["attempts"]] == [0, 1]
    assert projection["attempts"][0]["decision_code"] == "PROVIDER_RATE_LIMITED"
    assert projection["attempts"][1]["provider_id"] == 42
    assert projection["attempts"][1]["trigger_code"] == "PROVIDER_RATE_LIMITED"
    assert projection["attempts"][1]["backoff_seconds"] == 2
    assert projection["attempts"][1]["excluded_provider_ids"] == [41]

    child_projection = chain.chain_for_task(OWNER, CHILD)
    assert child_projection is not None and child_projection["requested_is_internal"] is True
    assert chain.chain_for_task(OTHER, ROOT) is None

    chain.record_terminal(owner_id=OWNER, attempt_task_id=CHILD, state="succeeded")
    completed = chain.chain_for_task(OWNER, ROOT)
    assert completed is not None
    assert completed["active_attempt_task_id"] == ""
    assert completed["latest_attempt_task_id"] == CHILD
    assert completed["latest_state"] == "succeeded"


def test_web_projection_follows_effective_attempt_and_redirects_internal_detail(monkeypatch, tmp_path: Path) -> None:
    from app import user_agent_task_routes as routes

    tasks, chain = _stores(monkeypatch, tmp_path)
    root = _task(tasks, ROOT)
    child = _task(tasks, CHILD, parent=ROOT)
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=ROOT,
        parent_task_id="",
        attempt_index=0,
    )
    chain.record_decision(
        owner_id=OWNER,
        attempt_task_id=ROOT,
        state="failed",
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="structured transient signal",
    )
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=CHILD,
        parent_task_id=ROOT,
        attempt_index=1,
        trigger_code="PROVIDER_UNREACHABLE",
    )
    chain.record_started(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=CHILD,
        parent_task_id=ROOT,
        attempt_index=1,
        provider_id=42,
        provider_name="alternate",
        model="codex-model",
    )

    monkeypatch.setattr(routes, "codex_retry_chain_store", lambda: chain)
    assert routes._effective_execution_task_id(OWNER, ROOT) == CHILD

    monkeypatch.setattr(routes, "_owner", lambda _request: ({"id": OWNER}, OWNER, None))
    monkeypatch.setattr(routes, "_task", lambda owner_id, task_id: tasks.get(owner_id, task_id))
    response = routes.agent_task_detail(CHILD, object())  # type: ignore[arg-type]
    assert response.status_code == 302
    assert response.headers["location"] == f"/account/agent/tasks/{ROOT}"
    assert root.id == ROOT and child.id == CHILD


def test_agent_api_retry_chain_projection_is_explicit_and_owner_scoped(monkeypatch, tmp_path: Path) -> None:
    from app import agent_retry_projection_routes as projection_routes
    from app import agent_routes

    tasks, chain = _stores(monkeypatch, tmp_path)
    _task(tasks, ROOT)
    _task(tasks, CHILD, parent=ROOT)
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=ROOT,
        parent_task_id="",
        attempt_index=0,
    )
    chain.record_queued(
        owner_id=OWNER,
        root_task_id=ROOT,
        attempt_task_id=CHILD,
        parent_task_id=ROOT,
        attempt_index=1,
        trigger_code="HOST_UNAVAILABLE",
        backoff_seconds=2,
    )

    projection_routes.install_agent_retry_projection_routes()
    monkeypatch.setattr(agent_routes, "_account_owner", lambda _request: (OWNER, "central"))
    monkeypatch.setattr(projection_routes, "agent_task_store", lambda: tasks)
    monkeypatch.setattr(projection_routes, "codex_retry_chain_store", lambda: chain)

    route = next(
        item
        for item in agent_routes.router.routes
        if getattr(item, "path", "").endswith("/tasks/{task_id}/retry-chain")
    )
    payload = asyncio.run(route.endpoint(ROOT, object()))  # type: ignore[arg-type]
    assert payload["task_id"] == ROOT
    assert payload["logical_task_id"] == ROOT
    assert payload["execution_task_id"] == CHILD
    assert payload["retry_chain"]["attempt_count"] == 2
    assert "worktree" not in str(payload["retry_chain"]).lower()


def test_phase739_source_contract_keeps_logical_and_execution_identity_separate() -> None:
    root = Path(__file__).parents[1] / "app"
    task_store = (root / "agent_tasks.py").read_text(encoding="utf-8")
    loop = (root / "agent_loop.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent.html").read_text(encoding="utf-8")
    api_projection = (root / "agent_retry_projection_routes.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")

    assert "codex_retry_attempts" in task_store
    assert "retry.attempt_index>0" in task_store
    assert "include_internal" in task_store
    assert "retry.auto_attempt" not in task_store  # visibility is structured, never event-text parsing

    assert "codex_retry_chain_store" in loop
    assert "decision.code" in loop and "decision.reason" in loop
    assert "run_codex_task" in loop
    assert "client_ai" not in loop
    assert "engine.fallback" not in loop

    assert 'id="codex-retry-chain"' in template
    assert "{% if task.status in ['queued','running'] %}<meta http-equiv=\"refresh\"" in template
    assert "not codex_session and task.status" not in template
    assert 'data-task-id="{{ execution_task_id or task.id }}"' in template
    assert '/account/agent/tasks/{{ execution_task_id or task.id }}/codex/interactions/' in template
    assert "excluded_provider_ids" in template

    assert '"/tasks/{task_id}/retry-chain"' in api_projection
    assert "install_agent_retry_projection_routes()" in main
    assert "api_key" not in api_projection.lower()
