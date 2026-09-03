from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_projects import agent_project_store
from app.audit import write_audit
from app.codex_engine import codex_runtime_status
from app.codex_runtime_admin_routes import router as codex_runtime_admin_router
from app.codex_subagent_admin_routes import router as codex_subagent_admin_router
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import write_env
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf
from app.system_info import schedule_service_restart

router = APIRouter(prefix="/admin/agent", include_in_schema=False)
router.include_router(codex_subagent_admin_router)
router.include_router(codex_runtime_admin_router)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {"request": request, "settings": fresh_settings(), "csrf_token": ensure_csrf_token(request), "flash": pop_flash(request), "current_path": request.url.path, **extra}


def _owner() -> str:
    return fresh_settings().fdex_agent_default_owner.strip() or "local"


@router.get("", response_class=HTMLResponse, response_model=None)
def agent_settings_page(request: Request) -> Response:
    if not is_admin(request): return _login_redirect()
    settings = fresh_settings(); owner_id = _owner(); store = agent_project_store()
    return templates.TemplateResponse(
        "agent_settings.html",
        _ctx(
            request,
            env_path=str(Path(settings.app_dir) / "server" / ".env"),
            token_ready=len(settings.fdex_agent_access_token.strip()) >= 32,
            owner_id=owner_id,
            connections=store.list_connections(owner_id),
            projects=store.list_projects(owner_id),
            codex_status=codex_runtime_status(),
        ),
    )


@router.post("", response_model=None)
def save_agent_settings(
    request: Request,
    csrf_token: str = Form(...),
    fdex_agent_enabled: str | None = Form(None),
) -> Response:
    if not is_admin(request): return _login_redirect()
    verify_csrf(request, csrf_token)
    enabled = fdex_agent_enabled == "true"; settings_before = fresh_settings()
    status = codex_runtime_status()
    write_env({"FDEX_AGENT_ENABLED": "true" if enabled else "false"})
    get_settings.cache_clear()
    write_audit(
        request,
        "save_agent_settings",
        enabled=enabled,
        previous_enabled=settings_before.fdex_agent_enabled,
        engine="codex",
        codex_ready=bool(status.get("ready")),
    )
    try:
        task = schedule_service_restart(fresh_settings()); write_audit(request, "restart_after_agent_settings", task=task)
        if enabled and not bool(status.get("ready")):
            detail = str(status.get("reason") or "Codex 未就绪")
            set_flash(
                request,
                f"Coding Agent 已启用，唯一执行核心为 OpenAI Codex；当前 Codex 尚未就绪：{detail}。任务会 fail-closed，不会回退旧 Agent 或普通 AI。服务将在约 2 秒后自动重启。",
                "error",
            )
        else:
            set_flash(request, f"Coding Agent {'已启用' if enabled else '已关闭'}；执行核心固定为 OpenAI Codex。服务将在约 2 秒后自动重启并应用设置。")
    except (ValueError, RuntimeError) as exc:
        write_audit(request, "restart_after_agent_settings", success=False, error=str(exc)); set_flash(request, f"Coding Agent 设置已保存，但自动重启失败：{exc}。请到“版本与维护”手动重启服务。", "error")
    return RedirectResponse("/admin/agent", status_code=303)


@router.post("/github-oauth", response_model=None)
def save_github_oauth_settings(
    request: Request,
    csrf_token: str = Form(...),
    client_id: str = Form(""),
    scope: str = Form("repo read:user offline_access"),
) -> Response:
    if not is_admin(request): return _login_redirect()
    verify_csrf(request, csrf_token)
    clean_client_id = client_id.strip()
    clean_scope = " ".join(scope.split())
    if clean_client_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", clean_client_id):
        set_flash(request, "GitHub OAuth Client ID 格式无效。", "error")
        return RedirectResponse("/admin/agent#github-oauth", status_code=303)
    if len(clean_scope) > 200 or (clean_scope and not re.fullmatch(r"[A-Za-z0-9:_ -]+", clean_scope)):
        set_flash(request, "GitHub OAuth scope 格式无效。", "error")
        return RedirectResponse("/admin/agent#github-oauth", status_code=303)
    write_env(
        {
            "FDEX_GITHUB_OAUTH_CLIENT_ID": clean_client_id,
            "FDEX_GITHUB_OAUTH_SCOPE": clean_scope,
        }
    )
    get_settings.cache_clear()
    write_audit(request, "save_agent_github_oauth", enabled=bool(clean_client_id), scope=clean_scope)
    set_flash(request, "GitHub Device OAuth 设置已保存；Android 新连接立即使用此配置。")
    return RedirectResponse("/admin/agent#github-oauth", status_code=303)


@router.post("/github", response_model=None)
def save_github_connection(request: Request, csrf_token: str = Form(...), name: str = Form("GitHub"), token: str = Form(...)) -> Response:
    if not is_admin(request): return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        connection = agent_project_store().save_connection(_owner(), name, token)
        write_audit(request, "agent_github_connected", login=connection.get("login"), connection_id=connection.get("id"))
        set_flash(request, f"GitHub 已连接：{connection.get('login')}")
    except Exception as exc:
        write_audit(request, "agent_github_connect_failed", success=False, error=str(exc)); set_flash(request, f"GitHub 连接失败：{exc}", "error")
    return RedirectResponse("/admin/agent#github", status_code=303)


@router.post("/github/{connection_id}/delete", response_model=None)
def delete_github_connection(connection_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request): return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        agent_project_store().delete_connection(_owner(), connection_id); write_audit(request, "agent_github_deleted", connection_id=connection_id); set_flash(request, "GitHub 连接已删除。")
    except Exception as exc:
        set_flash(request, f"无法删除 GitHub 连接：{exc}", "error")
    return RedirectResponse("/admin/agent#github", status_code=303)


@router.post("/projects", response_model=None)
def create_project(request: Request, csrf_token: str = Form(...), name: str = Form(...), repo_full_name: str = Form(...), base_branch: str = Form("main"), connection_id: str = Form(""), allow_push: str | None = Form(None), allow_pr: str | None = Form(None)) -> Response:
    if not is_admin(request): return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        project = agent_project_store().save_project(_owner(), name=name, repo_full_name=repo_full_name, base_branch=base_branch, connection_id=int(connection_id) if connection_id.strip() else None, allow_push=allow_push == "true", allow_pr=allow_pr == "true")
        write_audit(request, "agent_project_created", project_id=project["id"], repository=project["repo_full_name"], allow_push=project["allow_push"], allow_pr=project["allow_pr"])
        set_flash(request, f"Agent 项目已添加：{project['name']}")
    except Exception as exc:
        write_audit(request, "agent_project_create_failed", success=False, error=str(exc)); set_flash(request, f"项目添加失败：{exc}", "error")
    return RedirectResponse("/admin/agent#projects", status_code=303)


@router.post("/projects/{project_id}/delete", response_model=None)
def delete_project(project_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request): return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        agent_project_store().delete_project(_owner(), project_id); write_audit(request, "agent_project_deleted", project_id=project_id); set_flash(request, "Agent 项目已删除。已有沙箱文件不会被网页自动删除。")
    except Exception as exc:
        set_flash(request, f"无法删除项目：{exc}", "error")
    return RedirectResponse("/admin/agent#projects", status_code=303)
