from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_projects import agent_project_store
from app.audit import write_audit
from app.codex_capability_control import (
    CodexCapabilityError,
    PLUGIN_MUTATION_BLOCK_REASON,
    assert_plugin_mutation_blocked,
    capability_inventory,
    read_local_plugin,
    set_skill_enabled,
)
from app.codex_dynamic_tool_policy import dynamic_tool_policy
from app.config import SERVER_DIR
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf

router = APIRouter(prefix="/capabilities", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _redirect(project_id: int | None = None) -> RedirectResponse:
    query = urlencode({"project_id": int(project_id)}) if project_id else ""
    return RedirectResponse(
        "/account/agent/capabilities/" + (f"?{query}" if query else ""),
        status_code=303,
    )


@router.get("/", response_class=HTMLResponse, response_model=None)
async def codex_capability_center(
    request: Request,
    project_id: int = 0,
    refresh: bool = False,
    marketplace_path: str = "",
    plugin_name: str = "",
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    projects = agent_project_store().list_projects(owner_id, enabled_only=True)
    selected_project = int(project_id) if int(project_id or 0) > 0 else None
    inventory: dict[str, object] = {
        "cwd": "",
        "skills": [],
        "skill_errors": [],
        "hooks": [],
        "hook_diagnostics": [],
        "marketplaces": [],
        "installed_marketplaces": [],
        "plugin_diagnostics": [],
        "plugin_mutation_allowed": False,
        "plugin_mutation_reason": PLUGIN_MUTATION_BLOCK_REASON,
        "project": None,
    }
    error = ""
    plugin_detail: dict[str, object] | None = None
    try:
        inventory = await capability_inventory(
            owner_id,
            project_id=selected_project,
            force_reload=bool(refresh),
        )
        if marketplace_path.strip() or plugin_name.strip():
            plugin_detail = await read_local_plugin(
                owner_id,
                marketplace_path=marketplace_path,
                plugin_name=plugin_name,
                project_id=selected_project,
            )
    except (CodexCapabilityError, KeyError, ValueError) as exc:
        error = str(exc)
    return templates.TemplateResponse(
        "user_agent_capabilities.html",
        _ctx(
            request,
            user,
            projects=projects,
            selected_project_id=selected_project or 0,
            inventory=inventory,
            plugin_detail=plugin_detail,
            capability_error=error,
            dynamic_tool=dynamic_tool_policy(),
        ),
    )


@router.post("/skills/toggle", response_model=None)
async def codex_skill_toggle(
    request: Request,
    csrf_token: str = Form(...),
    path: str = Form(...),
    enabled: bool = Form(...),
    project_id: int = Form(default=0),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    selected_project = int(project_id) if int(project_id or 0) > 0 else None
    try:
        _verify_csrf(request, csrf_token)
        skill = await set_skill_enabled(
            owner_id,
            path=path,
            enabled=bool(enabled),
            project_id=selected_project,
        )
        state = "启用" if bool(enabled) else "禁用"
        _flash(request, f"已通过官方 skills/config/write {state} Skill：{skill.get('name') or path}", "success")
    except (CodexCapabilityError, KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return _redirect(selected_project)


def _plugin_write_block(
    request: Request,
    *,
    action: str,
    owner_id: str,
    project_id: int | None,
    plugin_ref: str,
) -> None:
    try:
        assert_plugin_mutation_blocked(action)
    except CodexCapabilityError as exc:
        write_audit(
            request,
            "codex_plugin_mutation_blocked",
            success=False,
            action=action,
            owner_id=owner_id,
            project_id=project_id,
            plugin_ref=plugin_ref[:240],
            error=str(exc),
        )
        _flash(request, str(exc), "error")


@router.post("/plugins/install", response_model=None)
async def codex_plugin_install_blocked(
    request: Request,
    csrf_token: str = Form(...),
    marketplace_path: str = Form(...),
    plugin_name: str = Form(...),
    project_id: int = Form(default=0),
) -> Response:
    """Compatibility route that is intentionally fail-closed in Phase 7.32.

    Keeping the endpoint avoids turning stale browser pages into 404s, but there is deliberately
    no call to plugin/install and no mutation Host is created.
    """
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    selected_project = int(project_id) if int(project_id or 0) > 0 else None
    try:
        _verify_csrf(request, csrf_token)
        _plugin_write_block(
            request,
            action="plugin/install",
            owner_id=str(user["id"]),
            project_id=selected_project,
            plugin_ref=f"{marketplace_path}:{plugin_name}",
        )
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return _redirect(selected_project)


@router.post("/plugins/uninstall", response_model=None)
async def codex_plugin_uninstall_blocked(
    request: Request,
    csrf_token: str = Form(...),
    plugin_id: str = Form(...),
    project_id: int = Form(default=0),
) -> Response:
    """Compatibility route that never invokes plugin/uninstall in Phase 7.32."""
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    selected_project = int(project_id) if int(project_id or 0) > 0 else None
    try:
        _verify_csrf(request, csrf_token)
        _plugin_write_block(
            request,
            action="plugin/uninstall",
            owner_id=str(user["id"]),
            project_id=selected_project,
            plugin_ref=plugin_id,
        )
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return _redirect(selected_project)


@router.post("/plugins/mutate", response_model=None)
async def codex_plugin_mutation_gate(
    request: Request,
    csrf_token: str = Form(...),
    action: str = Form(default="marketplace/add"),
    project_id: int = Form(default=0),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    selected_project = int(project_id) if int(project_id or 0) > 0 else None
    try:
        _verify_csrf(request, csrf_token)
        _plugin_write_block(
            request,
            action=action,
            owner_id=str(user["id"]),
            project_id=selected_project,
            plugin_ref="",
        )
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return _redirect(selected_project)
