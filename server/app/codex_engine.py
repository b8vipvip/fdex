from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, AgentTaskCancelled, FdexAgentRuntime
from app.codex_app_server import (
    CodexAppServerClient,
    CodexRpcError,
    CodexServerRequestDenied,
)
from app.config import SERVER_DIR, fresh_settings
from app.provider_manager import api_roots, provider_store, text_model_candidates

_PROVIDER_ENV_KEY = "FDEX_CODEX_PROVIDER_KEY"
_PROVIDER_ID = "fdex"
_WRAPPER = SERVER_DIR / "app" / "codex_env_wrapper.py"
_VERSION_RE = re.compile(r"(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z_.-]+)?)")

_DEVELOPER_INSTRUCTIONS = """You are running inside FDEX's account/project/task isolated worktree.
Work only on the user's requested coding task and inspect the repository before editing.
You may edit files and run relevant local tests using Codex's workspace sandbox.
Do not access, print, search for, or modify credentials, .env files, server/data, or .git internals.
Do not run git push, create remote branches, create pull requests, or otherwise contact GitHub directly.
Do not commit changes: FDEX validates the resulting worktree and owns commit/push/PR authority after the turn.
Never attempt to escape the current worktree or weaken the sandbox.
When finished, give a concise user-facing summary of changes and tests. Do not expose hidden reasoning.
"""


@dataclass(frozen=True, slots=True)
class CodexProviderSpec:
    provider_id: int
    name: str
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True, slots=True)
class CodexRuntimeSpec:
    path: str
    version: str
    source: str


def normalize_engine_mode(value: str) -> str:
    mode = (value or "legacy").strip().lower()
    return mode if mode in {"legacy", "codex", "auto"} else "legacy"


def select_codex_provider_from(providers: Iterable[dict[str, Any]]) -> CodexProviderSpec | None:
    """Pick the first configured Responses-capable text provider in FDEX priority order."""
    for provider in providers:
        protocols = [str(item).strip().lower() for item in (provider.get("protocol_order") or [])]
        if "responses" not in protocols:
            continue
        api_key = str(provider.get("api_key") or "").strip()
        models = text_model_candidates(provider)
        if not api_key or not models:
            continue
        roots = api_roots(str(provider.get("base_url") or ""))
        if not roots:
            continue
        try:
            provider_id = int(provider["id"])
        except (KeyError, TypeError, ValueError):
            continue
        return CodexProviderSpec(
            provider_id=provider_id,
            name=str(provider.get("name") or f"Provider {provider_id}"),
            base_url=roots[0],
            api_key=api_key,
            model=models[0],
        )
    return None


def select_codex_provider() -> CodexProviderSpec | None:
    return select_codex_provider_from(
        provider_store().list(enabled_only=True, include_secret=True)
    )


