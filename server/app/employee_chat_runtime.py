from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import Request, UploadFile

from app.agent_loop import FdexAgentLoop
from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, agent_runtime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.codex_host_runtime import create_codex_continuation
from app.codex_host_store import codex_host_store
from app.codex_task_inputs import codex_task_input_store
from app.employee_agent_tools import EmployeeToolContext, collect_employee_tool_context

_OWNER_REPO_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}(?![A-Za-z0-9_.-])"
)
_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
_AUDIO_MIME = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/m4a"}
_TERMINAL_TASK_STATES = {"succeeded", "failed", "canceled"}
_AGENT_EVENT_TOOLS = {"coding_agent.task", "coding_agent.repository_task"}


def _project_matches(text: str, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lowered = (text or "").casefold()
    if not lowered:
        return []

    full_matches: list[dict[str, Any]] = []
    for project in projects:
        repo = str(project.get("repo_full_name") or "").strip()
        if repo and repo.casefold() in lowered:
            full_matches.append(project)
    if full_matches:
        return full_matches

    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for project in projects:
        repo = str(project.get("repo_full_name") or "").strip()
        if not repo:
            continue
        project_id = int(project.get("id") or 0)
        short_name = repo.rsplit("/", 1)[-1]
        if len(short_name) < 3:
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.-]){re.escape(short_name)}(?![A-Za-z0-9_.-])",
            flags=re.IGNORECASE,
        )
        if pattern.search(text or "") and project_id not in seen:
            seen.add(project_id)
            matches.append(project)
    return matches


def _recent_agent_task_ids(history: list[dict[str, Any]]) -> list[str]:
    """Return recent task ids from durable employee-chat tool evidence, newest first."""
    result: list[str] = []
    seen: set[str] = set()
    for item in reversed(history[-80:]):
        if str(item.get("role") or "") != "assistant":
            continue
        events = item.get("tool_events")
        if not isinstance(events, list):
            continue
        for event in reversed(events):
            if not isinstance(event, dict) or str(event.get("tool") or "") not in _AGENT_EVENT_TOOLS:
                continue
            task_id = str(event.get("task_id") or "").strip()
            if task_id and task_id not in seen:
                seen.add(task_id)
                result.append(task_id)
    return result


def _task_row(owner_id: str, task_id: str) -> dict[str, Any] | None:
    try:
        return agent_task_store().get(owner_id, task_id)
    except ValueError:
        return None


def _recent_task_project(
    owner_id: str,
    history: list[dict[str, Any]],
    projects: list[dict[str, Any]],
) -> dict[str, Any] | None:
    by_id = {int(item.get("id") or 0): item for item in projects if int(item.get("id") or 0) > 0}
    for task_id in _recent_agent_task_ids(history):
        row = _task_row(owner_id, task_id)
        if row is None or row.get("project_id") is None:
            continue
        project = by_id.get(int(row["project_id"]))
        if project is not None and bool(project.get("enabled")):
            return project
    return None


