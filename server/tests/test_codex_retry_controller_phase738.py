from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import codex_provider_rollout as rollout
from app import codex_retry_controller as retry
from app.agent_loop import FdexAgentLoop
from app.agent_runtime import AgentTaskCancelled, FdexAgentRuntime
from app.agent_tasks import AgentTaskStore
from app.codex_host_store import CodexHostStore
from app.codex_retry_provider_context import (
    codex_retry_provider_exclusions,
    excluded_codex_provider_ids,
)


def _runtime(tmp_path: Path, monkeypatch) -> FdexAgentRuntime:
    runtime = FdexAgentRuntime(workspace=tmp_path / "repo", worktree_root=tmp_path / "worktrees")
    runtime.enabled = True
    runtime.task_store = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    host_store = CodexHostStore(tmp_path / "codex-host.db")
    monkeypatch.setattr(retry, "agent_task_store", lambda: runtime.task_store)
    monkeypatch.setattr(retry, "codex_host_store", lambda: host_store)
    return runtime


def _health(
    *,
    state: str = "DEGRADED",
    code: str = "PROVIDER_RATE_LIMITED",
    provider_state: str = "rate_limited",
    alternate: bool = False,
) -> dict:
    providers = [
        {
            "provider_id": 1,
            "provider_name": "primary",
            "state": provider_state,
            "status_code": 429 if provider_state == "rate_limited" else None,
        }
    ]
    compatibility = [
        {"provider_id": 1, "provider_name": "primary", "eligible": True, "level": "full"}
    ]
    if alternate:
        providers.append(
            {
                "provider_id": 2,
                "provider_name": "backup",
                "state": "ok",
                "status_code": 200,
            }
        )
        compatibility.append(
            {"provider_id": 2, "provider_name": "backup", "eligible": True, "level": "full"}
        )
    return {
        "state": state,
        "code": code,
        "reason": code,
        "providers": providers,
        "compatibility": compatibility,
    }


def test_phase738_transient_health_allows_bounded_retry(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))
    monkeypatch.setattr(retry, "run_codex_agent_health_check", lambda **_kwargs: asyncio.sleep(0, result=_health()))
    monkeypatch.setattr(retry, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))

    decision = asyncio.run(retry.decide_codex_retry(task, retry_number=1, failed_provider_id=1))

    assert decision.retry is True
    assert decision.code == "PROVIDER_RATE_LIMITED"
    assert decision.delay_seconds == 0.0
    assert decision.excluded_provider_ids == frozenset()


def test_phase738_hard_health_block_never_retries(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))

    async def health(**_kwargs):
        return _health(state="BLOCKED", code="SMOKE_EXPIRED", provider_state="ok")

    monkeypatch.setattr(retry, "run_codex_agent_health_check", health)
    decision = asyncio.run(retry.decide_codex_retry(task, retry_number=1, failed_provider_id=1))

    assert decision.retry is False
    assert decision.code == "SMOKE_EXPIRED"


def test_phase738_metadata_auth_probe_is_advisory_not_retry_trigger(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))

    async def health(**_kwargs):
        return _health(
            state="DEGRADED",
            code="PROVIDER_AUTH_PROBE_FAILED",
            provider_state="auth_error",
        )

    monkeypatch.setattr(retry, "run_codex_agent_health_check", health)
    decision = asyncio.run(retry.decide_codex_retry(task, retry_number=1, failed_provider_id=1))

    assert decision.retry is False
    assert decision.code == "PROVIDER_AUTH_PROBE_FAILED"


def test_phase738_side_effect_boundary_prevents_replay_without_health_call(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("change code"))
    task.changed_files.add("server/app/main.py")

    async def forbidden_health(**_kwargs):
        raise AssertionError("health must not be queried after replay safety boundary is crossed")

    monkeypatch.setattr(retry, "run_codex_agent_health_check", forbidden_health)
    decision = asyncio.run(retry.decide_codex_retry(task, retry_number=1, failed_provider_id=1))

    assert decision.retry is False
    assert decision.code == "SIDE_EFFECT_BOUNDARY_REACHED"


