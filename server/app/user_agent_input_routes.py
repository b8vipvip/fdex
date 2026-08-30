from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_tasks import agent_task_store
from app.codex_task_inputs import codex_task_input_store
from app.config import SERVER_DIR
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf

router = APIRouter(prefix="/account/agent/tasks", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _scope(request: Request, task_id: str) -> tuple[dict[str, object] | None, str, Response | None]:
    user = _current_user(request)
    if user is None:
        return None, "", _login_redirect(request)
    owner_id = str(user["id"])
    try:
        task = agent_task_store().get(owner_id, task_id)
    except ValueError:
        task = None
    if task is None:
        _flash(request, "Coding Agent 任务不存在或不属于当前账号", "error")
        return user, owner_id, RedirectResponse("/account/agent", status_code=303)
    return user, owner_id, None


def _queued(task: dict[str, object]) -> None:
    if str(task.get("status") or "") != "queued":
        raise ValueError("富输入只能在任务开始执行前添加或删除；运行中的 Turn 输入不可被静默修改")


@router.get("/{task_id}/inputs", response_class=HTMLResponse, response_model=None)
def task_inputs_page(task_id: str, request: Request) -> Response:
    user, owner_id, error = _scope(request, task_id)
    if error is not None:
        return error
    assert user is not None
    task = agent_task_store().get(owner_id, task_id)
    assert task is not None
    return templates.TemplateResponse(
        "user_agent_inputs.html",
        _ctx(
            request,
            user,
            task=task,
            inputs=codex_task_input_store().list(owner_id, task_id),
            editable=str(task.get("status") or "") == "queued",
        ),
    )


@router.post("/{task_id}/inputs/upload", response_model=None)
async def task_input_upload(
    task_id: str,
    request: Request,
    csrf_token: str = Form(...),
    kind: str = Form(...),
    asset: UploadFile = File(...),
) -> Response:
    user, owner_id, error = _scope(request, task_id)
    if error is not None:
        return error
    try:
        _verify_csrf(request, csrf_token)
        task = agent_task_store().get(owner_id, task_id)
        assert task is not None
        _queued(task)
        clean_kind = str(kind or "").strip()
        max_bytes = 20 * 1024 * 1024 if clean_kind == "image" else 50 * 1024 * 1024 if clean_kind == "audio" else 0
        if not max_bytes:
            raise ValueError("上传类型必须是 image 或 audio")
        data = await asset.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"{clean_kind} 文件超过 {max_bytes // (1024 * 1024)} MiB")
        row = codex_task_input_store().add_binary(
            owner_id,
            task_id,
            kind=clean_kind,
            filename=asset.filename or "asset",
            content_type=asset.content_type or "",
            data=data,
        )
        _flash(request, f"已添加 {clean_kind} 富输入：{row['display_name']}", "success")
    except (AssertionError, ValueError) as exc:
        _flash(request, str(exc), "error")
    finally:
        await asset.close()
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)


@router.post("/{task_id}/inputs/mention", response_model=None)
def task_input_mention(
    task_id: str,
    request: Request,
    csrf_token: str = Form(...),
    relative_path: str = Form(...),
) -> Response:
    user, owner_id, error = _scope(request, task_id)
    if error is not None:
        return error
    try:
        _verify_csrf(request, csrf_token)
        task = agent_task_store().get(owner_id, task_id)
        assert task is not None
        _queued(task)
        row = codex_task_input_store().add_mention(owner_id, task_id, relative_path)
        _flash(request, f"已添加仓库 Mention：{row['value']}", "success")
    except (AssertionError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)


@router.post("/{task_id}/inputs/{input_id}/delete", response_model=None)
def task_input_delete(
    task_id: str,
    input_id: str,
    request: Request,
    csrf_token: str = Form(...),
) -> Response:
    user, owner_id, error = _scope(request, task_id)
    if error is not None:
        return error
    try:
        _verify_csrf(request, csrf_token)
        task = agent_task_store().get(owner_id, task_id)
        assert task is not None
        _queued(task)
        if not codex_task_input_store().remove(owner_id, task_id, input_id):
            raise ValueError("富输入不存在或不属于当前任务")
        _flash(request, "富输入已删除", "success")
    except (AssertionError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)