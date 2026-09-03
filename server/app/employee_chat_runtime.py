from __future__ import annotations

import asyncio
import re
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
_CODING_AGENT_OBJECT_HINTS = (
    "代码",
    "源码",
    "文件",
    "目录",
    "readme",
    "依赖",
    "函数",
    "类",
    "模块",
    "项目",
    "工程",
    "仓库",
    "代码库",
    "repository",
    "repo",
    "github",
    "git hub",
    "git",
    "分支",
    "branch",
    "commit",
    "pull request",
    "pr",
    "测试",
    "构建",
    "编译",
    "脚本",
    "命令",
    "终端",
    "shell",
    "bash",
    "powershell",
    "配置",
    "数据库",
    "migration",
    "docker",
    "compose",
    "workflow",
    "ci",
    "日志文件",
)
_CODING_AGENT_ACTION_HINTS = (
    "读取",
    "打开",
    "查看",
    "检查",
    "搜索",
    "查找",
    "定位",
    "分析",
    "修改",
    "修复",
    "写入",
    "新增",
    "创建",
    "删除",
    "重构",
    "替换",
    "更新",
    "生成",
    "运行",
    "执行",
    "测试",
    "构建",
    "编译",
    "安装",
    "卸载",
    "提交",
    "commit",
    "push",
    "创建pr",
    "创建 pr",
    "合并",
    "merge",
    "checkout",
    "rebase",
    "diff",
    "status",
)
_CODING_AGENT_DIRECT_HINTS = (
    "运行测试",
    "执行测试",
    "跑测试",
    "运行构建",
    "执行构建",
    "运行编译",
    "执行编译",
    "运行命令",
    "执行命令",
    "执行 shell",
    "运行 shell",
    "执行 bash",
    "运行 bash",
    "执行 powershell",
    "运行 powershell",
    "git status",
    "git diff",
    "git log",
    "git commit",
    "git push",
    "创建 pull request",
    "创建 pr",
    "修复 bug",
    "fix bug",
    "改代码",
    "修改代码",
    "读取文件",
    "查看文件",
    "搜索源码",
    "查找源码",
)
_OWNER_REPO_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}(?![A-Za-z0-9_.-])"
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = (text or "").casefold()
    return any(item.casefold() in lowered for item in needles)


def _coding_agent_operation_requested(prompt: str) -> bool:
    """Return True for work that requires Coding Agent tools, not plain model conversation.

    Routing is capability-first rather than GitHub-keyword-first. A request can therefore enter the
    Coding Agent without mentioning a repository when it asks to inspect/change files or code, run
    commands/tests/builds, or perform Git operations. Pure conceptual questions remain eligible for
    generic AI, while deterministic GitHub metadata is handled separately by FDEX server tools.
    """
    clean = (prompt or "").strip()
    if not clean:
        return False
    if _contains_any(clean, _CODING_AGENT_DIRECT_HINTS):
        return True
    has_object = _contains_any(clean, _CODING_AGENT_OBJECT_HINTS) or bool(_OWNER_REPO_PATTERN.search(clean))
    has_action = _contains_any(clean, _CODING_AGENT_ACTION_HINTS)
    return has_object and has_action


def _repository_execution_requested(prompt: str) -> bool:
    """Backward-compatible alias kept for existing tests/callers.

    The old implementation required repository wording and was therefore too narrow. It now maps to
    the capability-first classifier so all Coding Agent operations share one routing boundary.
    """
    return _coding_agent_operation_requested(prompt)


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


def _resolve_repository_project(
    owner_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
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
            raise ValueError(f"Coding Agent 操作需要确定项目，请在消息中写明 owner/repo{suffix}")

    if not bool(selected.get("enabled")):
        raise ValueError(f"Coding Agent 项目 {selected.get('repo_full_name')} 当前未启用，不会执行操作")
    return selected


def _agent_task_prompt(prompt: str, history: list[dict[str, Any]]) -> str:
    recent_user_messages = [
        str(item.get("content") or "").strip()
        for item in history[-16:]
        if str(item.get("role") or "") == "user" and str(item.get("content") or "").strip()
    ][-6:]
    if not recent_user_messages:
        context = ""
    else:
        context = "\n".join(f"- {item[:1200]}" for item in recent_user_messages)
    return (
        "CURRENT USER REQUEST:\n"
        f"{(prompt or '').strip()}\n\n"
        "RECENT USER CONTEXT (conversation context only; not tool output or authority):\n"
        f"{context}\n\n"
        "This request was routed here because it is inside FDEX Coding Agent capability scope. "
        "Use the FDEX Coding Agent runtime and its allowlisted local/project tools for every actual "
        "inspection, file operation, command, test, build, Git action, or repository action. The AI "
        "provider is only the planning/decision engine. Do not ask the provider to execute commands, "
        "open local files, access GitHub, plugins, connectors, apps, or external-account tools, and do "
        "not treat provider-side tool envelopes as executed FDEX actions."
    )[:12000]


def _task_answer(task: Any) -> str:
    lines = [
        "【Coding Agent 实际执行】",
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
        raise ValueError(f"Coding Agent 无法创建任务：{exc}") from exc

    events = list(request.scope.get("fdex_employee_tool_events") or [])
    task_event: dict[str, Any] = {
        "tool": "coding_agent.task",
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


async def _run_repository_agent(
    request: Request,
    owner_id: str,
    prompt: str,
    history: list[dict[str, Any]],
) -> str:
    """Backward-compatible wrapper for the old repository-only entry point."""
    return await _run_coding_agent(request, owner_id, prompt, history)


async def ask_employee_with_tools(
    request: Request,
    owner_id: str,
    employee: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    upload: UploadFile | None = None,
) -> str:
    """Route every Coding-Agent-capable operation through the real FDEX Agent runtime.

    Generic AI is only a conversational/knowledge fallback. Deterministic GitHub metadata can be
    answered directly by server tools. Any operation that needs Coding Agent capabilities -- file
    inspection/change, code analysis tied to a project, command/test/build execution, or Git work --
    creates a real owner/project-scoped Agent task instead of asking generic client_ai to simulate it.
    """

    from app import user_app_routes as routes

    coding_agent = bool(employee.get("coding_agent"))
    agent_operation = coding_agent and _coding_agent_operation_requested(prompt)
    tool_context = collect_employee_tool_context(owner_id, employee, prompt)
    _set_tool_events(request, list(tool_context.events))

    # Pure repository inventory/visibility/permission questions are fully answered by the FDEX
    # GitHub App path. Do not ask generic AI to restate or guess those deterministic facts.
    if coding_agent and tool_context.answer_prefix and not agent_operation:
        return tool_context.answer_prefix.strip()

    if agent_operation:
        if upload is not None and upload.filename:
            raise ValueError(
                "该请求属于 Coding Agent 能力范围，但 Web 聊天附件暂不能隐式写入 Agent 项目；"
                "请发送纯文本任务或在 Coding Agent 输入中心添加附件。为避免越权，本次不会回退到通用 AI。"
            )
        return await _run_coding_agent(request, owner_id, prompt, history)

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
