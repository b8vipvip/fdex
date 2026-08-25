from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.account_operations import account_operation_status
from app.agent_access import agent_token_valid
from app.agent_accounts import agent_account_store
from app.agent_loop import FdexAgentLoop
from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, AgentTask, agent_runtime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.central_auth import central_auth_store
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix=f"{settings.api_prefix}/agent", tags=["agent"])


class AgentEnrollRequest(BaseModel):
    label: str = Field(default="FDEX account", max_length=100)


class GitHubConnectionRequest(BaseModel):
    name: str = Field(default="GitHub", max_length=80)
    token: str = Field(min_length=20, max_length=500)
    connection_id: int | None = Field(default=None, ge=1)


class AgentProjectRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    repo_full_name: str = Field(min_length=3, max_length=200)
    base_branch: str = Field(default="main", max_length=180)
    connection_id: int | None = Field(default=None, ge=1)
    allow_push: bool = False
    allow_pr: bool = False
    allow_network: bool = False
    sandbox_memory_mb: int = Field(default=2048, ge=128, le=16384)
    sandbox_cpu_percent: int = Field(default=150, ge=10, le=800)
    enabled: bool = True
    project_id: int | None = Field(default=None, ge=1)


class AgentTaskCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    project_id: int | None = Field(default=None, ge=1)


class AgentToolRunRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


def _require_bootstrap(request: Request) -> None:
    configured = settings.fdex_agent_access_token
    if not configured.strip():
        raise HTTPException(status_code=503, detail="FDEX Agent enrollment token is not configured")
    provided = request.headers.get("X-FDEX-Agent-Token", "")
    if not agent_token_valid(configured, provided):
        raise HTTPException(status_code=401, detail="invalid FDEX Agent enrollment token")


def _account_owner(request: Request) -> tuple[str, str]:
    authorization = request.headers.get("Authorization", "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        user = central_auth_store().authenticate_access(token.strip())
        if user is None:
            raise HTTPException(status_code=401, detail="FDEX login has expired")
        owner_id = str(user["id"])
        operation = account_operation_status(owner_id)
        if operation.busy:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "当前账号正在执行数据导出、长期记忆清理或账号注销，请稍后重试",
                    "operation": operation.operation,
                },
            )
        return owner_id, "central"

    account_token = request.headers.get("X-FDEX-Account-Token", "").strip()
    if account_token:
        account = agent_account_store().authenticate(account_token)
        if account is None:
            raise HTTPException(status_code=401, detail="invalid legacy Agent account token")
        return str(account["owner_id"]), "agent-account-legacy"

    configured = settings.fdex_agent_access_token
    provided = request.headers.get("X-FDEX-Agent-Token", "")
    if configured.strip() and agent_token_valid(configured, provided):
        return settings.fdex_agent_default_owner.strip() or "local", "bootstrap-legacy"
    raise HTTPException(status_code=401, detail="FDEX login is required")


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
        "cancel_requested": task.cancel_requested,
        "parent_task_id": task.parent_task_id,
        "changed_files": sorted(task.changed_files),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "events": [
            {"type": event.type, "message": event.message, "created_at": event.created_at}
            for event in task.events
        ],
    }


def _project_payload(item: dict[str, Any]) -> dict[str, object]:
    return {
        "id": item["id"],
        "name": item["name"],
        "repository": item["repo_full_name"],
        "base_branch": item["base_branch"],
        "connection_id": item.get("connection_id"),
        "allow_push": item["allow_push"],
        "allow_pr": item["allow_pr"],
        "allow_network": item.get("allow_network", False),
        "sandbox_memory_mb": item.get("sandbox_memory_mb", 2048),
        "sandbox_cpu_percent": item.get("sandbox_cpu_percent", 150),
        "enabled": item["enabled"],
    }


@router.post("/account/enroll", deprecated=True)
def enroll_account(request_body: AgentEnrollRequest, request: Request) -> dict[str, object]:
    _require_bootstrap(request)
    account, token = agent_account_store().enroll(request_body.label)
    return {
        "owner_id": account["owner_id"],
        "label": account["label"],
        "account_token": token,
        "ai_source": "shared_provider_pool",
        "deprecated": True,
    }


