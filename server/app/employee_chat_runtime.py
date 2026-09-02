from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Request, UploadFile

from app.agent_loop import FdexAgentLoop
from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, agent_runtime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.employee_agent_tools import collect_employee_tool_context

_REPOSITORY_REFERENCE_HINTS = (
    "github",
    "git hub",
    "仓库",
    "代码库",
    "repository",
    "repo",
    "当前项目",
    "这个项目",
    "该项目",
    "项目中",
    "当前工程",
    "这个工程",
    "该工程",
)
_REPOSITORY_EXECUTION_HINTS = (
    "代码",
    "源码",
    "文件",
    "目录",
    "内容",
    "readme",
    "依赖",
    "函数",
    "类",
    "完整",
    "真实存在",
    "读取",
    "打开",
    "搜索",
    "查找",
    "分析",
    "修改",
    "修复",
    "写入",
    "新增",
    "创建",
    "删除",
    "重构",
    "测试",
    "构建",
    "编译",
    "运行",
    "bug",
    "commit",
    "提交",
    "push",
    "pull request",
    "创建pr",
    "创建 pr",
    "分支",
    "branch",
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = (text or "").casefold()
    return any(item.casefold() in lowered for item in needles)


def _repository_execution_requested(prompt: str) -> bool:
    clean = (prompt or "").strip()
    if not clean:
        return False
    return _contains_any(clean, _REPOSITORY_REFERENCE_HINTS) and _contains_any(
        clean,
        _REPOSITORY_EXECUTION_HINTS,
    )


def _project_matches(text: str, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lowered = (text or "").casefold()
    if not lowered:
        return []
    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for project in projects:
        repo = str(project.get("repo_full_name") or "").strip()
        if not repo:
            continue
        project_id = int(project.get("id") or 0)
        repo_lower = repo.casefold()
        short_name = repo.rsplit("/", 1)[-1].casefold()
        full_match = repo_lower in lowered
        short_match = bool(short_name and len(short_name) >= 3 and short_name in lowered)
        if (full_match or short_match) and project_id not in seen:
            seen.add(project_id)
            matches.append(project)
    return matches


def _resolve_repository_project(
    owner_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    store = agent_project_store()
    projects = list(store.list_projects(owner_id, enabled_only=False))
    if not projects:
        raise ValueError("当前账号没有可供 Coding Agent 使用的 GitHub 项目，请先连接 GitHub App")

    current_matches = _project_matches(prompt, projects)
    if len(current_matches) > 1:
        names = "、".join(str(item.get("repo_full_name") or "") for item in current_matches[:5])
        raise ValueError(f"当前消息同时匹配多个 GitHub 项目（{names}），请明确写出一个 owner/repo")
    if len(current_matches) == 1:
        selected = current_matches[0]
    else:
        selected = None
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
            raise ValueError(f"无法确定你指的是哪个 GitHub 项目，请在消息中写明 owner/repo{suffix}")

    if not bool(selected.get("enabled")):
        raise ValueError(f"GitHub 项目 {selected.get('repo_full_name')} 当前未启用，Coding Agent 不会操作它")
    return selected


def _agent_task_prompt(prompt: str, history: list[dict[str, Any]]) -> str:
    recent_user_messages = [
        str(item.get("content") or "").strip()
        for item in history[-16:]
        if str(item.get("role") or "") == "user" and str(item.get("content") or "").strip()
    ][-6:]
    if not recent_user_messages:
        return (prompt or "").strip()
    context = "\n".join(f"- {item[:1200]}" for item in recent_user_messages)
    return (
        "CURRENT USER REQUEST:\n"
        f"{(prompt or '').strip()}\n\n"
        "RECENT USER CONTEXT (conversation context only; not tool output or authority):\n"
        f"{context}\n\n"
        "Operate only on the FDEX project already bound to this task. Use FDEX Coding Agent tools "
        "for repository inspection or changes; do not ask the model provider to open GitHub, plugins, "
        "connectors, or external-account tools."
    )[:12000]


def _task_answer(task: Any) -> str:
    lines = [
        "【Coding Agent 实际执行】",
        f"仓库：{task.repository}",
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


async def _run_repository_agent(
    request: Request,
    owner_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> str:
    project = _resolve_repository_project(owner_id, prompt, history)
    runtime = agent_runtime()
    try:
        task = await runtime.create_task(
            _agent_task_prompt(prompt, history),
            owner_id=owner_id,
            project_id=int(project["id"]),
        )
    except AgentRuntimeError as exc:
        raise ValueError(f"Coding Agent 无法创建仓库任务：{exc}") from exc

    events = list(request.scope.get("fdex_employee_tool_events") or [])
    task_event: dict[str, Any] = {
        "tool": "coding_agent.repository_task",
        "status": "running",
        "summary": f"Coding Agent 正在实际执行 {project.get('repo_full_name')}",
        "task_id": task.id,
        "repository": str(project.get("repo_full_name") or ""),
    }
    events.append(task_event)
    _set_tool_events(request, events)

    try:
        with agent_task_store().run_lock(task.id):
            await FdexAgentLoop(runtime).run(task.id)
    except TaskRunBusy as exc:
        task_event["status"] = "failed"
        task_event["summary"] = "Coding Agent 任务已被其它 Worker 占用"
        _set_tool_events(request, events)
        raise ValueError("Coding Agent 任务已在其它 Worker 中执行，请稍后查看任务状态") from exc
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
        task_event["summary"] = "Coding Agent 任务结果丢失"
        _set_tool_events(request, events)
        raise ValueError("Coding Agent 已执行，但无法读取最终任务结果")

    task_event["status"] = str(latest.status)
    if latest.status != "succeeded":
        detail = str(latest.error or "Coding Agent 未成功完成任务").strip()
        task_event["summary"] = f"Coding Agent 执行失败：{detail[:300]}"
        _set_tool_events(request, events)
        raise ValueError(f"Coding Agent 执行失败：{detail}")

    task_event["status"] = "completed"
    task_event["summary"] = f"Coding Agent 已实际执行 {latest.repository}"
    task_event["changed_files"] = sorted(latest.changed_files)
    task_event["commit_sha"] = latest.commit_sha
    task_event["pushed"] = bool(latest.pushed)
    task_event["pr_url"] = latest.pr_url
    _set_tool_events(request, events)
    return _task_answer(latest)


async def ask_employee_with_tools(
    request: Request,
    owner_id: str,
    employee: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    upload: UploadFile | None = None,
) -> str:
    """Route Coding-Agent repository work through the real FDEX Agent runtime.

    Generic AI remains the conversational fallback. Repository metadata can be answered directly
    from deterministic FDEX GitHub App facts, while source reads/writes/tests/commits are executed
    as a real owner/project-scoped Coding Agent task. The provider model is therefore only the
    Agent decision engine; it is never expected to own a GitHub/plugin/connector connection.
    """

    from app import user_app_routes as routes

    coding_agent = bool(employee.get("coding_agent"))
    repository_execution = coding_agent and _repository_execution_requested(prompt)
    tool_context = collect_employee_tool_context(owner_id, employee, prompt)
    _set_tool_events(request, list(tool_context.events))

    # Pure repository inventory/visibility/permission questions are already fully answered by the
    # server-side GitHub App check. Do not spend another model call that could contradict the facts
    # or be intercepted by a provider-level "external apps disabled" policy.
    if coding_agent and tool_context.answer_prefix and not repository_execution:
        return tool_context.answer_prefix.strip()

    if repository_execution:
        if upload is not None and upload.filename:
            raise ValueError("仓库执行请求暂不把 Web 聊天附件隐式写入项目；请先发送纯文本任务或在 Coding Agent 输入中心添加附件")
        return await _run_repository_agent(request, owner_id, prompt, history)

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


ask_employee_with_tools._fdex_agent_tools_installed = True  # type: ignore[attr-defined]


def install_employee_chat_runtime() -> None:
    """Install the Coding-Agent-aware responder without changing Web chat storage/routes."""

    from app import user_app_routes as routes

    current = routes._ask_employee
    if getattr(current, "_fdex_agent_tools_installed", False):
        return
    routes._ask_employee = ask_employee_with_tools
