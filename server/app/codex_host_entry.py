from __future__ import annotations

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.codex_host_guard import run_codex_task as guarded_run_codex_task
from app.codex_interaction_install import codex_interaction_scope


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    with codex_interaction_scope(task):
        await guarded_run_codex_task(runtime, task_id)
