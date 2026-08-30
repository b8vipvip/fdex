from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, Iterator

from app.agent_runtime import AgentTask
from app.codex_app_server import CodexServerRequestDenied
from app.codex_task_inputs import codex_task_input_store

_DYNAMIC_METHOD = "item/tool/call"
_NAMESPACE = "fdex_host"
_current_task: ContextVar[AgentTask | None] = ContextVar("fdex_codex_host_capability_task", default=None)
_installed = False


def dynamic_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "namespace",
            "name": _NAMESPACE,
            "description": "Read-only FDEX host metadata for the current isolated Coding Agent task.",
            "tools": [
                {
                    "type": "function",
                    "name": "task_info",
                    "description": "Return bounded non-secret metadata for the current FDEX task.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "list_inputs",
                    "description": "List names and kinds of FDEX rich input items attached to this task. Does not return server paths or secret contents.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            ],
        }
    ]


def _output(payload: Any, *, success: bool = True) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > 128 * 1024:
        raise CodexServerRequestDenied("FDEX dynamic tool output exceeds 128 KiB")
    return {"contentItems": [{"type": "inputText", "text": text}], "success": bool(success)}


def _validate_call(params: dict[str, Any]) -> tuple[AgentTask, str]:
    task = _current_task.get()
    if task is None:
        raise CodexServerRequestDenied("FDEX has no task scope for dynamic tool call")
    namespace = str(params.get("namespace") or "")
    tool = str(params.get("tool") or "")
    arguments = params.get("arguments")
    if namespace != _NAMESPACE:
        raise CodexServerRequestDenied("FDEX denies unknown dynamic tool namespace")
    if tool not in {"task_info", "list_inputs"}:
        raise CodexServerRequestDenied("FDEX denies unknown dynamic tool")
    if not isinstance(arguments, dict) or arguments:
        raise CodexServerRequestDenied("FDEX host tools accept no arguments")
    for key in ("threadId", "turnId", "callId"):
        if not str(params.get(key) or "").strip():
            raise CodexServerRequestDenied(f"FDEX dynamic tool request is missing {key}")
    return task, tool


async def handle_dynamic_tool(params: dict[str, Any]) -> dict[str, Any]:
    task, tool = _validate_call(params)
    if tool == "task_info":
        return _output(
            {
                "task_id": task.id,
                "project_id": task.project_id,
                "status": task.status,
                "branch": task.branch,
            }
        )
    rows = codex_task_input_store().list(task.owner_id, task.id)
    return _output(
        {
            "inputs": [
                {
                    "id": str(row.get("id") or ""),
                    "kind": str(row.get("kind") or ""),
                    "name": str(row.get("display_name") or ""),
                    "mime_type": str(row.get("mime_type") or ""),
                    "size_bytes": int(row.get("size_bytes") or 0),
                }
                for row in rows
            ]
        }
    )


def install_codex_host_capabilities() -> None:
    global _installed
    if _installed:
        return
    import app.codex_host_runtime as host
    import app.codex_interaction_install as interaction_install

    # Phase 7.23 owns the interactive client seam. Install it first, then compose the dynamic-tool
    # dispatcher on top so approvals/requestUserInput/MCP elicitation continue to use the durable
    # broker while only item/tool/call reaches this compiled host-tool table.
    interaction_install.install_codex_interaction_runtime()
    original_common = host._thread_common_params
    original_turn_start = host.turn_start_params
    original_client = interaction_install.ContextInteractiveCodexAppServerClient

    @wraps(original_common)
    def capability_common(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = dict(original_common(*args, **kwargs))
        payload["dynamicTools"] = dynamic_tool_specs()
        return payload

    @wraps(original_turn_start)
    def rich_turn_start(thread_id: str, prompt: str) -> dict[str, Any]:
        payload = dict(original_turn_start(thread_id, prompt))
        task = _current_task.get()
        if task is None:
            raise RuntimeError("FDEX rich input scope is missing")
        worktree = Path(str(task.worktree or "")).expanduser().resolve()
        if not worktree.is_dir():
            raise RuntimeError("FDEX task worktree is unavailable while building Codex UserInput")
        payload["input"] = codex_task_input_store().build_user_input(
            task.owner_id,
            task.id,
            prompt=prompt,
            worktree=worktree,
        )
        return payload

    class CapabilityCodexAppServerClient(original_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            # Do not forward an interactive_request_handler: the Phase 7.23 Context client owns
            # creation of the owner/task durable broker handler. We wrap that concrete handler only
            # after its constructor has installed it.
            kwargs.pop("interactive_request_handler", None)
            super().__init__(*args, **kwargs)
            existing = self.interactive_request_handler

            async def dispatch(request_id: int | str, method: str, params: dict[str, Any]) -> Any:
                if method == _DYNAMIC_METHOD:
                    return await handle_dynamic_tool(params)
                return await existing(request_id, method, params)

            self.interactive_request_handler = dispatch

    host._thread_common_params = capability_common
    host.turn_start_params = rich_turn_start
    interaction_install.ContextInteractiveCodexAppServerClient = CapabilityCodexAppServerClient
    host.CodexAppServerClient = CapabilityCodexAppServerClient
    _installed = True


@contextmanager
def codex_host_capability_scope(task: AgentTask) -> Iterator[None]:
    install_codex_host_capabilities()
    token = _current_task.set(task)
    try:
        yield
    finally:
        _current_task.reset(token)