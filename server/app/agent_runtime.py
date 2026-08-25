from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from app.agent_projects import agent_project_store
from app.agent_sandbox import AgentSandboxError, SystemdExecutionSandbox
from app.agent_tasks import agent_task_store
from app.config import get_settings

AgentTaskStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


@dataclass(slots=True)
class AgentEvent:
    type: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class AgentTask:
    id: str
    prompt: str
    owner_id: str = "local"
    project_id: int | None = None
    project_name: str = "Local FDEX"
    repository: str = ""
    base_branch: str = "main"
    status: AgentTaskStatus = "queued"
    result: str = ""
    error: str = ""
    branch: str = ""
    worktree: str = ""
    commit_sha: str = ""
    pushed: bool = False
    pr_url: str = ""
    changed_files: set[str] = field(default_factory=set)
    cancel_requested: bool = False
    parent_task_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    events: list[AgentEvent] = field(default_factory=list)
    _persist: Callable[[Any], None] | None = field(default=None, repr=False, compare=False)

    def emit(self, type_: str, message: str) -> None:
        self.events.append(AgentEvent(type=type_, message=message))
        if len(self.events) > 300:
            self.events = self.events[-300:]
        self.updated_at = datetime.now(UTC)
        if self._persist is not None:
            self._persist(self)


class AgentRuntimeError(RuntimeError):
    pass


class AgentTaskCancelled(AgentRuntimeError):
    pass


