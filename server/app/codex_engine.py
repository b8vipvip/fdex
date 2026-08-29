from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, AgentTaskCancelled, FdexAgentRuntime
from app.config import SERVER_DIR, fresh_settings
from app.provider_manager import api_roots, provider_store, text_model_candidates

_PROVIDER_ENV_KEY = "FDEX_CODEX_PROVIDER_KEY"
_PROVIDER_ID = "fdex"
_WRAPPER = SERVER_DIR / "app" / "codex_env_wrapper.py"

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


def _sdk_runtime() -> tuple[str, str]:
    try:
        import openai_codex
        from codex_cli_bin import bundled_codex_path
    except ImportError as exc:
        raise AgentRuntimeError(
            "官方 OpenAI Codex Python SDK/Runtime 未安装；请安装 server/requirements.txt 后重启 FDEX"
        ) from exc
    binary = Path(bundled_codex_path()).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise AgentRuntimeError(f"官方 Codex Runtime 不可执行：{binary}")
    return str(getattr(openai_codex, "__version__", "unknown")), str(binary)


def codex_runtime_status() -> dict[str, object]:
    provider: CodexProviderSpec | None = None
    sdk_version = ""
    runtime_path = ""
    reason = ""
    try:
        sdk_version, runtime_path = _sdk_runtime()
        provider = select_codex_provider()
        if provider is None:
            reason = "没有已启用且支持 Responses 协议、API Key 和文本模型完整的供应商"
    except (AgentRuntimeError, RuntimeError, ValueError) as exc:
        reason = str(exc)
    return {
        "ready": bool(sdk_version and runtime_path and provider is not None),
        "sdk_version": sdk_version,
        "runtime_path": runtime_path,
        "provider_id": provider.provider_id if provider else None,
        "provider_name": provider.name if provider else "",
        "model": provider.model if provider else "",
        "reason": reason,
    }


def _toml_string(value: str) -> str:
    # TOML basic strings and JSON strings share the escaping needed by these values.
    return json.dumps(value, ensure_ascii=False)


def _provider_override(provider: CodexProviderSpec) -> str:
    return (
        "model_providers.fdex={ "
        f"name = {_toml_string('FDEX · ' + provider.name)}, "
        f"base_url = {_toml_string(provider.base_url)}, "
        f"env_key = {_toml_string(_PROVIDER_ENV_KEY)}, "
        "wire_api = \"responses\", request_max_retries = 2, stream_max_retries = 2 }"
    )


def _codex_home(task_owner: str, task_id: str) -> Path:
    settings = fresh_settings()
    root = Path(settings.fdex_agent_codex_home_root).expanduser().resolve()
    safe_owner = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in task_owner)[:80] or "owner"
    path = (root / safe_owner / task_id).resolve()
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
    # Tool commands get a clean environment without the model provider API key or any
    # unrelated FDEX service secrets. Only build/runtime variables are explicitly restored.
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
    clean = relative.strip().replace("\\", "/").lstrip("./")
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
        # The legacy bootstrap workspace never grants Codex arbitrary network access.
        return False
    project = agent_project_store().get_project(task.owner_id, task.project_id)
    return bool(project.get("allow_network"))


def _codex_thread_config(codex_home: Path, *, allow_network: bool) -> dict[str, object]:
    # Keep FDEX project network semantics authoritative. Web Search is disabled in the
    # foundation release because it is model-side network access rather than workspace
    # command access and must not silently bypass allow_network.
    return {
        "shell_environment_policy": _shell_environment_policy(codex_home),
        "sandbox_workspace_write": {"network_access": bool(allow_network)},
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


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    provider = select_codex_provider()
    if provider is None:
        raise AgentRuntimeError("没有可供 Codex 使用的 Responses 供应商")
    sdk_version, runtime_path = _sdk_runtime()

    try:
        from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
    except ImportError as exc:
        raise AgentRuntimeError("官方 OpenAI Codex SDK 无法导入") from exc

    task.status = "running"
    task.emit("engine.selected", f"Official OpenAI Codex SDK {sdk_version} · {provider.name} / {provider.model}")
    try:
        await runtime._raise_if_cancelled(task)
        worktree = await asyncio.to_thread(runtime._ensure_worktree, task)
        initial_head = await asyncio.to_thread(
            runtime._run_command, ("git", "rev-parse", "HEAD"), cwd=worktree
        )
        codex_home = await asyncio.to_thread(_codex_home, task.owner_id, task.id)
        allow_network = await asyncio.to_thread(_task_network_allowed, task)
        config = CodexConfig(
            launch_args_override=_launch_args(runtime_path, provider),
            cwd=str(worktree),
            env=_safe_process_env(codex_home, provider.api_key),
            client_name="fdex",
            client_title="FDEX Coding Agent",
            client_version=fresh_settings().app_version,
        )
        task.emit(
            "codex.started",
            "Starting official Codex app-server in the isolated task worktree "
            f"(workspace network={'enabled' if allow_network else 'disabled'})",
        )
        final_parts: list[str] = []
        turn_status = ""
        turn_error = ""
        async with AsyncCodex(config=config) as codex:
            thread = await codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(worktree),
                developer_instructions=_DEVELOPER_INSTRUCTIONS,
                ephemeral=True,
                model=provider.model,
                model_provider=_PROVIDER_ID,
                sandbox=Sandbox.workspace_write,
                config=_codex_thread_config(codex_home, allow_network=allow_network),
            )
            task.emit("codex.thread_started", f"Codex thread {thread.id}")
            turn = await thread.turn(
                task.prompt,
                approval_mode=ApprovalMode.deny_all,
                sandbox=Sandbox.workspace_write,
            )
            try:
                async for event in turn.stream():
                    await runtime._raise_if_cancelled(task)
                    method = str(getattr(event, "method", "") or "")
                    payload = getattr(event, "payload", None)
                    if method == "item/agentMessage/delta":
                        delta = str(getattr(payload, "delta", "") or "")
                        if delta:
                            final_parts.append(delta)
                    elif method == "item/started":
                        task.emit("codex.action_started", "Codex started a workspace action")
                    elif method == "item/completed":
                        task.emit("codex.action_completed", "Codex completed a workspace action")
                    elif method == "turn/completed":
                        turn_obj = getattr(payload, "turn", None)
                        status_obj = getattr(turn_obj, "status", "")
                        turn_status = str(getattr(status_obj, "value", status_obj) or "")
                        error_obj = getattr(turn_obj, "error", None)
                        if error_obj:
                            turn_error = str(error_obj)[:1000]
            except AgentTaskCancelled:
                try:
                    await turn.interrupt()
                except Exception:
                    pass
                raise

        if turn_status and turn_status not in {"completed", "succeeded"}:
            raise AgentRuntimeError(f"Codex turn {turn_status}: {turn_error or 'no additional error detail'}")
        final_response = "".join(final_parts).strip() or "Codex 已完成任务。"
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
