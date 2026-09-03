from __future__ import annotations

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime


class FdexAgentLoop:
    """Compatibility facade for the Coding Agent execution entry point.

    Phase 7.36 removes the legacy FDEX model/tool loop and the legacy/auto engine selector.
    Production Coding Agent tasks always execute through the official Codex native Host.  The
    class name is retained only so existing HTTP/background-task call sites keep one stable entry
    point while the implementation underneath is now Codex-only.
    """

    def __init__(self, runtime: FdexAgentRuntime) -> None:
        self.runtime = runtime

    async def run(self, task_id: str) -> None:
        task = await self.runtime.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if task.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {task.status}")

        # Import at execution time so Phase 7.33's rollout installer can rebind the status/provider
        # seams before FastAPI serves traffic.  A missing/stale full-smoke proof therefore fails
        # closed instead of falling back to the deleted legacy Agent loop.
        from app.codex_engine import codex_runtime_status
        from app.codex_host_entry import run_codex_task

        status = codex_runtime_status()
        if not bool(status.get("ready")):
            reason = str(status.get("reason") or "Codex engine is not ready")
            await self.runtime.fail_task(task_id, reason)
            return

        await run_codex_task(self.runtime, task_id)