@router.get("/account")
def current_account(request: Request) -> dict[str, object]:
    owner_id, mode = _account_owner(request)
    return {"owner_id": owner_id, "auth_mode": mode, "ai_source": "shared_provider_pool"}


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, object]:
    owner_id, mode = _account_owner(request)
    return {**agent_runtime().capabilities(), "owner_id": owner_id, "auth_mode": mode}


@router.get("/github/connections")
def github_connections(request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    return {"connections": agent_project_store().list_connections(owner_id)}


@router.post("/github/connections")
def save_github_connection(request_body: GitHubConnectionRequest, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        return agent_project_store().save_connection(
            owner_id,
            request_body.name,
            request_body.token,
            request_body.connection_id,
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/github/connections/{connection_id}")
def delete_github_connection(connection_id: int, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        agent_project_store().delete_connection(owner_id, connection_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/projects")
def projects(request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        items = agent_project_store().list_projects(owner_id, enabled_only=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "owner_id": owner_id,
        "ai_source": "shared_provider_pool",
        "projects": [_project_payload(item) for item in items],
    }


@router.post("/projects")
def save_project(request_body: AgentProjectRequest, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        project = agent_project_store().save_project(
            owner_id,
            name=request_body.name,
            repo_full_name=request_body.repo_full_name,
            base_branch=request_body.base_branch,
            connection_id=request_body.connection_id,
            allow_push=request_body.allow_push,
            allow_pr=request_body.allow_pr,
            allow_network=request_body.allow_network,
            sandbox_memory_mb=request_body.sandbox_memory_mb,
            sandbox_cpu_percent=request_body.sandbox_cpu_percent,
            enabled=request_body.enabled,
            project_id=request_body.project_id,
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_payload(project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        agent_project_store().delete_project(owner_id, project_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/tasks")
async def list_tasks(request: Request, status: str = "", limit: int = 50) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    clean_status = (status or "").strip().lower()
    if clean_status and clean_status not in {"queued", "running", "succeeded", "failed", "canceled"}:
        raise HTTPException(status_code=400, detail="invalid Agent task status")
    tasks = await agent_runtime().list_tasks(owner_id, status=clean_status, limit=max(1, min(limit, 100)))
    return {"tasks": [_task_payload(task) for task in tasks]}


@router.post("/tasks")
async def create_task(request_body: AgentTaskCreateRequest, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        task = await agent_runtime().create_task(
            request_body.prompt,
            owner_id=owner_id,
            project_id=request_body.project_id,
        )
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _task_payload(task)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    task = await agent_runtime().get_task(task_id)
    if task is None or task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_payload(task)


@router.post("/tasks/{task_id}/run")
async def run_agent(task_id: str, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    runtime = agent_runtime()
    task = await runtime.get_task(task_id)
    if task is None or task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        with agent_task_store().run_lock(task_id):
            await FdexAgentLoop(runtime).run(task_id)
    except TaskRunBusy as exc:
        raise HTTPException(status_code=409, detail="该 Coding Agent 任务已在其它 Worker 中执行") from exc
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task = await runtime.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_payload(task)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        task = await agent_runtime().request_cancel(owner_id, task_id)
    except AgentRuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _task_payload(task)


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    try:
        task = await agent_runtime().retry_task(owner_id, task_id)
    except AgentRuntimeError as exc:
        message = str(exc)
        raise HTTPException(status_code=404 if message == "task not found" else 400, detail=message) from exc
    return _task_payload(task)


@router.post("/tasks/{task_id}/tools/run")
async def run_tool(task_id: str, request_body: AgentToolRunRequest, request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    task = await agent_runtime().get_task(task_id)
    if task is None or task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        task = await agent_runtime().run_inspection(task_id, request_body.tool, request_body.args)
    except AgentRuntimeError as exc:
        message = str(exc)
        raise HTTPException(status_code=404 if message == "task not found" else 400, detail=message) from exc
    return _task_payload(task)


@router.get("/sandbox/usage")
def sandbox_usage(request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    return agent_runtime().execution_sandbox.account_usage(owner_id)


@router.post("/sandbox/cleanup")
async def sandbox_cleanup(request: Request) -> dict[str, object]:
    owner_id, _ = _account_owner(request)
    runtime = agent_runtime()
    before = runtime.execution_sandbox.account_usage(owner_id)
    cleanup = await runtime.cleanup_completed_workspaces(owner_id)
    after = runtime.execution_sandbox.account_usage(owner_id)
    return {"ok": True, "before": before, "after": after, "cleanup": cleanup}
