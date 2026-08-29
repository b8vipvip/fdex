from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.account_operations import account_operation_status
from app.agent_loop import FdexAgentLoop
from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, agent_runtime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.codex_host_guard import compact_codex_thread
from app.codex_host_runtime import create_codex_continuation, queue_codex_steer
from app.codex_host_store import codex_host_store
from app.codex_interaction_store import codex_interaction_store
from app.config import SERVER_DIR
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf

router = APIRouter(prefix="/account/agent", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _owner(request: Request) -> tuple[dict[str, object] | None, str, Response | None]:
    user = _current_user(request)
    if user is None:
        return None, "", _login_redirect(request)
    return user, str(user["id"]), None


def _task(owner_id: str, task_id: str) -> dict[str, object] | None:
    try:
        return agent_task_store().get(owner_id, task_id)
    except ValueError:
        return None


async def _execute(owner_id: str, task_id: str) -> None:
    runtime = agent_runtime()
    try:
        row = await asyncio.to_thread(agent_task_store().get, owner_id, task_id)
        if row is None:
            return
        task = runtime._task_from_record(row)
        async with runtime._lock:
            runtime._tasks[task.id] = task
        with agent_task_store().run_lock(task_id):
            row = await asyncio.to_thread(agent_task_store().get, owner_id, task_id)
            if row is None:
                return
            current = runtime._task_from_record(row)
            if current.status not in {"queued", "running"}:
                return
            async with runtime._lock:
                runtime._tasks[current.id] = current
            await FdexAgentLoop(runtime).run(task_id)
    except TaskRunBusy:
        return
    except Exception as exc:
        try:
            task = await runtime.get_task(task_id)
            if task is not None and task.owner_id == owner_id and task.status in {"queued", "running"}:
                await runtime.fail_task(task_id, str(exc))
        except Exception:
            pass


async def _compact(owner_id: str, task_id: str) -> None:
    try:
        await compact_codex_thread(agent_runtime(), owner_id=owner_id, task_id=task_id)
    except Exception:
        # The durable Codex control row stores the detailed error; the task detail page can
        # display it after refresh without leaking an exception through BackgroundTasks.
        return


@router.get("", response_class=HTMLResponse, response_model=None)
def agent_center(request: Request, status: str = "") -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    clean_status = status.strip().lower()
    if clean_status not in {"", "queued", "running", "succeeded", "failed", "canceled"}:
        clean_status = ""
    projects = agent_project_store().list_projects(owner_id, enabled_only=True)
    tasks = agent_task_store().list(owner_id, status=clean_status, limit=80)
    usage = agent_runtime().execution_sandbox.account_usage(owner_id)
    operation = account_operation_status(owner_id)
    return templates.TemplateResponse(
        "user_agent.html",
        _ctx(
            request,
            user,
            view="center",
            projects=projects,
            tasks=tasks,
            task=None,
            codex_session=None,
            codex_interactions=[],
            status_filter=clean_status,
            usage=usage,
            operation=operation.to_dict(),
        ),
    )


@router.post("/tasks", response_model=None)
async def agent_task_create(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
    project_id: int = Form(...),
    prompt: str = Form(...),
    run_now: bool = Form(default=False),
) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        operation = account_operation_status(owner_id)
        if operation.busy:
            raise ValueError("当前账号正在执行数据导出、长期记忆清理或账号注销，请稍后重试")
        project = agent_project_store().get_project(owner_id, project_id)
        if not bool(project.get("enabled")):
            raise ValueError("Coding Agent 项目未启用")
        task = await agent_runtime().create_task(prompt, owner_id=owner_id, project_id=project_id)
        if run_now:
            background_tasks.add_task(_execute, owner_id, task.id)
            _flash(request, "Coding Agent 任务已创建并开始执行；详情页会自动刷新进度", "success")
        else:
            _flash(request, "Coding Agent 任务已创建，点击“开始执行”后才会操作仓库", "success")
        return RedirectResponse(f"/account/agent/tasks/{task.id}", status_code=303)
    except (KeyError, ValueError, AgentRuntimeError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/agent", status_code=303)


@router.get("/tasks/{task_id}", response_class=HTMLResponse, response_model=None)
def agent_task_detail(task_id: str, request: Request) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    task = _task(owner_id, task_id)
    if task is None:
        _flash(request, "Coding Agent 任务不存在或不属于当前账号", "error")
        return RedirectResponse("/account/agent", status_code=303)
    projects = agent_project_store().list_projects(owner_id, enabled_only=True)
    usage = agent_runtime().execution_sandbox.account_usage(owner_id)
    codex_session = codex_host_store().task_state(owner_id, task_id)
    codex_interactions = codex_interaction_store().list_for_task(owner_id, task_id, limit=100)
    return templates.TemplateResponse(
        "user_agent.html",
        _ctx(
            request,
            user,
            view="task",
            task=task,
            codex_session=codex_session,
            codex_interactions=codex_interactions,
            projects=projects,
            tasks=[],
            status_filter="",
            usage=usage,
            operation=account_operation_status(owner_id).to_dict(),
        ),
    )


@router.post("/tasks/{task_id}/run", response_model=None)
def agent_task_run(task_id: str, request: Request, background_tasks: BackgroundTasks, csrf_token: str = Form(...)) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        task = _task(owner_id, task_id)
        if task is None:
            raise ValueError("Coding Agent 任务不存在")
        if str(task.get("status")) not in {"queued", "running"}:
            raise ValueError(f"任务当前状态为 {task.get('status')}，不能执行")
        background_tasks.add_task(_execute, owner_id, task_id)
        _flash(request, "Coding Agent 已开始执行，详情页会自动刷新", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/cancel", response_model=None)
async def agent_task_cancel(task_id: str, request: Request, csrf_token: str = Form(...)) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        row = await asyncio.to_thread(agent_task_store().get, owner_id, task_id)
        if row is None:
            raise ValueError("Coding Agent 任务不存在")
        runtime = agent_runtime()
        task = runtime._task_from_record(row)
        async with runtime._lock:
            runtime._tasks[task.id] = task
        try:
            with agent_task_store().run_lock(task_id):
                await runtime.request_cancel(owner_id, task_id, force_terminal=True)
        except TaskRunBusy:
            await runtime.request_cancel(owner_id, task_id)
        _flash(request, "已提交取消请求", "success")
    except (ValueError, AgentRuntimeError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/retry", response_model=None)
async def agent_task_retry(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
    run_now: bool = Form(default=True),
) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        source = _task(owner_id, task_id)
        if source is None:
            raise ValueError("Coding Agent 任务不存在")
        task = await agent_runtime().retry_task(owner_id, task_id)
        if run_now:
            background_tasks.add_task(_execute, owner_id, task.id)
        _flash(request, "已创建重试任务" + ("并开始执行" if run_now else ""), "success")
        return RedirectResponse(f"/account/agent/tasks/{task.id}", status_code=303)
    except (ValueError, AgentRuntimeError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/codex/resume", response_model=None)
async def codex_resume_task(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
    prompt: str = Form(...),
) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        child = await create_codex_continuation(
            agent_runtime(),
            owner_id=owner_id,
            source_task_id=task_id,
            prompt=prompt,
            fork=False,
        )
        background_tasks.add_task(_execute, owner_id, child.id)
        _flash(request, "已在同一官方 Codex Thread 上创建续接 Turn 并开始执行", "success")
        return RedirectResponse(f"/account/agent/tasks/{child.id}", status_code=303)
    except (ValueError, AgentRuntimeError, KeyError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/codex/fork", response_model=None)
async def codex_fork_task(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
    prompt: str = Form(...),
) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        child = await create_codex_continuation(
            agent_runtime(),
            owner_id=owner_id,
            source_task_id=task_id,
            prompt=prompt,
            fork=True,
        )
        background_tasks.add_task(_execute, owner_id, child.id)
        _flash(request, "已请求 fork 官方 Codex Thread，并在隔离子任务中开始新 Turn", "success")
        return RedirectResponse(f"/account/agent/tasks/{child.id}", status_code=303)
    except (ValueError, AgentRuntimeError, KeyError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/codex/steer", response_model=None)
async def codex_steer_task(
    task_id: str,
    request: Request,
    csrf_token: str = Form(...),
    text: str = Form(...),
) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        control = await queue_codex_steer(owner_id=owner_id, task_id=task_id, text=text)
        _flash(request, f"Steer 已进入 Codex Host 控制队列（#{control['id']}）", "success")
    except (ValueError, AgentRuntimeError, KeyError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/codex/compact", response_model=None)
async def codex_compact_task(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        session = await asyncio.to_thread(codex_host_store().task_state, owner_id, task_id)
        if session is None:
            raise AgentRuntimeError("task has no persisted Codex thread")
        background_tasks.add_task(_compact, owner_id, task_id)
        _flash(request, "已提交 Codex Thread compact；活动 Turn 会先完成，再串行压缩上下文", "success")
    except (ValueError, AgentRuntimeError, KeyError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.post("/sandbox/cleanup", response_model=None)
async def agent_sandbox_cleanup(request: Request, csrf_token: str = Form(...)) -> Response:
    user, owner_id, redirect = _owner(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        cleanup = await agent_runtime().cleanup_completed_workspaces(owner_id)
        released = cleanup.get("released", 0) if isinstance(cleanup, dict) else 0
        _flash(request, f"已清理完成任务工作区：{released}", "success")
    except (ValueError, AgentRuntimeError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/agent", status_code=303)