def test_phase738_healthy_full_alternate_excludes_failed_provider_only_for_new_task(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))

    async def health(**_kwargs):
        return _health(alternate=True)

    monkeypatch.setattr(retry, "run_codex_agent_health_check", health)
    monkeypatch.setattr(retry, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    decision = asyncio.run(retry.decide_codex_retry(task, retry_number=1, failed_provider_id=1))

    assert decision.retry is True
    assert decision.excluded_provider_ids == frozenset({1})
    assert excluded_codex_provider_ids() == frozenset()
    with codex_retry_provider_exclusions(set(decision.excluded_provider_ids)):
        assert excluded_codex_provider_ids() == frozenset({1})
    assert excluded_codex_provider_ids() == frozenset()


def test_phase738_rollout_reselection_still_requires_fresh_full(monkeypatch) -> None:
    providers = [
        {
            "id": 1,
            "name": "primary",
            "base_url": "https://one.example/v1",
            "api_key": "key-one",
            "enabled": True,
            "protocol_order": ["responses"],
            "main_text_model": "gpt-test-1",
            "backup_text_models": [],
        },
        {
            "id": 2,
            "name": "backup",
            "base_url": "https://two.example/v1",
            "api_key": "key-two",
            "enabled": True,
            "protocol_order": ["responses"],
            "main_text_model": "gpt-test-2",
            "backup_text_models": [],
        },
    ]

    class Providers:
        def list(self, **_kwargs):
            return providers

    class Compatibility:
        def evaluate(self, provider, _runtime, **_kwargs):
            # Both are fresh-full for the first assertion. The second assertion flips the backup
            # invalid to prove exclusion cannot bypass the compatibility seal.
            valid = int(provider["id"]) != 2 or backup_valid[0]
            return {
                "valid": valid,
                "reason": "fresh full" if valid else "smoke expired",
                "level": "full" if valid else "none",
                "age_hours": 1,
            }

    backup_valid = [True]
    monkeypatch.setattr(rollout, "provider_store", lambda: Providers())
    monkeypatch.setattr(rollout, "codex_provider_compatibility_store", lambda: Compatibility())
    runtime = SimpleNamespace(path="/codex", version="test", source="test")

    with codex_retry_provider_exclusions({1}):
        selected = rollout.rollout_selection(runtime)["provider"]
        assert selected is not None and selected.provider_id == 2

    backup_valid[0] = False
    with codex_retry_provider_exclusions({1}):
        selected = rollout.rollout_selection(runtime)["provider"]
        assert selected is None


def test_phase738_failure_capture_does_not_reopen_terminal_tasks(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))

    with retry.capture_codex_attempt(task.id, root_task_id=task.id, provider_id=7) as capture:
        captured = asyncio.run(runtime.fail_task(task.id, "upstream failed"))
        assert captured.status == "running"
        assert capture.failed is True
        assert capture.provider_id == 7

    failed = asyncio.run(retry.terminalize_task_failure(runtime, task.id, "upstream failed"))
    assert failed.status == "failed"
    assert failed.error == "upstream failed"


def test_phase738_fail_task_outside_attempt_scope_remains_fail_closed(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))

    failed = asyncio.run(runtime.fail_task(task.id, "hard failure"))

    assert failed.status == "failed"
    assert failed.error == "hard failure"


