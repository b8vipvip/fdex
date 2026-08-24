from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from app.agent_loop import FdexAgentLoop, parse_decision
from app.agent_runtime import FdexAgentRuntime


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "FDEX Test"], cwd=path, check=True)
    (path / "README.md").write_text("# fdex agent test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_parse_decision_accepts_only_allowlisted_tool() -> None:
    decision = parse_decision(
        json.dumps({"action": "tool", "tool": "git_status", "summary": "Inspecting repository"}),
        ("git_status",),
    )
    assert decision.tool == "git_status"


def test_agent_loop_runs_tool_then_finishes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    runtime = FdexAgentRuntime(workspace=tmp_path)
    runtime.enabled = True
    task = asyncio.run(runtime.create_task("inspect repository state"))
    responses = iter(
        [
            json.dumps({"action": "tool", "tool": "git_status", "summary": "Checking repository status"}),
            json.dumps({"action": "final", "answer": "Repository is clean.", "summary": "Inspection complete"}),
        ]
    )
    prompts: list[str] = []

    async def fake_model(system: str, prompt: str, max_tokens: int) -> str:
        assert "shared provider-management pool" in system
        assert "shell access" in system
        assert max_tokens >= 128
        prompts.append(prompt)
        return next(responses)

    asyncio.run(FdexAgentLoop(runtime, model_call=fake_model, max_steps=3).run(task.id))
    completed = asyncio.run(runtime.get_task(task.id))
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.result == "Repository is clean."
    assert any(event.type == "tool.completed" for event in completed.events)
    assert any(event.type == "agent.progress" for event in completed.events)
    assert "TOOL: git_status" in prompts[1]
    assert "OWNER SCOPE:" in prompts[0]


def test_agent_loop_rejects_model_tool_escalation(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    runtime = FdexAgentRuntime(workspace=tmp_path)
    runtime.enabled = True
    task = asyncio.run(runtime.create_task("delete everything"))

    async def fake_model(system: str, prompt: str, max_tokens: int) -> str:
        return json.dumps({"action": "tool", "tool": "shell", "summary": "Trying shell"})

    asyncio.run(FdexAgentLoop(runtime, model_call=fake_model, max_steps=2).run(task.id))
    failed = asyncio.run(runtime.get_task(task.id))
    assert failed is not None
    assert failed.status == "failed"
    assert "disallowed tool" in failed.error
    assert not any(event.type == "tool.started" for event in failed.events)


def test_agent_loop_stops_at_step_limit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    runtime = FdexAgentRuntime(workspace=tmp_path)
    runtime.enabled = True
    task = asyncio.run(runtime.create_task("keep inspecting"))

    async def fake_model(system: str, prompt: str, max_tokens: int) -> str:
        return json.dumps({"action": "tool", "tool": "git_status", "summary": "Inspecting again"})

    asyncio.run(FdexAgentLoop(runtime, model_call=fake_model, max_steps=2).run(task.id))
    failed = asyncio.run(runtime.get_task(task.id))
    assert failed is not None
    assert failed.status == "failed"
    assert "maximum steps (2)" in failed.error
