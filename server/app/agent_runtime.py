from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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
    branch: str = ""
    worktree: str = ""
    commit_sha: str = ""
    changed_files: set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, type_: str, message: str) -> None:
        self.events.append(AgentEvent(type=type_, message=message))
        self.updated_at = datetime.now(timezone.utc)


class AgentRuntimeError(RuntimeError):
    pass


class FdexAgentRuntime:
    """Bounded server-side execution runtime for Codex-style FDEX agents.

    Phase 3 gives an agent write/build capability only inside a per-task Git worktree.
    There is still no arbitrary shell, network tool, push, merge, or direct main write.
    """

    _TOOLS = (
        "git_status",
        "git_diff",
        "git_log",
        "list_files",
        "read_file",
        "write_file",
        "replace_text",
        "run_tests",
        "git_commit",
    )
    _TEST_SUITES: dict[str, tuple[tuple[str, ...], str]] = {
        "server": (("python", "-m", "pytest", "-q"), "server"),
        "android_unit": (("gradle", "--no-daemon", ":app:testDebugUnitTest"), "."),
        "android_debug": (("gradle", "--no-daemon", ":app:assembleDebug"), "."),
    }

    def __init__(self, workspace: Path | None = None, worktree_root: Path | None = None) -> None:
        settings = get_settings()
        self.enabled = bool(settings.fdex_agent_enabled)
        configured_workspace = Path(settings.fdex_agent_workspace).expanduser()
        configured_worktrees = Path(settings.fdex_agent_worktree_root).expanduser()
        self.workspace = (workspace or configured_workspace).resolve()
        self.worktree_root = (worktree_root or configured_worktrees).resolve()
        self.timeout_seconds = settings.fdex_agent_command_timeout_seconds
        self.build_timeout_seconds = settings.fdex_agent_build_timeout_seconds
        self.max_output_chars = settings.fdex_agent_max_output_chars
        self.max_file_chars = settings.fdex_agent_max_file_chars
        self._tasks: dict[str, AgentTask] = {}
        self._lock = asyncio.Lock()

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return self._TOOLS

    def capabilities(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "runtime": "fdex-agent-runtime-v3",
            "workspace": str(self.workspace),
            "worktree_root": str(self.worktree_root),
            "tools": list(self.allowed_tools),
            "test_suites": sorted(self._TEST_SUITES),
            "autonomous_loop": True,
            "isolated_worktrees": True,
            "arbitrary_shell": False,
            "network_tool": False,
            "github_write": False,
            "git_push": False,
            "direct_main_write": False,
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

    async def execute_tool(
        self,
        task_id: str,
        tool: str,
        *,
        args: dict[str, Any] | None = None,
        terminal: bool = False,
    ) -> str:
        task = await self.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if tool not in self.allowed_tools:
            raise AgentRuntimeError(f"tool not allowed: {tool}")
        if not self.workspace.is_dir():
            raise AgentRuntimeError("agent workspace does not exist")

        args = args or {}
        task.status = "running"
        task.emit("tool.started", f"Running {tool}")
        try:
            worktree = await asyncio.to_thread(self._ensure_worktree, task)
            output = await asyncio.to_thread(self._execute_tool_sync, task, worktree, tool, args)
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.emit("tool.failed", task.error[:500])
            raise AgentRuntimeError(task.error) from exc

        if terminal:
            task.status = "succeeded"
            task.result = output
        task.emit("tool.completed", f"Completed {tool}")
        return output

    async def run_inspection(self, task_id: str, tool: str, args: dict[str, Any] | None = None) -> AgentTask:
        await self.execute_tool(task_id, tool, args=args, terminal=True)
        task = await self.get_task(task_id)
        if task is None:
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

    def _ensure_worktree(self, task: AgentTask) -> Path:
        if task.worktree:
            existing = Path(task.worktree)
            if existing.is_dir():
                return existing.resolve()
            raise AgentRuntimeError("agent task worktree disappeared")

        self._run_command(("git", "rev-parse", "--is-inside-work-tree"), cwd=self.workspace)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        branch = f"fdex-agent/{task.id[:12]}"
        path = (self.worktree_root / task.id).resolve()
        if path.exists():
            raise AgentRuntimeError("agent worktree path already exists")
        self._run_command(("git", "worktree", "add", "-b", branch, str(path), "HEAD"), cwd=self.workspace)
        task.branch = branch
        task.worktree = str(path)
        task.emit("workspace.ready", f"Prepared isolated worktree on {branch}")
        return path

    def _execute_tool_sync(self, task: AgentTask, worktree: Path, tool: str, args: dict[str, Any]) -> str:
        if tool == "git_status":
            return self._run_command(("git", "status", "--short", "--branch"), cwd=worktree)
        if tool == "git_diff":
            return self._run_command(("git", "diff", "--"), cwd=worktree)
        if tool == "git_log":
            return self._run_command(("git", "log", "-5", "--oneline"), cwd=worktree)
        if tool == "list_files":
            return self._list_files(worktree, str(args.get("path") or "."))
        if tool == "read_file":
            return self._read_file(worktree, str(args.get("path") or ""), args)
        if tool == "write_file":
            return self._write_file(task, worktree, str(args.get("path") or ""), args.get("content"))
        if tool == "replace_text":
            return self._replace_text(task, worktree, args)
        if tool == "run_tests":
            return self._run_tests(worktree, str(args.get("suite") or ""))
        if tool == "git_commit":
            return self._git_commit(task, worktree, str(args.get("message") or ""))
        raise AgentRuntimeError(f"tool not allowed: {tool}")

    def _safe_path(self, worktree: Path, relative: str, *, allow_directory: bool = False) -> Path:
        clean = relative.strip().replace("\\", "/")
        if not clean:
            raise AgentRuntimeError("path is required")
        rel = Path(clean)
        if rel.is_absolute() or ".." in rel.parts:
            raise AgentRuntimeError("path escapes agent worktree")
        lowered = clean.lower().strip("/")
        if lowered == ".env" or (lowered.startswith(".env.") and lowered != ".env.example"):
            raise AgentRuntimeError("path is protected")
        if lowered == "server/data" or lowered.startswith("server/data/"):
            raise AgentRuntimeError("path is protected")
        if any(part == ".git" for part in rel.parts):
            raise AgentRuntimeError("path is protected")

        candidate = (worktree / rel).resolve(strict=False)
        root = worktree.resolve()
        if candidate != root and root not in candidate.parents:
            raise AgentRuntimeError("path escapes agent worktree")
        if not allow_directory and candidate == root:
            raise AgentRuntimeError("file path is required")
        return candidate

    def _list_files(self, worktree: Path, relative: str) -> str:
        base = self._safe_path(worktree, relative, allow_directory=True)
        if not base.exists() or not base.is_dir():
            raise AgentRuntimeError("directory not found")
        root = worktree.resolve()
        rows: list[str] = []
        for current, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in {".git", ".gradle", "build", "data"})
            current_path = Path(current)
            for name in sorted(files):
                path = current_path / name
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                rows.append(rel)
                if len(rows) >= 250:
                    rows.append("... file list truncated ...")
                    return "\n".join(rows)
        return "\n".join(rows) or "(no files)"

    def _read_file(self, worktree: Path, relative: str, args: dict[str, Any]) -> str:
        path = self._safe_path(worktree, relative)
        if not path.is_file():
            raise AgentRuntimeError("file not found")
        raw = path.read_bytes()
        if len(raw) > self.max_file_chars * 4:
            raise AgentRuntimeError("file is too large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentRuntimeError("only UTF-8 text files can be read") from exc
        lines = text.splitlines(keepends=True)
        start = max(1, int(args.get("start_line") or 1))
        end = min(len(lines), int(args.get("end_line") or min(len(lines), start + 399)))
        if end < start:
            raise AgentRuntimeError("end_line must be >= start_line")
        selected = "".join(lines[start - 1 : end])
        if len(selected) > self.max_file_chars:
            selected = selected[: self.max_file_chars] + "\n... file truncated ..."
        return f"{relative} lines {start}-{end}/{len(lines)}\n{selected}"

    def _write_file(self, task: AgentTask, worktree: Path, relative: str, content: Any) -> str:
        if not isinstance(content, str):
            raise AgentRuntimeError("write_file content must be text")
        if len(content) > self.max_file_chars:
            raise AgentRuntimeError("write_file content exceeds limit")
        path = self._safe_path(worktree, relative)
        if path.exists() and not path.is_file():
            raise AgentRuntimeError("write target is not a file")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = path.relative_to(worktree.resolve()).as_posix()
        task.changed_files.add(rel)
        task.emit("file.written", f"Updated {rel}")
        return f"updated {rel} ({len(content)} chars)"

    def _replace_text(self, task: AgentTask, worktree: Path, args: dict[str, Any]) -> str:
        relative = str(args.get("path") or "")
        old = args.get("old")
        new = args.get("new")
        if not isinstance(old, str) or not old:
            raise AgentRuntimeError("replace_text old text is required")
        if not isinstance(new, str):
            raise AgentRuntimeError("replace_text new text must be text")
        path = self._safe_path(worktree, relative)
        if not path.is_file():
            raise AgentRuntimeError("file not found")
        text = path.read_text(encoding="utf-8")
        matches = text.count(old)
        if matches != 1:
            raise AgentRuntimeError(f"replace_text requires exactly one match; found {matches}")
        updated = text.replace(old, new, 1)
        if len(updated) > self.max_file_chars:
            raise AgentRuntimeError("updated file exceeds limit")
        path.write_text(updated, encoding="utf-8")
        rel = path.relative_to(worktree.resolve()).as_posix()
        task.changed_files.add(rel)
        task.emit("file.written", f"Updated {rel}")
        return f"replaced one occurrence in {rel}"

    def _run_tests(self, worktree: Path, suite: str) -> str:
        if suite not in self._TEST_SUITES:
            raise AgentRuntimeError(f"unknown test suite: {suite or '<empty>'}")
        argv, relative_cwd = self._TEST_SUITES[suite]
        cwd = worktree if relative_cwd == "." else (worktree / relative_cwd)
        if not cwd.is_dir():
            raise AgentRuntimeError(f"test working directory not found: {relative_cwd}")
        return self._run_command(
            argv,
            cwd=cwd,
            timeout=self.build_timeout_seconds,
            check=False,
            include_exit_code=True,
        )

    def _git_commit(self, task: AgentTask, worktree: Path, message: str) -> str:
        if not task.changed_files:
            raise AgentRuntimeError("no agent-written files to commit")
        clean_message = " ".join(message.strip().split())[:120] or f"FDEX agent task {task.id[:12]}"
        paths = sorted(task.changed_files)
        self._run_command(("git", "add", "--", *paths), cwd=worktree)
        staged = self._run_command(("git", "diff", "--cached", "--name-only"), cwd=worktree)
        if not staged.strip():
            raise AgentRuntimeError("no staged changes to commit")
        self._run_command(("git", "-c", "user.name=FDEX Agent", "-c", "user.email=agent@fdex.local", "commit", "-m", clean_message), cwd=worktree)
        sha = self._run_command(("git", "rev-parse", "HEAD"), cwd=worktree)
        task.commit_sha = sha.strip()
        task.emit("git.committed", f"Created commit {task.commit_sha[:12]} on {task.branch}")
        return f"commit={task.commit_sha}\nbranch={task.branch}\nfiles=\n{staged}"

    def _run_command(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout: float | None = None,
        check: bool = True,
        include_exit_code: bool = False,
    ) -> str:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "GIT_TERMINAL_PROMPT": "0",
            "CI": "true",
        }
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout or self.timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + "\n... output truncated ..."
        if check and completed.returncode != 0:
            command = " ".join(shlex.quote(part) for part in argv)
            raise AgentRuntimeError(
                f"command failed ({completed.returncode}): {command}\n{output}".strip()
            )
        if include_exit_code:
            return f"exit_code={completed.returncode}\n{output}".strip()
        return output.strip()


_runtime: FdexAgentRuntime | None = None


def agent_runtime() -> FdexAgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = FdexAgentRuntime()
    return _runtime