def test_phase738_root_cancel_propagates_to_active_retry_child(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(runtime.create_task("long task"))
    root.status = "running"
    root.emit("test.running", "root now running")
    child = asyncio.run(runtime.create_task("long task", parent_task_id=root.id))

    asyncio.run(runtime.request_cancel(root.owner_id, root.id))
    with retry.capture_codex_attempt(child.id, root_task_id=root.id, provider_id=1):
        with pytest.raises(AgentTaskCancelled):
            asyncio.run(runtime._raise_if_cancelled(child))

    latest = asyncio.run(runtime.get_task(child.id))
    assert latest is not None
    assert latest.status == "canceled"


def test_phase738_retry_budget_is_exactly_two(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    task = asyncio.run(runtime.create_task("inspect project"))

    async def forbidden_health(**_kwargs):
        raise AssertionError("budget rejection must not run a health probe")

    monkeypatch.setattr(retry, "run_codex_agent_health_check", forbidden_health)
    decision = asyncio.run(retry.decide_codex_retry(task, retry_number=retry.MAX_AUTO_RETRIES + 1, failed_provider_id=1))
    assert decision.retry is False
    assert decision.code == "RETRY_LIMIT_REACHED"


def test_phase738_agent_loop_transparently_recovers_root_with_new_child(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(runtime.create_task("do the requested work"))
    calls: list[str] = []

    monkeypatch.setattr(
        codex_engine,
        "codex_runtime_status",
        lambda: {"ready": True, "provider_id": 1, "provider_name": "primary", "reason": ""},
    )

    async def fake_host(runtime_arg: FdexAgentRuntime, task_id: str) -> None:
        calls.append(task_id)
        if len(calls) == 1:
            await runtime_arg.fail_task(task_id, "opaque transport failure text")
        else:
            await runtime_arg.complete_task(task_id, "recovered by official Codex")

    async def health(**_kwargs):
        return _health()

    monkeypatch.setattr(codex_host_entry, "run_codex_task", fake_host)
    monkeypatch.setattr(retry, "run_codex_agent_health_check", health)
    monkeypatch.setattr(retry, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))

    final = asyncio.run(FdexAgentLoop(runtime).run(root.id))
    durable_root = asyncio.run(runtime.get_task(root.id))
    rows = runtime.task_store.list(root.owner_id, limit=20)
    children = [row for row in rows if str(row.get("parent_task_id") or "") == root.id]

    assert len(calls) == 2
    assert calls[0] == root.id
    assert calls[1] != root.id
    assert final.id == root.id
    assert durable_root is not None
    assert durable_root.status == "succeeded"
    assert durable_root.result == "recovered by official Codex"
    assert len(children) == 1
    assert children[0]["id"] == calls[1]
    assert any(event.type == "retry.auto_recovered" for event in durable_root.events)


def test_phase738_nontransient_failure_terminalizes_without_child(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(runtime.create_task("do the requested work"))
    calls: list[str] = []

    monkeypatch.setattr(codex_engine, "codex_runtime_status", lambda: {"ready": True, "provider_id": 1})

    async def fake_host(runtime_arg: FdexAgentRuntime, task_id: str) -> None:
        calls.append(task_id)
        await runtime_arg.fail_task(task_id, "this text intentionally mentions 429 but must not classify retry")

    async def health(**_kwargs):
        return _health(state="READY", code="READY", provider_state="ok")

    monkeypatch.setattr(codex_host_entry, "run_codex_task", fake_host)
    monkeypatch.setattr(retry, "run_codex_agent_health_check", health)
    monkeypatch.setattr(retry, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))

    final = asyncio.run(FdexAgentLoop(runtime).run(root.id))
    rows = runtime.task_store.list(root.owner_id, limit=20)

    assert calls == [root.id]
    assert final.status == "failed"
    assert len(rows) == 1
    assert "429" in final.error


def test_phase738_exhaustion_runs_original_plus_two_children_only(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime = _runtime(tmp_path, monkeypatch)
    root = asyncio.run(runtime.create_task("always transiently fail"))
    calls: list[str] = []

    monkeypatch.setattr(codex_engine, "codex_runtime_status", lambda: {"ready": True, "provider_id": 1})

    async def fake_host(runtime_arg: FdexAgentRuntime, task_id: str) -> None:
        calls.append(task_id)
        await runtime_arg.fail_task(task_id, "opaque failure")

    async def health(**_kwargs):
        return _health()

    monkeypatch.setattr(codex_host_entry, "run_codex_task", fake_host)
    monkeypatch.setattr(retry, "run_codex_agent_health_check", health)
    monkeypatch.setattr(retry, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))

    final = asyncio.run(FdexAgentLoop(runtime).run(root.id))
    rows = runtime.task_store.list(root.owner_id, limit=20)

    assert len(calls) == 1 + retry.MAX_AUTO_RETRIES
    assert len(set(calls)) == len(calls)
    assert len(rows) == 1 + retry.MAX_AUTO_RETRIES
    assert final.status == "failed"
    assert any(event.type == "retry.auto_exhausted" for event in final.events)


def test_phase738_source_contains_no_generic_ai_or_legacy_fallback() -> None:
    root = Path(__file__).resolve().parents[2]
    loop_source = (root / "server/app/agent_loop.py").read_text(encoding="utf-8")
    retry_source = (root / "server/app/codex_retry_controller.py").read_text(encoding="utf-8")
    combined = loop_source + "\n" + retry_source

    assert "client_ai" not in combined
    assert "engine.fallback" not in combined
    assert "legacy model" not in combined.lower()
    assert "MAX_AUTO_RETRIES = 2" in retry_source