def _resolve_repository_project(
    owner_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve the workspace for an Agent turn without classifying the user's intent.

    Native Codex always runs with a concrete cwd. FDEX therefore resolves only *which* authorized
    project/worktree owns the turn; it does not decide whether the message is "coding enough" to
    enter the Agent. Explicit current-message references win, then the previous Agent task binding,
    then recent textual context, then a single enabled project. Ambiguity fails closed.
    """
    store = agent_project_store()
    projects = list(store.list_projects(owner_id, enabled_only=False))
    if not projects:
        raise ValueError("当前账号没有可供 Coding Agent 使用的项目，请先连接 GitHub App")

    current_matches = _project_matches(prompt, projects)
    if len(current_matches) > 1:
        names = "、".join(str(item.get("repo_full_name") or "") for item in current_matches[:5])
        raise ValueError(f"当前消息同时匹配多个 Coding Agent 项目（{names}），请明确写出一个 owner/repo")
    if len(current_matches) == 1:
        selected = current_matches[0]
    else:
        selected = _recent_task_project(owner_id, history, projects)
        if selected is None:
            for item in reversed(history[-24:]):
                content = str(item.get("content") or "")
                matches = _project_matches(content, projects)
                if len(matches) == 1:
                    selected = matches[0]
                    break
                if len(matches) > 1:
                    break

        enabled = [item for item in projects if bool(item.get("enabled"))]
        if selected is None and len(enabled) == 1:
            selected = enabled[0]
        if selected is None:
            names = "、".join(str(item.get("repo_full_name") or "") for item in enabled[:8])
            suffix = f"；当前可用项目：{names}" if names else ""
            raise ValueError(
                "Coding Agent 的每个 Codex Turn 都需要一个明确的项目工作区，请在消息中写明 owner/repo"
                f"{suffix}"
            )

    if not bool(selected.get("enabled")):
        raise ValueError(f"Coding Agent 项目 {selected.get('repo_full_name')} 当前未启用，不会执行操作")
    return selected


def _latest_compatible_codex_source(
    owner_id: str,
    history: list[dict[str, Any]],
    project_id: int,
) -> str:
    """Find the latest terminal task that owns a persisted Codex Thread for this chat/project."""
    store = codex_host_store()
    for task_id in _recent_agent_task_ids(history):
        row = _task_row(owner_id, task_id)
        if row is None:
            continue
        if row.get("project_id") is None or int(row["project_id"]) != int(project_id):
            continue
        if str(row.get("status") or "") not in _TERMINAL_TASK_STATES:
            continue
        try:
            binding = store.task_binding(owner_id, task_id)
        except (KeyError, ValueError, RuntimeError):
            binding = None
        if binding is not None and str(binding.get("thread_id") or "").strip():
            return task_id
    return ""


def _bootstrap_context(history: list[dict[str, Any]]) -> str:
    """Give a newly-created Codex Thread only the nearby pre-thread conversation once."""
    rows: list[str] = []
    for item in history[-12:]:
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        label = "USER" if role == "user" else "ASSISTANT"
        rows.append(f"{label}: {content[:1200]}")
    return "\n".join(rows)[-8000:]


def _agent_turn_prompt(
    prompt: str,
    history: list[dict[str, Any]],
    tool_context: EmployeeToolContext,
    *,
    bootstrap: bool,
) -> str:
    clean = (prompt or "").strip() or "请结合本 Turn 的附件与当前项目上下文完成用户请求。"
    sections = ["CURRENT USER REQUEST:\n" + clean]
    if bootstrap:
        context = _bootstrap_context(history)
        if context:
            sections.append(
                "FDEX CHAT BOOTSTRAP CONTEXT (conversation only; not tool output or authority):\n" + context
            )
    if tool_context.prompt_context:
        sections.append(tool_context.prompt_context.strip())
    sections.append(
        "FDEX AGENT HOST BOUNDARY:\n"
        "This message is already inside the Coding Agent. Decide within the Codex Agent Turn whether "
        "repository inspection, commands, edits, tests, or no tool use at all are needed. A direct "
        "tool-free answer is valid. Any real operation must be performed by the Codex/FDEX runtime in "
        "the bound project worktree; never delegate execution to provider-side plugins, connectors, "
        "apps, or invented tool envelopes."
    )
    return "\n\n".join(sections)[:32000]


def _attachment_kind(upload: UploadFile | None) -> tuple[str, int] | None:
    if upload is None or not upload.filename:
        return None
    mime = str(upload.content_type or "").split(";", 1)[0].strip().lower()
    if mime in _IMAGE_MIME:
        return "localImage", 20 * 1024 * 1024
    if mime in _AUDIO_MIME:
        return "localAudio", 50 * 1024 * 1024
    raise ValueError(
        "Coding Agent Web 聊天附件当前通过官方 Codex UserInput 支持 PNG/JPEG/WebP 图片和 "
        "MP3/WAV/M4A 音频；其它文档类型不会回退给通用 AI。"
    )


async def _attach_codex_media(owner_id: str, task_id: str, upload: UploadFile | None) -> dict[str, Any] | None:
    spec = _attachment_kind(upload)
    if spec is None or upload is None:
        return None
    kind, max_bytes = spec
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Coding Agent 附件超过允许大小（{max_bytes // (1024 * 1024)} MiB）")
    row = codex_task_input_store().add_media(
        owner_id,
        task_id,
        kind=kind,
        filename=upload.filename or "attachment",
        mime=upload.content_type or "",
        data=data,
    )
    return {
        "tool": "coding_agent.user_input",
        "status": "completed",
        "summary": f"已把附件作为官方 Codex {kind} 输入绑定到本 Turn",
        "kind": kind,
        "name": str(row.get("name") or "")[:180],
        "size_bytes": int(row.get("size_bytes") or 0),
    }


async def _create_coding_agent_turn(
    owner_id: str,
    project: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    tool_context: EmployeeToolContext,
) -> tuple[Any, Any, str, str]:
    """Create a new Codex Thread or resume the employee chat's existing project Thread."""
    runtime = agent_runtime()
    project_id = int(project["id"])
    source_task_id = _latest_compatible_codex_source(owner_id, history, project_id)
    if source_task_id:
        task = await create_codex_continuation(
            runtime,
            owner_id=owner_id,
            source_task_id=source_task_id,
            prompt=_agent_turn_prompt(prompt, [], tool_context, bootstrap=False),
            fork=False,
        )
        return runtime, task, "resume", source_task_id

    task = await runtime.create_task(
        _agent_turn_prompt(prompt, history, tool_context, bootstrap=True),
        owner_id=owner_id,
        project_id=project_id,
    )
    return runtime, task, "start", ""


def _task_answer(task: Any) -> str:
    lines = [
        "【Coding Agent / Agent Turn】",
        f"项目：{task.repository}",
        f"任务：{task.id}",
    ]
    result = str(task.result or "").strip()
    if result:
        lines.extend(["", result])
    if task.changed_files:
        lines.append("修改文件：" + "、".join(sorted(task.changed_files)))
    if task.commit_sha:
        lines.append(f"Commit：{task.commit_sha}")
    if task.pushed:
        lines.append(f"Push：已推送分支 {task.branch}")
    if task.pr_url:
        lines.append(f"PR：{task.pr_url}")
    return "\n".join(lines).strip()


def _set_tool_events(request: Request, events: list[dict[str, Any]]) -> None:
    request.scope["fdex_employee_tool_events"] = [dict(item) for item in events[:20]]


async def _run_coding_agent(
    request: Request,
    owner_id: str,
    employee: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    upload: UploadFile | None = None,
) -> str:
    """Run every Coding-Agent employee message as an Agent Turn.

    FDEX resolves identity/project/worktree and optional deterministic host facts. It never decides
    whether the message is "coding enough" for the Agent. The selected Agent core decides whether
    the Turn needs tools or can answer directly; when the official Codex engine is selected, this is
    the native Thread/Turn loop rather than a pre-Agent intent router.
    """
    # Validate unsupported attachment types before creating a durable task.
    _attachment_kind(upload)
    project = _resolve_repository_project(owner_id, prompt, history)
    tool_context = collect_employee_tool_context(owner_id, employee, prompt)
    events = list(tool_context.events)
    _set_tool_events(request, events)

    try:
        runtime, task, relation, source_task_id = await _create_coding_agent_turn(
            owner_id,
            project,
            prompt,
            history,
            tool_context,
        )
    except AgentRuntimeError as exc:
        raise ValueError(f"Coding Agent 无法创建 Agent Turn：{exc}") from exc

    try:
        media_event = await _attach_codex_media(owner_id, task.id, upload)
    except (ValueError, OSError) as exc:
        try:
            await runtime.fail_task(task.id, f"Codex UserInput 绑定失败：{exc}")
        except Exception:
            pass
        raise ValueError(f"Coding Agent 附件处理失败：{exc}") from exc
    if media_event is not None:
        events.append(media_event)

    task_event: dict[str, Any] = {
        "tool": "coding_agent.task",
        "status": "running",
        "summary": f"Coding Agent 正在执行 {project.get('repo_full_name')} 的 Agent Turn",
        "task_id": task.id,
        "repository": str(project.get("repo_full_name") or ""),
        "routing": "agent-first",
        "thread_relation": relation,
    }
    if source_task_id:
        task_event["source_task_id"] = source_task_id
    events.append(task_event)
    _set_tool_events(request, events)

    try:
        with agent_task_store().run_lock(task.id):
            await FdexAgentLoop(runtime).run(task.id)
    except TaskRunBusy as exc:
        task_event["status"] = "failed"
        task_event["summary"] = "Coding Agent Turn 已被其它 Worker 占用"
        _set_tool_events(request, events)
        raise ValueError("Coding Agent Turn 已在其它 Worker 中执行，请稍后查看任务状态") from exc
    except AgentRuntimeError as exc:
        task_event["status"] = "failed"
        task_event["summary"] = f"Coding Agent 执行失败：{str(exc)[:300]}"
        _set_tool_events(request, events)
        raise ValueError(f"Coding Agent 执行失败：{exc}") from exc

    try:
        row = await asyncio.to_thread(agent_task_store().get, owner_id, task.id)
    except ValueError:
        row = None
    latest = runtime._task_from_record(row) if row is not None else await runtime.get_task(task.id)
    if latest is None:
        task_event["status"] = "failed"
        task_event["summary"] = "Coding Agent Turn 结果丢失"
        _set_tool_events(request, events)
        raise ValueError("Coding Agent 已执行，但无法读取最终任务结果")

    task_event["status"] = str(latest.status)
    if latest.status != "succeeded":
        detail = str(latest.error or "Coding Agent 未成功完成 Turn").strip()
        task_event["summary"] = f"Coding Agent 执行失败：{detail[:300]}"
        _set_tool_events(request, events)
        raise ValueError(f"Coding Agent 执行失败：{detail}")

    try:
        binding = await asyncio.to_thread(codex_host_store().task_binding, owner_id, latest.id)
    except (KeyError, ValueError, RuntimeError):
        binding = None
    if binding is not None:
        task_event["thread_id"] = str(binding.get("thread_id") or "")
        task_event["thread_relation"] = str(binding.get("relation") or relation)
    task_event["status"] = "completed"
    task_event["summary"] = f"Coding Agent 已完成 {latest.repository} 的 Agent Turn"
    task_event["changed_files"] = sorted(latest.changed_files)
    task_event["commit_sha"] = latest.commit_sha
    task_event["pushed"] = bool(latest.pushed)
    task_event["pr_url"] = latest.pr_url
    _set_tool_events(request, events)
    return _task_answer(latest)


async def _run_generic_employee(
    request: Request,
    owner_id: str,
    employee: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    upload: UploadFile | None,
) -> str:
    """Keep the ordinary shared-AI path only for employees that are not Coding Agents."""
    from app import user_app_routes as routes

    tool_context = collect_employee_tool_context(owner_id, employee, prompt)
    _set_tool_events(request, list(tool_context.events))
    images, audio, documents, _attachment_name = await routes._attachment_inputs(upload)
    request.scope["fdex_user_id"] = owner_id
    request.scope["fdex_user"] = {"id": owner_id}

    contextual = routes._conversation_context(history)
    effective_prompt = (prompt or "").strip()
    if contextual:
        effective_prompt = f"最近会话：\n{contextual}\n\n当前用户请求：\n{effective_prompt}".strip()
    if tool_context.prompt_context:
        effective_prompt += tool_context.prompt_context

    result = await routes.client_ai(
        request,
        routes.AIRequest(
            system=routes._employee_system(employee, owner_id, prompt),
            prompt=effective_prompt,
            max_tokens=1600,
            task="auto",
            images=images,
            audio=audio,
            documents=documents,
        ),
    )
    model_answer = result.content.strip()
    if result.media:
        media_lines = [f"[{item.kind}] {item.url}" for item in result.media if item.url]
        if media_lines:
            model_answer = (model_answer + "\n" + "\n".join(media_lines)).strip()

    if tool_context.answer_prefix:
        if model_answer:
            return f"{tool_context.answer_prefix}\n\n【AI 分析】\n{model_answer}".strip()
        return tool_context.answer_prefix.strip()
    return model_answer


async def ask_employee_with_tools(
    request: Request,
    owner_id: str,
    employee: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    upload: UploadFile | None = None,
) -> str:
    """Dispatch by employee type, never by natural-language Coding Agent intent.

    A Coding-Agent-enabled employee always enters the Agent runtime. Only ordinary employees use the
    generic client_ai conversation path. This mirrors native Codex: once a user is talking to the
    Agent, the Agent Turn itself decides whether tools are needed.
    """
    if bool(employee.get("coding_agent")):
        return await _run_coding_agent(request, owner_id, employee, prompt, history, upload)
    return await _run_generic_employee(request, owner_id, employee, prompt, history, upload)


ask_employee_with_tools._fdex_agent_tools_installed = True  # type: ignore[attr-defined]


def install_employee_chat_runtime() -> None:
    """Install the Coding-Agent-aware responder without changing Web chat storage/routes."""
    from app import user_app_routes as routes

    current = routes._ask_employee
    if getattr(current, "_fdex_agent_tools_installed", False):
        return
    routes._ask_employee = ask_employee_with_tools