class FdexAgentRuntime:
    """Account/project/task scoped Coding Agent runtime.

    Phase 7.4 persists every task/event to SQLite so history survives process restarts and
    multiple Uvicorn workers. The in-memory map is only a hot cache. Task execution remains
    project/worktree isolated and the durable task store supplies cross-worker run locking.
    """

    _BASE_TOOLS = (
        "git_status", "git_diff", "git_log", "list_files", "read_file",
        "write_file", "replace_text", "run_tests", "git_commit",
    )
    _REMOTE_TOOLS = ("git_push", "github_create_pr")
    _TEST_SUITES: dict[str, tuple[tuple[str, ...], str]] = {
        "server": (("python", "-m", "pytest", "-q"), "server"),
        "android_unit": (("gradle", "--no-daemon", ":app:testDebugUnitTest"), "."),
        "android_debug": (("gradle", "--no-daemon", ":app:assembleDebug"), "."),
    }

    def __init__(self, workspace: Path | None = None, worktree_root: Path | None = None) -> None:
        settings = get_settings()
        self.enabled = bool(settings.fdex_agent_enabled)
        self.default_owner = settings.fdex_agent_default_owner.strip() or "local"
        self.workspace = (workspace or Path(settings.fdex_agent_workspace).expanduser()).resolve()
        self.worktree_root = (worktree_root or Path(settings.fdex_agent_worktree_root).expanduser()).resolve()
        self.sandbox_root = Path(settings.fdex_agent_sandbox_root).expanduser().resolve()
        self.timeout_seconds = settings.fdex_agent_command_timeout_seconds
        self.build_timeout_seconds = settings.fdex_agent_build_timeout_seconds
        self.max_output_chars = settings.fdex_agent_max_output_chars
        self.max_file_chars = settings.fdex_agent_max_file_chars
        self.execution_sandbox = SystemdExecutionSandbox()
        self.task_store = agent_task_store()
        self._tasks: dict[str, AgentTask] = {}
        self._lock = asyncio.Lock()

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return self._BASE_TOOLS

    def allowed_tools_for_task(self, task: AgentTask) -> tuple[str, ...]:
        tools = list(self._BASE_TOOLS)
        if task.project_id is not None:
            try:
                project = agent_project_store().get_project(task.owner_id, task.project_id)
                if project.get("allow_push"):
                    tools.append("git_push")
                if project.get("allow_pr"):
                    tools.append("github_create_pr")
            except (KeyError, ValueError):
                pass
        return tuple(tools)

    def capabilities(self) -> dict[str, object]:
        settings = get_settings()
        return {
            "enabled": self.enabled,
            "runtime": "fdex-agent-runtime-v7",
            "ai_source": "shared_provider_pool",
            "default_owner": self.default_owner,
            "sandbox_root": str(self.sandbox_root),
            "legacy_workspace": str(self.workspace),
            "tools": list(self._BASE_TOOLS + self._REMOTE_TOOLS),
            "test_suites": sorted(self._TEST_SUITES),
            "autonomous_loop": True,
            "account_project_task_isolation": True,
            "isolated_worktrees": True,
            "ephemeral_execution_sandbox": "systemd",
            "persistent_task_history": True,
            "cross_worker_task_lock": True,
            "task_cancel_retry": True,
            "sandbox_memory_mb": settings.fdex_agent_sandbox_memory_mb,
            "sandbox_cpu_percent": settings.fdex_agent_sandbox_cpu_percent,
            "sandbox_max_concurrent": settings.fdex_agent_sandbox_max_concurrent,
            "github_connector": True,
            "arbitrary_shell": False,
            "direct_main_write": False,
        }

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        text = str(value or "").strip()
        if not text:
            return datetime.now(UTC)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)

    def _persist_task_sync(self, task: AgentTask) -> None:
        self.task_store.save(task)

    def _task_from_record(self, row: dict[str, Any]) -> AgentTask:
        events = [
            AgentEvent(
                type=str(item.get("type") or ""),
                message=str(item.get("message") or ""),
                created_at=self._parse_time(item.get("created_at")),
            )
            for item in list(row.get("events") or [])
            if isinstance(item, dict)
        ]
        task = AgentTask(
            id=str(row["id"]),
            prompt=str(row.get("prompt") or ""),
            owner_id=str(row.get("owner_id") or self.default_owner),
            project_id=int(row["project_id"]) if row.get("project_id") is not None else None,
            project_name=str(row.get("project_name") or "Local FDEX"),
            repository=str(row.get("repository") or ""),
            base_branch=str(row.get("base_branch") or "main"),
            status=str(row.get("status") or "queued"),  # type: ignore[arg-type]
            result=str(row.get("result") or ""),
            error=str(row.get("error") or ""),
            branch=str(row.get("branch") or ""),
            worktree=str(row.get("worktree") or ""),
            commit_sha=str(row.get("commit_sha") or ""),
            pushed=bool(row.get("pushed")),
            pr_url=str(row.get("pr_url") or ""),
            changed_files=set(str(item) for item in list(row.get("changed_files") or [])),
            cancel_requested=bool(row.get("cancel_requested")),
            parent_task_id=str(row.get("parent_task_id") or ""),
            created_at=self._parse_time(row.get("created_at")),
            updated_at=self._parse_time(row.get("updated_at")),
            events=events,
            _persist=self._persist_task_sync,
        )
        return task

    async def create_task(
        self,
        prompt: str,
        *,
        owner_id: str | None = None,
        project_id: int | None = None,
        parent_task_id: str = "",
    ) -> AgentTask:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise AgentRuntimeError("prompt is required")
        if not self.enabled:
            raise AgentRuntimeError("FDEX Agent Runtime is disabled")
        owner = (owner_id or self.default_owner).strip()
        if project_id is not None:
            usage = self.execution_sandbox.account_usage(owner)
            if bool(usage.get("over_limit")):
                raise AgentRuntimeError("account sandbox disk limit reached; clean completed workspaces before creating a new task")
        task = AgentTask(
            id=uuid.uuid4().hex,
            prompt=clean_prompt,
            owner_id=owner,
            parent_task_id=parent_task_id,
            _persist=self._persist_task_sync,
        )
        if project_id is not None:
            try:
                project = agent_project_store().get_project(owner, int(project_id))
            except (KeyError, ValueError) as exc:
                raise AgentRuntimeError(str(exc)) from exc
            if not project["enabled"]:
                raise AgentRuntimeError("Agent project is disabled")
            task.project_id = int(project_id)
            task.project_name = str(project["name"])
            task.repository = str(project["repo_full_name"])
            task.base_branch = str(project["base_branch"])
        task.emit("task.created", f"Agent task created for {task.project_name}")
        async with self._lock:
            self._tasks[task.id] = task
        return task

    async def get_task(self, task_id: str) -> AgentTask | None:
        async with self._lock:
            cached = self._tasks.get(task_id)
        if cached is not None:
            return cached
        try:
            row = await asyncio.to_thread(self.task_store.get_any, task_id)
        except ValueError:
            return None
        if row is None:
            return None
        task = self._task_from_record(row)
        async with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None:
                return existing
            self._tasks[task_id] = task
        return task

    async def list_tasks(self, owner_id: str, *, status: str = "", limit: int = 50) -> list[AgentTask]:
        rows = await asyncio.to_thread(self.task_store.list, owner_id, status=status, limit=limit)
        tasks: list[AgentTask] = []
        for row in rows:
            task_id = str(row["id"])
            async with self._lock:
                cached = self._tasks.get(task_id)
            task = cached or self._task_from_record(row)
            if cached is None:
                async with self._lock:
                    self._tasks.setdefault(task_id, task)
            tasks.append(task)
        return tasks

    async def request_cancel(self, owner_id: str, task_id: str, *, force_terminal: bool = False) -> AgentTask:
        task = await self.get_task(task_id)
        if task is None or task.owner_id != owner_id:
            raise AgentRuntimeError("task not found")
        if task.status in {"succeeded", "failed", "canceled"}:
            return task
        task.cancel_requested = True
        was_queued = task.status == "queued"
        if was_queued or force_terminal:
            task.status = "canceled"
            task.error = ""
            message = "Task canceled before execution"
            if force_terminal and not was_queued:
                message = "Task canceled after confirming no worker owns its execution lock"
            task.emit("task.canceled", message)
        else:
            task.emit("task.cancel_requested", "Cancellation requested; stopping at the next safe tool boundary")
        return task

    async def retry_task(self, owner_id: str, task_id: str) -> AgentTask:
        source = await self.get_task(task_id)
        if source is None or source.owner_id != owner_id:
            raise AgentRuntimeError("task not found")
        if source.status not in {"succeeded", "failed", "canceled"}:
            raise AgentRuntimeError("only completed, failed or canceled tasks can be retried")
        return await self.create_task(
            source.prompt,
            owner_id=owner_id,
            project_id=source.project_id,
            parent_task_id=source.id,
        )

    async def _raise_if_cancelled(self, task: AgentTask) -> None:
        requested = task.cancel_requested
        if not requested:
            requested = await asyncio.to_thread(self.task_store.cancel_requested, task.id)
        if requested:
            task.cancel_requested = True
            if task.status != "canceled":
                task.status = "canceled"
                task.error = ""
                task.emit("task.canceled", "Task stopped after a cancellation request")
            raise AgentTaskCancelled("task canceled")

    async def execute_tool(self, task_id: str, tool: str, *, args: dict[str, Any] | None = None, terminal: bool = False) -> str:
        task = await self.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        await self._raise_if_cancelled(task)
        allowed = self.allowed_tools_for_task(task)
        if tool not in allowed:
            raise AgentRuntimeError(f"tool not allowed for project: {tool}")
        args = args or {}
        task.status = "running"
        task.emit("tool.started", f"Running {tool}")
        try:
            worktree = await asyncio.to_thread(self._ensure_worktree, task)
            output = await asyncio.to_thread(self._execute_tool_sync, task, worktree, tool, args)
            await self._raise_if_cancelled(task)
        except AgentTaskCancelled:
            raise
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
        await self._raise_if_cancelled(task)
        task.status = "succeeded"
        task.result = result.strip()
        task.error = ""
        task.emit("task.completed", "Agent task completed")
        return task

    async def fail_task(self, task_id: str, error: str) -> AgentTask:
        task = await self.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if task.cancel_requested or await asyncio.to_thread(self.task_store.cancel_requested, task.id):
            task.cancel_requested = True
            task.status = "canceled"
            task.error = ""
            task.emit("task.canceled", "Task stopped after a cancellation request")
            return task
        task.status = "failed"
        task.error = error.strip()[:2000]
        task.emit("task.failed", task.error)
        return task

    async def cleanup_completed_workspaces(self, owner_id: str, *, limit: int = 100) -> dict[str, int]:
        rows = await asyncio.to_thread(self.task_store.list_releasable, owner_id, limit=limit)
        tasks = [self._task_from_record(row) for row in rows]
        released = 0
        failed = 0
        for task in tasks:
            if task.status not in {"succeeded", "failed", "canceled"} or not task.worktree:
                continue
            try:
                await asyncio.to_thread(self._release_worktree, task)
                released += 1
            except Exception:
                failed += 1
        cache_bytes = await asyncio.to_thread(self.execution_sandbox.clear_account_cache, owner_id)
        return {"released_worktrees": released, "failed_worktrees": failed, "cache_bytes_removed": cache_bytes}

    def _release_worktree(self, task: AgentTask) -> None:
        path = Path(task.worktree).expanduser().resolve()
        if task.project_id is None:
            allowed_root = self.worktree_root.resolve()
            source = self.workspace.resolve()
        else:
            repo, worktrees = agent_project_store().project_paths(task.owner_id, task.project_id)
            allowed_root = worktrees.resolve()
            source = repo.resolve()
        if path != allowed_root and allowed_root not in path.parents:
            raise AgentRuntimeError("task worktree escaped configured worktree root")
        if source.is_dir() and (source / ".git").exists():
            subprocess.run(
                ("git", "worktree", "remove", "--force", str(path)),
                cwd=str(source),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            subprocess.run(
                ("git", "worktree", "prune"),
                cwd=str(source),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        if path.exists():
            shutil.rmtree(path)
        task.worktree = ""
        task.emit("workspace.released", "Released completed task worktree")

    def _source_and_worktrees(self, task: AgentTask) -> tuple[Path, Path, str]:
        if task.project_id is None:
            if not self.workspace.is_dir():
                raise AgentRuntimeError("agent workspace does not exist")
            return self.workspace, self.worktree_root, "HEAD"
        try:
            project, repo, worktrees = agent_project_store().prepare_repository(task.owner_id, task.project_id)
        except Exception as exc:
            raise AgentRuntimeError(str(exc)) from exc
        task.project_name = str(project["name"])
        task.repository = str(project["repo_full_name"])
        task.base_branch = str(project["base_branch"])
        return repo, worktrees, f"origin/{task.base_branch}"

    def _ensure_worktree(self, task: AgentTask) -> Path:
        if task.worktree:
            existing = Path(task.worktree)
            if existing.is_dir():
                return existing.resolve()
            raise AgentRuntimeError("agent task worktree disappeared")
        source, worktree_root, base_ref = self._source_and_worktrees(task)
        self._run_command(("git", "rev-parse", "--is-inside-work-tree"), cwd=source)
        worktree_root.mkdir(parents=True, exist_ok=True)
        branch = f"fdex-agent/{task.owner_id[:20]}-{task.id[:12]}"
        path = (worktree_root / task.id).resolve()
        if path.exists():
            raise AgentRuntimeError("agent worktree path already exists")
        self._run_command(("git", "worktree", "add", "-b", branch, str(path), base_ref), cwd=source)
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
            return self._run_tests(task, worktree, str(args.get("suite") or ""))
        if tool == "git_commit":
            return self._git_commit(task, worktree, str(args.get("message") or ""))
        if tool == "git_push":
            if task.project_id is None or not task.commit_sha:
                raise AgentRuntimeError("configured project and commit are required before push")
            output = agent_project_store().push_branch(task.owner_id, task.project_id, worktree, task.branch)
            task.pushed = True
            task.emit("git.pushed", f"Pushed {task.branch} to origin")
            return output or f"pushed {task.branch}"
        if tool == "github_create_pr":
            if task.project_id is None or not task.pushed:
                raise AgentRuntimeError("push the Agent branch before creating a pull request")
            title = " ".join(str(args.get("title") or "FDEX Agent changes").split())[:240]
            body = str(args.get("body") or "")
            url = agent_project_store().create_pr(task.owner_id, task.project_id, head=task.branch, title=title, body=body)
            task.pr_url = url
            task.emit("github.pr_created", f"Created pull request {url}")
            return f"pull_request={url}"
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
            for name in sorted(files):
                path = Path(current) / name
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
        selected = "".join(lines[start - 1:end])
        if len(selected) > self.max_file_chars:
            selected = selected[:self.max_file_chars] + "\n... file truncated ..."
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

    def _run_tests(self, task: AgentTask, worktree: Path, suite: str) -> str:
        if suite not in self._TEST_SUITES:
            raise AgentRuntimeError(f"unknown test suite: {suite or '<empty>'}")
        argv, relative_cwd = self._TEST_SUITES[suite]
        cwd = worktree if relative_cwd == "." else worktree / relative_cwd
        if not cwd.is_dir():
            raise AgentRuntimeError(f"test working directory not found: {relative_cwd}")
        if task.project_id is None:
            return self._run_command(
                argv,
                cwd=cwd,
                timeout=self.build_timeout_seconds,
                check=False,
                include_exit_code=True,
            )
        try:
            project = agent_project_store().get_project(task.owner_id, task.project_id)
            task.emit(
                "sandbox.started",
                f"Running {suite} in transient sandbox (MemoryMax={project['sandbox_memory_mb']}MB)",
            )
            return self.execution_sandbox.run(
                owner_id=task.owner_id,
                task_id=task.id,
                worktree=worktree,
                cwd=cwd,
                argv=argv,
                timeout=self.build_timeout_seconds,
                max_output_chars=self.max_output_chars,
                memory_mb=int(project["sandbox_memory_mb"]),
                cpu_percent=int(project["sandbox_cpu_percent"]),
                allow_network=bool(project["allow_network"]),
            )
        except (KeyError, ValueError, AgentSandboxError) as exc:
            raise AgentRuntimeError(str(exc)) from exc

    def _git_commit(self, task: AgentTask, worktree: Path, message: str) -> str:
        if not task.changed_files:
            raise AgentRuntimeError("no agent-written files to commit")
        clean_message = " ".join(message.strip().split())[:120] or f"FDEX agent task {task.id[:12]}"
        paths = sorted(task.changed_files)
        self._run_command(("git", "add", "--", *paths), cwd=worktree)
        staged = self._run_command(("git", "diff", "--cached", "--name-only"), cwd=worktree)
        if not staged.strip():
            raise AgentRuntimeError("no staged changes to commit")
        self._run_command(
            ("git", "-c", "user.name=FDEX Agent", "-c", "user.email=agent@fdex.local", "commit", "-m", clean_message),
            cwd=worktree,
        )
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
            output = output[:self.max_output_chars] + "\n... output truncated ..."
        if check and completed.returncode != 0:
            command = " ".join(shlex.quote(part) for part in argv)
            raise AgentRuntimeError(f"command failed ({completed.returncode}): {command}\n{output}".strip())
        if include_exit_code:
            return f"exit_code={completed.returncode}\n{output}".strip()
        return output.strip()


_runtime: FdexAgentRuntime | None = None


def agent_runtime() -> FdexAgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = FdexAgentRuntime()
    return _runtime
