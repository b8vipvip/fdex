from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent_loop import FdexAgentLoop
from app.agent_runtime import FdexAgentRuntime


def _runtime(tmp_path: Path) -> FdexAgentRuntime:
    runtime = FdexAgentRuntime(workspace=tmp_path, worktree_root=tmp_path / "worktrees")
    runtime.enabled = True
    return runtime


def test_agent_entry_always_uses_official_codex(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime = _runtime(tmp_path)
    task = asyncio.run(runtime.create_task("explain the current project"))
    calls: list[str] = []

    monkeypatch.setattr(
        codex_engine,
        "codex_runtime_status",
        lambda: {"ready": True, "reason": "", "runtime_version": "test"},
    )

    async def fake_codex(runtime_arg: FdexAgentRuntime, task_id: str) -> None:
        assert runtime_arg is runtime
        calls.append(task_id)
        await runtime_arg.complete_task(task_id, "handled by official Codex")

    monkeypatch.setattr(codex_host_entry, "run_codex_task", fake_codex)

    asyncio.run(FdexAgentLoop(runtime).run(task.id))
    completed = asyncio.run(runtime.get_task(task.id))

    assert calls == [task.id]
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.result == "handled by official Codex"


def test_agent_entry_fails_closed_when_codex_is_not_ready(monkeypatch, tmp_path: Path) -> None:
    from app import codex_engine, codex_host_entry

    runtime = _runtime(tmp_path)
    task = asyncio.run(runtime.create_task("run tests"))
    called = False

    monkeypatch.setattr(
        codex_engine,
        "codex_runtime_status",
        lambda: {"ready": False, "reason": "Provider has no fresh full Codex proof"},
    )

    async def forbidden_codex(*_args, **_kwargs) -> None:
        nonlocal called
        called = True
        raise AssertionError("Codex host must not start before rollout readiness")

    monkeypatch.setattr(codex_host_entry, "run_codex_task", forbidden_codex)

    asyncio.run(FdexAgentLoop(runtime).run(task.id))
    failed = asyncio.run(runtime.get_task(task.id))

    assert called is False
    assert failed is not None
    assert failed.status == "failed"
    assert "fresh full" in failed.error
    assert not any(event.type == "engine.fallback" for event in failed.events)


def test_agent_loop_api_no_longer_accepts_legacy_model_loop_injection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        FdexAgentLoop(runtime, model_call=lambda *_args: None)  # type: ignore[call-arg]
    except TypeError:
        pass
    else:  # pragma: no cover - regression guard
        raise AssertionError("legacy model_call injection unexpectedly remained available")