def _runtime_version(binary: Path) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    try:
        result = subprocess.run(
            (str(binary), "--version"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = f"{result.stdout}\n{result.stderr}".strip()
    match = _VERSION_RE.search(text)
    return match.group("version") if match else (text[:120] or "unknown")


def resolve_codex_runtime() -> CodexRuntimeSpec:
    """Resolve an official Codex executable without tying FDEX to one SDK version.

    Operator pinning wins, then a system-installed official `codex`, then the bundled
    openai-codex-cli-bin shipped by the Phase 7.19 dependency. The native app-server
    protocol is the compatibility boundary, so a newer official binary can be adopted
    without waiting for a matching Python SDK package.
    """
    configured = os.environ.get("FDEX_AGENT_CODEX_BIN", "").strip()
    if configured:
        binary = Path(configured).expanduser().resolve()
        source = "configured"
    else:
        system_codex = shutil.which("codex")
        if system_codex:
            binary = Path(system_codex).resolve()
            source = "system"
        else:
            try:
                from codex_cli_bin import bundled_codex_path
            except ImportError as exc:
                raise AgentRuntimeError(
                    "官方 Codex Runtime 未安装；请安装 server/requirements.txt，或设置 FDEX_AGENT_CODEX_BIN"
                ) from exc
            binary = Path(bundled_codex_path()).resolve()
            source = "bundled"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise AgentRuntimeError(f"官方 Codex Runtime 不可执行：{binary}")
    return CodexRuntimeSpec(path=str(binary), version=_runtime_version(binary), source=source)


def codex_runtime_status() -> dict[str, object]:
    provider: CodexProviderSpec | None = None
    runtime: CodexRuntimeSpec | None = None
    reason = ""
    try:
        runtime = resolve_codex_runtime()
        provider = select_codex_provider()
        if provider is None:
            reason = "没有已启用且支持 Responses 协议、API Key 和文本模型完整的供应商"
    except (AgentRuntimeError, RuntimeError, ValueError) as exc:
        reason = str(exc)
    return {
        "ready": bool(runtime is not None and provider is not None),
        # Kept for Phase 7.19 admin-template/API compatibility. Execution no longer depends
        # on the high-level Python SDK; FDEX speaks app-server JSON-RPC directly.
        "sdk_version": "native-jsonrpc",
        "runtime_version": runtime.version if runtime else "",
        "runtime_source": runtime.source if runtime else "",
        "runtime_path": runtime.path if runtime else "",
        "protocol": "codex-app-server-jsonrpc-v2",
        "provider_id": provider.provider_id if provider else None,
        "provider_name": provider.name if provider else "",
        "model": provider.model if provider else "",
        "reason": reason,
    }


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _provider_override(provider: CodexProviderSpec) -> str:
    return (
        "model_providers.fdex={ "
        f"name = {_toml_string('FDEX · ' + provider.name)}, "
        f"base_url = {_toml_string(provider.base_url)}, "
        f"env_key = {_toml_string(_PROVIDER_ENV_KEY)}, "
        "wire_api = \"responses\", request_max_retries = 2, stream_max_retries = 2 }"
    )


def _codex_home(task_owner: str) -> Path:
    """Return the owner-scoped Codex home used by the native runtime.

    Codex's own thread store, skills, hooks and plugin/MCP configuration are user-scoped
    concepts. Keeping one CODEX_HOME per FDEX owner allows those native capabilities to
    persist without crossing the FDEX user_id security boundary.
    """
    settings = fresh_settings()
    root = Path(settings.fdex_agent_codex_home_root).expanduser().resolve()
    safe_owner = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in task_owner)[:80] or "owner"
    path = (root / safe_owner).resolve()
    if root != path and root not in path.parents:
        raise AgentRuntimeError("Codex HOME escaped configured root")
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _safe_process_env(codex_home: Path, provider_key: str) -> dict[str, str]:
    env: dict[str, str] = {
        _PROVIDER_ENV_KEY: provider_key,
        "CODEX_HOME": str(codex_home),
        "HOME": str(codex_home),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "CI": "true",
    }
    for name in (
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "JAVA_HOME",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            env[name] = value
    return env


def _shell_environment_policy(codex_home: Path) -> dict[str, object]:
    values: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(codex_home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "CI": "true",
    }
    for name in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            values[name] = value
    return {"inherit": "none", "set": values}


def _launch_args(runtime_path: str, provider: CodexProviderSpec) -> tuple[str, ...]:
    if not _WRAPPER.is_file():
        raise AgentRuntimeError(f"FDEX Codex 环境隔离包装器缺失：{_WRAPPER}")
    return (
        sys.executable,
        str(_WRAPPER),
        runtime_path,
        "--config",
        _provider_override(provider),
        "app-server",
        "--listen",
        "stdio://",
    )


def _path_is_protected(relative: str) -> bool:
    clean = relative.strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    clean = clean.lstrip("/")
    lowered = clean.lower()
    if lowered == ".env" or (lowered.startswith(".env.") and lowered != ".env.example"):
        return True
    if lowered == "server/data" or lowered.startswith("server/data/"):
        return True
    return any(part == ".git" for part in Path(clean).parts)


def _name_lines(text: str) -> set[str]:
    return {line.strip() for line in (text or "").splitlines() if line.strip()}


def _working_changes(runtime: FdexAgentRuntime, worktree: Path) -> set[str]:
    paths = _name_lines(runtime._run_command(("git", "diff", "--name-only", "--"), cwd=worktree))
    paths.update(_name_lines(runtime._run_command(("git", "diff", "--cached", "--name-only", "--"), cwd=worktree)))
    paths.update(_name_lines(runtime._run_command(("git", "ls-files", "--others", "--exclude-standard"), cwd=worktree)))
    return paths


def _all_task_changes(runtime: FdexAgentRuntime, worktree: Path, initial_head: str) -> set[str]:
    paths = _working_changes(runtime, worktree)
    current = runtime._run_command(("git", "rev-parse", "HEAD"), cwd=worktree).strip()
    if current and current != initial_head:
        paths.update(_name_lines(runtime._run_command(("git", "diff", "--name-only", f"{initial_head}..{current}", "--"), cwd=worktree)))
    return paths


def _task_network_allowed(task: Any) -> bool:
    if task.project_id is None:
        return False
    project = agent_project_store().get_project(task.owner_id, task.project_id)
    return bool(project.get("allow_network"))


def _codex_thread_config(codex_home: Path, *, allow_network: bool) -> dict[str, object]:
    return {
        "shell_environment_policy": _shell_environment_policy(codex_home),
        "sandbox_workspace_write": {"network_access": bool(allow_network)},
        # Keep model-side web search disabled until FDEX exposes it as an explicit project
        # permission. Shell network remains controlled separately by allow_network.
        "web_search": "disabled",
    }


def _commit_and_publish(
    runtime: FdexAgentRuntime,
    task: Any,
    worktree: Path,
    initial_head: str,
    final_response: str,
) -> None:
    working = _working_changes(runtime, worktree)
    protected = sorted(path for path in _all_task_changes(runtime, worktree, initial_head) if _path_is_protected(path))
    if protected:
        raise AgentRuntimeError(
            "Codex 尝试修改 FDEX 保护路径，任务已阻止提交/推送：" + ", ".join(protected[:10])
        )

    if working:
        task.changed_files.update(working)
        subject = " ".join(str(task.prompt or "").split())[:86]
        runtime._git_commit(task, worktree, f"FDEX Codex: {subject or task.id[:12]}")
    else:
        current = runtime._run_command(("git", "rev-parse", "HEAD"), cwd=worktree).strip()
        if current and current != initial_head:
            task.commit_sha = current
            task.changed_files.update(_all_task_changes(runtime, worktree, initial_head))
            task.emit("git.committed", f"Codex worktree contains commit {current[:12]}")

    if task.project_id is None or not task.commit_sha:
        return
    project = agent_project_store().get_project(task.owner_id, task.project_id)
    if bool(project.get("allow_push")):
        agent_project_store().push_branch(task.owner_id, task.project_id, worktree, task.branch)
        task.pushed = True
        task.emit("git.pushed", f"Pushed {task.branch} through FDEX GitHub authority")
    if task.pushed and bool(project.get("allow_pr")):
        title = " ".join(str(task.prompt or "FDEX Codex changes").split())[:180] or "FDEX Codex changes"
        body = (final_response or "Codex completed the requested changes.").strip()[:12000]
        body += "\n\n---\nGenerated by FDEX using the official OpenAI Codex engine. GitHub credentials remained owned by FDEX."
        task.pr_url = agent_project_store().create_pr(
            task.owner_id,
            task.project_id,
            head=task.branch,
            title=title,
            body=body,
        )
        task.emit("github.pr_created", f"Created pull request {task.pr_url}")


def _safe_event_message(method: str, params: dict[str, Any]) -> tuple[str, str] | None:
    """Map useful native notifications to durable FDEX events without dumping raw secrets."""
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(item.get("type") or "item")
    if method == "item/started":
        return "codex.item_started", f"Codex started {item_type}"
    if method == "item/completed":
        suffix = ""
        if item_type == "commandExecution":
            status = str(item.get("status") or "")
            exit_code = item.get("exitCode")
            suffix = f" · {status}" if status else ""
            if exit_code is not None:
                suffix += f" · exit={exit_code}"
        if item_type == "fileChange":
            changes = item.get("changes")
            if isinstance(changes, list):
                suffix = f" · {len(changes)} file change(s)"
        return "codex.item_completed", f"Codex completed {item_type}{suffix}"
    if method == "turn/plan/updated":
        return "codex.plan_updated", "Codex updated its execution plan"
    if method == "turn/diff/updated":
        return "codex.diff_updated", "Codex updated the task diff"
    if method == "hook/started":
        return "codex.hook_started", "Codex hook started"
    if method == "hook/completed":
        return "codex.hook_completed", "Codex hook completed"
    if method == "item/mcpToolCall/progress":
        return "codex.mcp_progress", "Codex MCP tool call progressed"
    if method in {"warning", "configWarning", "guardianWarning", "deprecationNotice"}:
        message = str(params.get("message") or params.get("warning") or method)
        return "codex.warning", message[:700]
    if method == "error":
        message = str(params.get("message") or params.get("error") or "Codex reported an error")
        return "codex.error", message[:700]
    return None


def _turn_error_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        message = value.get("message")
        if message:
            return str(message)[:1200]
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:1200]
    return str(value)[:1200]


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    """Execute one FDEX task through the official Codex app-server protocol.

    Phase 7.20 intentionally bypasses the high-level Python SDK. FDEX speaks the public
    app-server JSON-RPC protocol so runtime features can evolve independently from the SDK.
    """
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    provider = select_codex_provider()
    if provider is None:
        raise AgentRuntimeError("没有可供 Codex 使用的 Responses 供应商")
    runtime_spec = resolve_codex_runtime()

    task.status = "running"
    task.emit(
        "engine.selected",
        f"Official OpenAI Codex native app-server {runtime_spec.version} · {provider.name} / {provider.model}",
    )
    try:
        await runtime._raise_if_cancelled(task)
        worktree = await asyncio.to_thread(runtime._ensure_worktree, task)
        initial_head = await asyncio.to_thread(
            runtime._run_command, ("git", "rev-parse", "HEAD"), cwd=worktree
        )
        codex_home = await asyncio.to_thread(_codex_home, task.owner_id)
        allow_network = await asyncio.to_thread(_task_network_allowed, task)

        async def on_notification(method: str, params: dict[str, Any]) -> None:
            event = _safe_event_message(method, params)
            if event is not None:
                task.emit(*event)

        async def on_server_request(method: str, _params: dict[str, Any]) -> Any:
            # Phase 7.20 opts into the complete protocol transport but keeps interactive
            # authority fail-closed. Approval/user-input/MCP elicitation bridges are added
            # only when FDEX has an owner-scoped UI decision channel for them.
            task.emit("codex.server_request_denied", f"Denied unsupported interactive request: {method}")
            raise CodexServerRequestDenied(f"FDEX policy denies interactive request {method}")

        client = CodexAppServerClient(
            _launch_args(runtime_spec.path, provider),
            env=_safe_process_env(codex_home, provider.api_key),
            cwd=worktree,
            client_version=fresh_settings().app_version,
            request_timeout=30.0,
            notification_handler=on_notification,
            server_request_handler=on_server_request,
            experimental_api=True,
        )
        task.emit(
            "codex.started",
            "Starting native official Codex app-server in the isolated task worktree "
            f"(workspace network={'enabled' if allow_network else 'disabled'})",
        )

        final_parts: list[str] = []
        final_item_text = ""
        turn_status = ""
        turn_error = ""
        thread_id = ""
        turn_id = ""

        async with client:
            thread_result = await client.request(
                "thread/start",
                {
                    "model": provider.model,
                    "modelProvider": _PROVIDER_ID,
                    "cwd": str(worktree),
                    "approvalPolicy": "never",
                    "sandbox": "workspace-write",
                    "config": _codex_thread_config(codex_home, allow_network=allow_network),
                    "developerInstructions": _DEVELOPER_INSTRUCTIONS,
                    # Durable within the FDEX owner-scoped CODEX_HOME. This is the basis for
                    # resume/fork/steer and native thread history in the next UI phase.
                    "ephemeral": False,
                },
            )
            thread_obj = thread_result.get("thread") if isinstance(thread_result, dict) else None
            if not isinstance(thread_obj, dict) or not str(thread_obj.get("id") or ""):
                raise AgentRuntimeError("Codex thread/start returned no thread id")
            thread_id = str(thread_obj["id"])
            task.emit("codex.thread_started", f"Codex thread {thread_id}")

            turn_result = await client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "text",
                            "text": task.prompt,
                            "text_elements": [],
                        }
                    ],
                    "approvalPolicy": "never",
                },
            )
            turn_obj = turn_result.get("turn") if isinstance(turn_result, dict) else None
            if not isinstance(turn_obj, dict) or not str(turn_obj.get("id") or ""):
                raise AgentRuntimeError("Codex turn/start returned no turn id")
            turn_id = str(turn_obj["id"])
            task.emit("codex.turn_started", f"Codex turn {turn_id}")

            try:
                while True:
                    await runtime._raise_if_cancelled(task)
                    try:
                        method, params = await client.next_notification(timeout=1.0)
                    except CodexRpcError as exc:
                        if exc.code == -32002:
                            continue
                        raise
                    if method == "item/agentMessage/delta" and str(params.get("turnId") or "") in {"", turn_id}:
                        delta = str(params.get("delta") or "")
                        if delta:
                            final_parts.append(delta)
                    elif method == "item/completed" and str(params.get("turnId") or "") in {"", turn_id}:
                        item = params.get("item")
                        if isinstance(item, dict) and str(item.get("type") or "") == "agentMessage":
                            text = str(item.get("text") or "").strip()
                            if text:
                                final_item_text = text
                    elif method == "turn/completed":
                        completed = params.get("turn")
                        if not isinstance(completed, dict):
                            continue
                        if str(completed.get("id") or "") != turn_id:
                            continue
                        turn_status = str(completed.get("status") or "")
                        turn_error = _turn_error_text(completed.get("error"))
                        break
            except AgentTaskCancelled:
                if thread_id and turn_id:
                    try:
                        await client.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            timeout=10.0,
                        )
                    except Exception:
                        pass
                raise

        if turn_status != "completed":
            raise AgentRuntimeError(
                f"Codex turn {turn_status or 'ended without completion'}: "
                f"{turn_error or 'no additional error detail'}"
            )
        final_response = final_item_text or "".join(final_parts).strip() or "Codex 已完成任务。"
        await asyncio.to_thread(
            _commit_and_publish,
            runtime,
            task,
            worktree,
            initial_head.strip(),
            final_response,
        )
        if task.pr_url:
            final_response += f"\n\nPull Request: {task.pr_url}"
        elif task.pushed:
            final_response += f"\n\n已推送分支：{task.branch}"
        await runtime.complete_task(task_id, final_response)
    except AgentTaskCancelled:
        return
    except Exception as exc:
        await runtime.fail_task(task_id, str(exc))
