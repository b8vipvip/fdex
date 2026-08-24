from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.agent_loop import FdexAgentLoop
from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "FDEX Test"], cwd=path, check=True)
    (path / "app.txt").write_text("value=old\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _runtime(repo: Path, worktrees: Path) -> FdexAgentRuntime:
    runtime = FdexAgentRuntime(workspace=repo, worktree_root=worktrees)
    runtime.enabled = True
    return runtime


def test_write_is_isolated_from_source_workspace(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    runtime = _runtime(repo, tmp_path / "worktrees")
    task = asyncio.run(runtime.create_task("change value"))

    output = asyncio.run(
        runtime.execute_tool(
            task.id,
            "replace_text",
            args={"path": "app.txt", "old": "value=old", "new": "value=new"},
        )
    )
    current = asyncio.run(runtime.get_task(task.id))
    assert current is not None
    assert "replaced one occurrence" in output
    assert (repo / "app.txt").read_text(encoding="utf-8") == "value=old\n"
    assert Path(current.worktree, "app.txt").read_text(encoding="utf-8") == "value=new\n"
    assert current.branch.startswith("fdex-agent/")


def test_agent_rejects_path_escape_and_protected_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    runtime = _runtime(repo, tmp_path / "worktrees")
    task = asyncio.run(runtime.create_task("try invalid path"))

    with pytest.raises(AgentRuntimeError, match="escapes"):
        asyncio.run(runtime.execute_tool(task.id, "read_file", args={"path": "../outside.txt"}))

    task2 = asyncio.run(runtime.create_task("try secret"))
    with pytest.raises(AgentRuntimeError, match="protected"):
        asyncio.run(runtime.execute_tool(task2.id, "write_file", args={"path": ".env", "content": "TOKEN=x"}))


def test_commit_contains_only_agent_written_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    runtime = _runtime(repo, tmp_path / "worktrees")
    task = asyncio.run(runtime.create_task("change value"))
    asyncio.run(
        runtime.execute_tool(
            task.id,
            "replace_text",
            args={"path": "app.txt", "old": "value=old", "new": "value=new"},
        )
    )
    current = asyncio.run(runtime.get_task(task.id))
    assert current is not None
    Path(current.worktree, "untracked.log").write_text("do not commit\n", encoding="utf-8")

    output = asyncio.run(runtime.execute_tool(task.id, "git_commit", args={"message": "Update app value"}))
    current = asyncio.run(runtime.get_task(task.id))
    assert current is not None
    assert current.commit_sha
    assert "app.txt" in output
    show = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", current.commit_sha],
        cwd=current.worktree,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert "app.txt" in show
    assert "untracked.log" not in show


def test_failed_test_is_observation_then_agent_repairs_and_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    test_dir = repo / "server"
    test_dir.mkdir()
    (test_dir / "test_sample.py").write_text(
        "from pathlib import Path\n\ndef test_value():\n    assert Path('../app.txt').read_text().strip() == 'value=new'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "server/test_sample.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add test"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    runtime = _runtime(repo, tmp_path / "worktrees")
    task = asyncio.run(runtime.create_task("make the test pass and commit"))
    responses = iter(
        [
            {"action": "tool", "tool": "run_tests", "args": {"suite": "server"}, "summary": "Running tests"},
            {"action": "tool", "tool": "replace_text", "args": {"path": "app.txt", "old": "value=old", "new": "value=new"}, "summary": "Applying fix"},
            {"action": "tool", "tool": "run_tests", "args": {"suite": "server"}, "summary": "Verifying fix"},
            {"action": "tool", "tool": "git_commit", "args": {"message": "Fix app value"}, "summary": "Committing validated fix"},
            {"action": "final", "answer": "Fixed and committed.", "summary": "Task complete"},
        ]
    )
    prompts: list[str] = []

    async def fake_model(system: str, prompt: str, max_tokens: int) -> str:
        prompts.append(prompt)
        return json.dumps(next(responses))

    asyncio.run(FdexAgentLoop(runtime, model_call=fake_model, max_steps=6).run(task.id))
    completed = asyncio.run(runtime.get_task(task.id))
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.commit_sha
    assert any("exit_code=1" in prompt for prompt in prompts)
    assert any("exit_code=0" in prompt for prompt in prompts)
