from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import SERVER_DIR
from app.plugin_runtime import (
    catalog_snapshot,
    effective_agent_grant,
    plugin_audit_rows,
    plugin_connection_status,
    plugin_definition,
    save_agent_grant,
)
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf
from app.web_workspace import web_workspace_store

router = APIRouter(prefix="/account/plugins", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _owner(user: dict[str, object]) -> str:
    return str(user["id"])


@router.get("", response_class=HTMLResponse, response_model=None)
def plugin_center(request: Request, category: str = "all") -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    store.ensure_defaults(owner_id)
    employees = [item for item in store.list(owner_id, "employee", limit=500) if not bool(item.get("_deleted"))]
    catalog = catalog_snapshot(owner_id)
    categories = ["代码仓库", "沟通与知识", "项目管理", "部署与运维", "可观测性"]
    clean_category = category if category in categories else "all"
    visible = catalog if clean_category == "all" else [item for item in catalog if item["category"] == clean_category]
    grants: dict[str, dict[str, str]] = {}
    for employee in employees:
        grants[str(employee["id"])] = {
            item["id"]: str(effective_agent_grant(owner_id, employee, item["id"])["mode"])
            for item in catalog
        }
    return templates.TemplateResponse(
        "user_plugins.html",
        _ctx(
            request,
            user,
            page="plugins",
            plugins=visible,
            plugin_catalog=catalog,
            employees=employees,
            grants=grants,
            categories=categories,
            selected_category=clean_category,
            audit_rows=plugin_audit_rows(owner_id, 60),
            preferences=store.preferences(owner_id),
        ),
    )


@router.get("/catalog.json", response_model=None)
def plugin_catalog_json(request: Request) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    return JSONResponse({"ok": True, "plugins": catalog_snapshot(_owner(user))})


@router.post("/{plugin_id}/agents/{employee_id}", response_model=None)
def save_plugin_agent_grant(
    plugin_id: str,
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    mode: str = Form("none"),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        definition = plugin_definition(plugin_id)
        status = plugin_connection_status(_owner(user), definition.id)
        if not bool(status.get("connected")):
            raise ValueError(f"{definition.name} 尚未连接，不能授权给智体")
        save_agent_grant(_owner(user), employee_id, definition.id, mode)
        label = {"none": "不授权", "read": "只读", "write": "读写"}.get(mode, mode)
        _flash(request, f"{definition.name} 智体权限已更新为：{label}", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/plugins", status_code=303)
