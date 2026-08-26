from __future__ import annotations

from typing import Any

from fastapi import Request, UploadFile

from app.employee_agent_tools import collect_employee_tool_context


def install_employee_chat_runtime() -> None:
    """Give Coding-Agent-enabled Web employees real owner-scoped tools before AI synthesis.

    Phase 7.9 exposed a `coding_agent` employee flag in the UI, but employee chat still called the
    generic AI provider directly. That meant a question such as “当前 GitHub 有哪些仓库？” was
    answered from model guesswork even though the employee was explicitly granted Coding Agent
    capability. This installer replaces only the responder while preserving the existing attachment,
    conversation-context and shared provider paths.
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
        # Collect tools from the original user request. Do not feed external GitHub metadata into
        # knowledge retrieval or employee-role selection; tool output is appended only to the final
        # model prompt as a clearly delimited, non-instruction data block.
        tool_context = collect_employee_tool_context(owner_id, employee, prompt)
        request.scope["fdex_employee_tool_events"] = list(tool_context.events)

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
        answer = result.content.strip()
        if result.media:
            media_lines = [f"[{item.kind}] {item.url}" for item in result.media if item.url]
            if media_lines:
                answer = (answer + "\n" + "\n".join(media_lines)).strip()
        return answer

    tool_aware_ask_employee._fdex_agent_tools_installed = True  # type: ignore[attr-defined]
    tool_aware_ask_employee.__module__ = "app.employee_chat_runtime"
    routes._ask_employee = tool_aware_ask_employee
