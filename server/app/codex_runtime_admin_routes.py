from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.codex_engine import codex_runtime_status
from app.codex_runtime_manager import (
    CodexRuntimeManagerError,
    fetch_release,
    rollback_runtime,
    runtime_manager_status,
    upgrade_runtime,
)
from app.config import SERVER_DIR, fresh_settings
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf
from app.system_info import schedule_service_restart

router = APIRouter(prefix="/runtime", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


def _restart_after_switch(request: Request, action: str) -> None:
    try:
        task = schedule_service_restart(fresh_settings())
        write_audit(request, "restart_after_codex_runtime_switch", action=action, task=task)
    except (ValueError, RuntimeError) as exc:
        write_audit(
            request,
            "restart_after_codex_runtime_switch",
            success=False,
            action=action,
            error=str(exc),
        )
        raise CodexRuntimeManagerError(
            f"Runtime 已切换，但自动重启失败：{exc}；请立即在“版本与维护”重启 FDEX"
        ) from exc


@router.get("", response_class=HTMLResponse, response_model=None)
def runtime_page(request: Request) -> Response:
    if not is_admin(request):
        return _login_redirect()
    return templates.TemplateResponse(
        "agent_runtime_manager.html",
        _ctx(
            request,
            manager=runtime_manager_status(),
            codex_status=codex_runtime_status(),
        ),
    )


@router.post("/check", response_model=None)
def check_latest_runtime(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        release = fetch_release()
        write_audit(
            request,
            "codex_runtime_check_latest",
            tag=release["tag"],
            version=release["version"],
            asset_id=release["asset_id"],
            asset_sha256=release["asset_sha256"],
        )
        set_flash(
            request,
            f"OpenAI Codex 最新稳定版：{release['tag']}；官方资产 SHA-256：{release['asset_sha256'][:16]}…。仅检查，没有下载或切换。",
        )
    except CodexRuntimeManagerError as exc:
        write_audit(request, "codex_runtime_check_latest", success=False, error=str(exc))
        set_flash(request, f"检查官方 Codex Runtime 失败：{exc}", "error")
    return RedirectResponse("/admin/agent/runtime", status_code=303)


@router.post("/upgrade", response_model=None)
def upgrade_managed_runtime(
    request: Request,
    csrf_token: str = Form(...),
    tag: str = Form(""),
) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    requested = tag.strip() or None
    try:
        before = runtime_manager_status()
        after = upgrade_runtime(requested)
        write_audit(
            request,
            "codex_runtime_upgrade",
            requested_tag=requested or "latest",
            previous_pin=before.get("active_pin") or "fallback",
            active_pin=after.get("active_pin") or "fallback",
            active_version=after.get("active_version") or "",
        )
        _restart_after_switch(request, "upgrade")
        set_flash(
            request,
            f"Codex Runtime 已完成官方资产校验并切换到 {after.get('active_version') or requested or '最新稳定版'}；旧 Codex 进程树已清理，FDEX 将自动重启。",
        )
    except CodexRuntimeManagerError as exc:
        write_audit(request, "codex_runtime_upgrade", success=False, requested_tag=requested or "latest", error=str(exc))
        set_flash(request, f"Codex Runtime 升级未完成：{exc}", "error")
    return RedirectResponse("/admin/agent/runtime", status_code=303)


@router.post("/rollback", response_model=None)
def rollback_managed_runtime(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        before = runtime_manager_status()
        after = rollback_runtime()
        write_audit(
            request,
            "codex_runtime_rollback",
            previous_pin=before.get("active_pin") or "fallback",
            active_pin=after.get("active_pin") or "fallback",
            active_version=after.get("active_version") or "fallback",
        )
        _restart_after_switch(request, "rollback")
        set_flash(
            request,
            f"Codex Runtime 已回滚到 {after.get('active_version') or 'bundled/system fallback'}；FDEX 将自动重启。",
        )
    except CodexRuntimeManagerError as exc:
        write_audit(request, "codex_runtime_rollback", success=False, error=str(exc))
        set_flash(request, f"Codex Runtime 回滚未完成：{exc}", "error")
    return RedirectResponse("/admin/agent/runtime", status_code=303)
