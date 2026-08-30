from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_tasks import agent_task_store
from app.codex_engine import _codex_home
from app.codex_task_inputs import codex_task_input_store
from app.config import SERVER_DIR
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf

router = APIRouter(prefix="/account/agent/tasks", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _owned_task(owner_id: str, task_id: str) -> dict[str, object]:
    try:
        task = agent_task_store().get(owner_id, task_id)
    except ValueError as exc:
        raise KeyError("Coding Agent 任务不存在") from exc
    if task is None:
        raise KeyError("Coding Agent 任务不存在或不属于当前账号")
    return task


def _require_editable(task: dict[str, object]) -> None:
    if str(task.get("status") or "") != "queued":
        raise ValueError("只有尚未开始执行的 queued 任务可以修改附件 / Skill / Mention")


def _installed_skills(owner_id: str) -> list[str]:
    root = (_codex_home(owner_id) / "skills").resolve()
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in root.iterdir():
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if root not in resolved.parents or not resolved.is_dir():
            continue
        if (resolved / "SKILL.md").is_file():
            names.append(child.name)
    return sorted(set(names), key=str.casefold)[:200]


@router.get("/{task_id}/inputs", response_class=HTMLResponse, response_model=None)
def codex_task_inputs_page(task_id: str, request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        task = _owned_task(owner_id, task_id)
        rows = codex_task_input_store().list(owner_id, task_id)
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/agent", status_code=303)
    return templates.TemplateResponse(
        "user_agent_inputs.html",
        _ctx(
            request,
            user,
            task=task,
            inputs=rows,
            skills=_installed_skills(owner_id),
            editable=str(task.get("status") or "") == "queued",
        ),
    )


@router.post("/{task_id}/inputs/media", response_model=None)
async def codex_task_input_media(
    task_id: str,
    request: Request,
    csrf_token: str = Form(...),
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        task = _owned_task(owner_id, task_id)
        _require_editable(task)
        clean_kind = kind.strip()
        max_bytes = 20 * 1024 * 1024 if clean_kind == "localImage" else 50 * 1024 * 1024
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("附件超过允许大小")
        row = codex_task_input_store().add_media(
            owner_id,
            task_id,
            kind=clean_kind,
            filename=file.filename or "attachment",
            mime=file.content_type or "",
            data=data,
        )
        _flash(request, f"已添加 {row['kind']}：{row['name']}", "success")
    except (KeyError, ValueError, OSError) as exc:
        _flash(request, f"Codex 附件保存失败：{exc}", "error")
    finally:
        await file.close()
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)


@router.post("/{task_id}/inputs/mention", response_model=None)
def codex_task_input_mention(
    task_id: str,
    request: Request,
    csrf_token: str = Form(...),
    path: str = Form(...),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        task = _owned_task(owner_id, task_id)
        _require_editable(task)
        row = codex_task_input_store().add_mention(owner_id, task_id, path)
        _flash(request, f"已添加仓库 Mention：{row['ref']}", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)


@router.post("/{task_id}/inputs/skill", response_model=None)
def codex_task_input_skill(
    task_id: str,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        task = _owned_task(owner_id, task_id)
        _require_editable(task)
        clean = name.strip()
        if clean not in _installed_skills(owner_id):
            raise ValueError("该 Skill 未安装在当前账号的 CODEX_HOME/skills")
        row = codex_task_input_store().add_skill(owner_id, task_id, clean)
        _flash(request, f"已选择 Skill：{row['name']}", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)


@router.post("/{task_id}/inputs/{item_id}/delete", response_model=None)
def codex_task_input_delete(
    task_id: str,
    item_id: str,
    request: Request,
    csrf_token: str = Form(...),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        task = _owned_task(owner_id, task_id)
        _require_editable(task)
        if not codex_task_input_store().delete(owner_id, task_id, item_id):
            raise KeyError("Codex 输入不存在")
        _flash(request, "已删除该 Codex 输入", "success")
    except (KeyError, ValueError, OSError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/agent/tasks/{task_id}/inputs", status_code=303)
