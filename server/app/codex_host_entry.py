from __future__ import annotations

import asyncio

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.codex_host_guard import run_codex_task as guarded_run_codex_task
from app.codex_host_store import codex_host_store
from app.codex_interaction_install import codex_interaction_scope
from app.codex_remote_mcp_install import codex_remote_mcp_scope
from app.codex_task_input_install import codex_task_input_scope, install_codex_task_input_runtime
from app.codex_task_inputs import codex_task_input_store

install_codex_task_input_runtime()


async def _inherit_retry_inputs(task: object) -> None:
    owner_id = str(getattr(task, "owner_id", "") or "")
    task_id = str(getattr(task, "id", "") or "")
    parent_task_id = str(getattr(task, "parent_task_id", "") or "")
    if not owner_id or not task_id or not parent_task_id:
        return
    store = codex_task_input_store()
    if await asyncio.to_thread(store.list, owner_id, task_id):
        return
    # Resume/Fork already inherit the official Thread history and should not duplicate media.
    binding = await asyncio.to_thread(codex_host_store().task_binding, owner_id, task_id)
    if binding is not None:
        return
    await asyncio.to_thread(store.clone_task, owner_id, parent_task_id, task_id)


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    await _inherit_retry_inputs(task)
    # Interaction, Remote MCP and official UserInput resolution are independent task-scoped
    # capabilities around the same durable Host. Every scope cleans itself up on terminal exit.
    with (
        codex_interaction_scope(task),
        codex_remote_mcp_scope(task.owner_id, task.id),
        codex_task_input_scope(task),
    ):
        await guarded_run_codex_task(runtime, task_id)
