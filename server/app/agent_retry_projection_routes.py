from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request

from app.agent_tasks import agent_task_store
from app.codex_retry_chain_store import codex_retry_chain_store

_installed = False


def _execution_task_id(chain: dict[str, object] | None, fallback: str) -> str:
    if not chain:
        return fallback
    active = str(chain.get("active_attempt_task_id") or "")
    latest = str(chain.get("latest_attempt_task_id") or "")
    return active or latest or fallback


def install_agent_retry_projection_routes() -> None:
    """Extend the existing authenticated Agent API with logical retry-chain inspection.

    The normal ``GET /api/agent/tasks`` route already uses AgentTaskStore.list(), which hides
    ``task_kind=auto_retry`` by default. This endpoint exposes Phase 7.40 task identity/lineage plus
    the Phase 7.39 retry audit projection without changing old Android/API task-list response
    shapes.
    """

    global _installed
    if _installed:
        return

    from app import agent_routes

    @agent_routes.router.get("/tasks/{task_id}/retry-chain")
    async def get_retry_chain(task_id: str, request: Request) -> dict[str, object]:
        owner_id, _auth_mode = agent_routes._account_owner(request)
        try:
            task = await asyncio.to_thread(agent_task_store().get, owner_id, task_id)
        except ValueError:
            task = None
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        chain = await asyncio.to_thread(codex_retry_chain_store().chain_for_task, owner_id, task_id)
        logical_task_id = str(task.get("logical_root_id") or (chain or {}).get("root_task_id") or task_id)
        return {
            "task_id": task_id,
            "task_kind": str(task.get("task_kind") or "user"),
            "logical_task_id": logical_task_id,
            "attempt_index": int(task.get("attempt_index") or 0),
            "execution_task_id": _execution_task_id(chain, task_id),
            "retry_chain": chain,
        }

    _installed = True