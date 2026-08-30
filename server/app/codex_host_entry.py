from __future__ import annotations

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.codex_host_guard import run_codex_task as guarded_run_codex_task
from app.codex_interaction_install import codex_interaction_scope
from app.codex_remote_mcp_install import codex_remote_mcp_scope
from app.codex_task_input_install import codex_task_input_scope, install_codex_task_input_runtime

install_codex_task_input_runtime()


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    # Interaction, Remote MCP and official UserInput resolution are independent task-scoped
    # capabilities around the same durable Host. Every scope cleans itself up on terminal exit.
    with (
        codex_interaction_scope(task),
        codex_remote_mcp_scope(task.owner_id, task.id),
        codex_task_input_scope(task),
    ):
        await guarded_run_codex_task(runtime, task_id)
