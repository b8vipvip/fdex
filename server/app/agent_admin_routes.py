from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import write_env
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf
from app.system_info import schedule_service_restart

router = APIRouter(prefix="/admin/agent", include_in_schema=False)
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


@router.get("", response_class=HTMLResponse, response_model=None)
def agent_settings_page(request: Request) -> Response:
    if not is_admin(request):
        return _login_redirect()
    settings = fresh_settings()
    return templates.TemplateResponse(
        "agent_settings.html",
        _ctx(
            request,
            env_path=str(Path(settings.app_dir) / "server" / ".env"),
            token_ready=len(settings.fdex_agent_access_token.strip()) >= 32,
        ),
    )


@router.post("", response_model=None)
def save_agent_settings(
    request: Request,
    csrf_token: str = Form(...),
    fdex_agent_enabled: str | None = Form(None),
) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)

    enabled = fdex_agent_enabled == "true"
    settings_before = fresh_settings()
    write_env({"FDEX_AGENT_ENABLED": "true" if enabled else "false"})
    get_settings.cache_clear()
    write_audit(
        request,
        "save_agent_settings",
        enabled=enabled,
        previous_enabled=settings_before.fdex_agent_enabled,
    )

    try:
        task = schedule_service_restart(fresh_settings())
        write_audit(request, "restart_after_agent_settings", task=task)
        state = "已启用" if enabled else "已关闭"
        set_flash(request, f"Coding Agent {state}，服务将在约 2 秒后自动重启并应用设置。")
    except (ValueError, RuntimeError) as exc:
        write_audit(request, "restart_after_agent_settings", success=False, error=str(exc))
        set_flash(
            request,
            f"Coding Agent 设置已保存，但自动重启失败：{exc}。请到“版本与维护”手动重启服务。",
            "error",
        )
    return RedirectResponse("/admin/agent", status_code=303)
