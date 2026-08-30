from __future__ import annotations

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.codex_host_capabilities import codex_host_capability_scope
from app.codex_host_guard import run_codex_task as guarded_run_codex_task
from app.codex_interaction_install import codex_interaction_scope
from app.codex_remote_mcp_install import codex_remote_mcp_scope


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    # Install left-to-right: the durable interaction broker owns generic app-server requests;
    # Phase 7.29 then composes safe item/tool/call + rich UserInput on that seam; Remote MCP is an
    # independent task-scoped capability. Every scope cleans up on success/failure/cancellation.
    with (
        codex_interaction_scope(task),
        codex_host_capability_scope(task),
        codex_remote_mcp_scope(task.owner_id, task.id),
    ):
        await guarded_run_codex_task(runtime, task_id)