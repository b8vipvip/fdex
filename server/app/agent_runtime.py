from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.config import get_settings

AgentTaskStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass(slots=True)
class AgentEvent:
    type: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AgentTask:
    id: str
    prompt: str
    status: AgentTaskStatus = "queued"
    result: str = ""
    error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, type_: str, message: str) -> None:
        self.events.append(AgentEvent(type=type_, message=message))
        self.updated_at = datetime.now(timezone.utc)


class AgentRuntimeError(RuntimeError):
    pass


class FdexAgentRuntime:
    """Server-side execution boundary for Codex-style FDEX agents.

    The model never receives arbitrary shell access. Every requested action is
    validated against this runtime allowlist before a subprocess is started.
    Phase 2 keeps the allowlist read-only while adding an autonomous model loop.
    """

    _ALLOWED_COMMANDS: dict[str, tuple[str, ...]] = {
        "git_status": ("git", "status", "--short", "--branch"),
        "git_diff": ("git", "diff", "--stat"),
        "git_log": ("git", "log", "-5", "--oneline"),
    }

    def __init__(self, workspace: Path | None = None) -> None:
        settings = get_settings()
        self.enabled = bool(settings.fdex_agent_enabled)
        configured_workspace = Path(settings.fdex_agent_workspace).expanduser()
        self.workspace = (workspace or configured_workspace).resolve()
        self.timeout_seconds = settings.fdex_agent_command_timeout_seconds
        self.max_output_chars = settings.fdex_agent_max_output_chars
        self._tasks: dict[str, AgentTask] = {}
        self._lock = asyncio.Lock()

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(sorted(self._ALLOWED_COMMANDS))

    def capabilities(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "runtime": "fdex-agent-runtime-v2",
            "workspace": str(self.workspace),
            "tools": list(self.allowed_tools),
            "autonomous_loop": True,
            "arbitrary_shell": False,
            "github_write": False,
        }

    async def create_task(self, prompt: str) -> AgentTask:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise AgentRuntimeError("prompt is required")
        if not self.enabled:
            raise AgentRuntimeError("FDEX Agent Runtime is disabled")

        task = AgentTask(id=uuid.uuid4().hex, prompt=clean_prompt)
        task.emit("task.created", "Agent task created")
        async with self._lock:
            self._tasks[task.id] = task
        return task

    async def get_task(self, task_id: str) -> AgentTask | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def execute_tool(self, task_id: str, tool: str, *, terminal: bool = False) -> str:
        task = await self.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if tool not in self._ALLOWED_COMMANDS:
            raise AgentRuntimeError(f"tool not allowed: {tool}")
        if not self.workspace.is_dir():
            raise AgentRuntimeError("agent workspace does not exist")

        task.status = "running"
        task.emit("tool.started", f"Running {tool}")
        try:
            output = await asyncio.to_thread(self._run_command, self._ALLOWED_COMMANDS[tool])
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.emit("tool.failed", task.error)
            raise AgentRuntimeError(task.error) from exc

        if terminal:
            task.status = "succeeded"
            task.result = output
        task.emit("tool.completed", f"Completed {tool}")
        return output

    async def run_inspection(self, task_id: str, tool: str) -> AgentTask:
        await self.execute_tool(task_id, tool, terminal=True)
        task = await self.get_task(task_id)
        if task is None:  # Defensive: execute_tool already validates this.
            raise AgentRuntimeError("task not found")
        return task

    async def complete_task(self, task_id: str, result: str) -> AgentTask:
        task = await self.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        task.status = "succeeded"
        task.result = result.strip()
        task.error = ""
        task.emit("task.completed", "Agent task completed")
        return task

    async def fail_task(self, task_id: str, error: str) -> AgentTask:
        task = await self.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        task.status = "failed"
        task.error = error.strip()[:2000]
        task.emit("task.failed", task.error)
        return task

    def _run_command(self, argv: tuple[str, ...]) -> str:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "GIT_TERMINAL_PROMPT": "0",
        }
        completed = subprocess.run(
            argv,
            cwd=self.workspace,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=self.timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + "\n... output truncated ..."
        if completed.returncode != 0:
            command = " ".join(shlex.quote(part) for part in argv)
            raise AgentRuntimeError(
                f"command failed ({completed.returncode}): {command}\n{output}".strip()
            )
        return output.strip()


_runtime: FdexAgentRuntime | None = None


def agent_runtime() -> FdexAgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = FdexAgentRuntime()
    return _runtime
