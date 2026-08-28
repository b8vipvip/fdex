from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.agent_identity_runtime import next_agent_name
from app.user_portal_routes import _current_user, _flash, _login_redirect, _verify_csrf
from app.web_workspace import web_workspace_store

router = APIRouter(prefix="/account", include_in_schema=False)


def _owner(user: dict[str, object]) -> str:
    return str(user["id"])


@router.post("/employees", response_model=None)
def create_agent(
    request: Request,
    csrf_token: str = Form(...),
    role_prompt: str = Form(""),
    name: str = Form(""),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        display_name = (name or "").strip()[:80] or next_agent_name(store, owner_id)
        store.create(
            owner_id,
            "employee",
            {
                "name": display_name,
                "role_prompt": (role_prompt or "").strip()[:12000],
                "active": True,
                "knowledge_read": True,
                "knowledge_write": True,
                "coding_agent": False,
            },
            sort_key=display_name.casefold(),
        )
        _flash(request, "智体已创建", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/employees", status_code=303)


@router.post("/employees/{employee_id}", response_model=None)
def update_agent(
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(""),
    role_prompt: str = Form(""),
    active: bool = Form(False),
    knowledge_read: bool = Form(False),
    knowledge_write: bool = Form(False),
    coding_agent: bool = Form(False),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        current = store.get(owner_id, "employee", employee_id, include_deleted=True)
        current.pop("_parent_id", None)
        deleted = bool(current.pop("_deleted", False))
        for legacy in ("department", "position", "industry"):
            current.pop(legacy, None)
        display_name = (name or "").strip()[:80] or str(current.get("name") or "").strip()[:80] or f"智体 {employee_id}"
        current.update(
            {
                "name": display_name,
                "role_prompt": (role_prompt or "").strip()[:12000],
                "active": active,
                "knowledge_read": knowledge_read,
                "knowledge_write": knowledge_write,
                "coding_agent": coding_agent,
            }
        )
        store.upsert(
            owner_id,
            "employee",
            employee_id,
            current,
            sort_key=display_name.casefold(),
            deleted=deleted,
        )
        _flash(request, "智体设置已保存", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/employees", status_code=303)
