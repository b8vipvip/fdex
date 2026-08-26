from __future__ import annotations

from typing import Any

from fastapi import Request, UploadFile

from app.employee_agent_tools import collect_employee_tool_context


def install_employee_chat_runtime() -> None:
    """Give Coding-Agent-enabled Web employees real owner-scoped tools before AI synthesis.

    Phase 7.9 exposed a `coding_agent` employee flag in the UI, but employee chat still called the
    generic AI provider directly. That meant a question such as “当前 GitHub 有哪些仓库？” was
    answered from model guesswork even though the employee was explicitly granted Coding Agent
    capability. This installer wraps the existing employee responder without changing the durable
    Web workspace or the shared Android/provider API.
    """

    from app import user_app_routes as routes

    current = routes._ask_employee
    if getattr(current, "_fdex_agent_tools_installed", False):
        return

    async def tool_aware_ask_employee(
        request: Request,
        owner_id: str,
        employee: dict[str, Any],
        prompt: str,
        history: list[dict[str, Any]],
        upload: UploadFile | None = None,
    ) -> str:
        tool_context = collect_employee_tool_context(owner_id, employee, prompt)
        request.scope["fdex_employee_tool_events"] = list(tool_context.events)
        effective_prompt = (prompt or "").strip()
        if tool_context.prompt_context:
            effective_prompt += tool_context.prompt_context
        return await current(request, owner_id, employee, effective_prompt, history, upload)

    tool_aware_ask_employee._fdex_agent_tools_installed = True  # type: ignore[attr-defined]
    tool_aware_ask_employee.__module__ = "app.employee_chat_runtime"
    routes._ask_employee = tool_aware_ask_employee
