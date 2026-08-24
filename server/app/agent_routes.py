from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent_access import agent_token_valid
from app.agent_loop import FdexAgentLoop
from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, AgentTask, agent_runtime
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix=f"{settings.api_prefix}/agent", tags=["agent"])


class AgentTaskCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    project_id: int | None = Field(default=None, ge=1)


class AgentToolRunRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


def _require_agent_access(request: Request) -> None:
    configured = settings.fdex_agent_access_token
    if not configured.strip():
        raise HTTPException(status_code=503, detail="FDEX Agent access token is not configured")
    provided = request.headers.get("X-FDEX-Agent-Token", "")
    if not agent_token_valid(configured, provided):
        raise HTTPException(status_code=401, detail="invalid FDEX Agent token")


def _owner_id() -> str:
    # Current FDEX deployment is single-account. Never trust a client-supplied owner id;
    # future multi-account auth must resolve this scope from the authenticated server session.
    return settings.fdex_agent_default_owner.strip() or "local"


def _task_payload(task: AgentTask) -> dict[str, object]:
    return {
        "id": task.id,
        "prompt": task.prompt,
        "owner_id": task.owner_id,
        "project_id": task.project_id,
        "project_name": task.project_name,
        "repository": task.repository,
        "base_branch": task.base_branch,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "branch": task.branch,
        "worktree": task.worktree,
        "commit_sha": task.commit_sha,
        "pushed": task.pushed,
        "pr_url": task.pr_url,
        "changed_files": sorted(task.changed_files),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "events": [{"type": event.type, "message": event.message, "created_at": event.created_at} for event in task.events],
    }


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, object]:
    _require_agent_access(request)
    return agent_runtime().capabilities()


@router.get("/projects")
def projects(request: Request) -> dict[str, object]:
    _require_agent_access(request)
    owner_id = _owner_id()
    try:
        items = agent_project_store().list_projects(owner_id, enabled_only=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "owner_id": owner_id,
        "ai_source": "shared_provider_pool",
        "projects": [
            {
                "id": item["id"], "name": item["name"], "repository": item["repo_full_name"],
                "base_branch": item["base_branch"], "allow_push": item["allow_push"], "allow_pr": item["allow_pr"],
            }
            for item in items
        ],
    }


@router.post("/tasks")
async def create_task(request_body: AgentTaskCreateRequest, request: Request) -> dict[str, object]:
    _require_agent_access(request)
    try:
        task = await agent_runtime().create_task(request_body.prompt, owner_id=_owner_id(), project_id=request_body.project_id)
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _task_payload(task)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, object]:
    _require_agent_access(request)
    task = await agent_runtime().get_task(task_id)
    if task is None or task.owner_id != _owner_id():
        raise HTTPException(status_code=404, detail="task not found")
    return _task_payload(task)


@router.post("/tasks/{task_id}/run")
async def run_agent(task_id: str, request: Request) -> dict[str, object]:
    _require_agent_access(request)
    runtime = agent_runtime()
    task = await runtime.get_task(task_id)
    if task is None or task.owner_id != _owner_id():
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
async def run_tool(task_id: str, request_body: AgentToolRunRequest, request: Request) -> dict[str, object]:
    _require_agent_access(request)
    task = await agent_runtime().get_task(task_id)
    if task is None or task.owner_id != _owner_id():
        raise HTTPException(status_code=404, detail="task not found")
    try:
        task = await agent_runtime().run_inspection(task_id, request_body.tool, request_body.args)
    except AgentRuntimeError as exc:
        message = str(exc); status_code = 404 if message == "task not found" else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return _task_payload(task)
