from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent_loop import FdexAgentLoop
from app.agent_runtime import AgentRuntimeError, AgentTask, agent_runtime
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix=f"{settings.api_prefix}/agent", tags=["agent"])


class AgentTaskCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)


class AgentToolRunRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


def _task_payload(task: AgentTask) -> dict[str, object]:
    return {
        "id": task.id,
        "prompt": task.prompt,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "branch": task.branch,
        "worktree": task.worktree,
        "commit_sha": task.commit_sha,
        "changed_files": sorted(task.changed_files),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "events": [
            {
                "type": event.type,
                "message": event.message,
                "created_at": event.created_at,
            }
            for event in task.events
        ],
    }


@router.get("/capabilities")
def capabilities() -> dict[str, object]:
    return agent_runtime().capabilities()


@router.post("/tasks")
async def create_task(request: AgentTaskCreateRequest) -> dict[str, object]:
    try:
        task = await agent_runtime().create_task(request.prompt)
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _task_payload(task)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, object]:
    task = await agent_runtime().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_payload(task)


@router.post("/tasks/{task_id}/run")
async def run_agent(task_id: str) -> dict[str, object]:
    runtime = agent_runtime()
    task = await runtime.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        await FdexAgentLoop(runtime).run(task_id)
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task = await runtime.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_payload(task)


@router.post("/tasks/{task_id}/tools/run")
async def run_tool(task_id: str, request: AgentToolRunRequest) -> dict[str, object]:
    try:
        task = await agent_runtime().run_inspection(task_id, request.tool, request.args)
    except AgentRuntimeError as exc:
        message = str(exc)
        status_code = 404 if message == "task not found" else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return _task_payload(task)
