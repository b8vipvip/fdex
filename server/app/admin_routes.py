from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.ai_service import test_ai_connection
from app.audit import read_audit, write_audit
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import mask_secret, read_env, write_env
from app.github_service import github_status
from app.security import (
    clear_login_failures,
    ensure_csrf_token,
    is_admin,
    login_is_limited,
    login_session,
    logout_session,
    pop_flash,
    record_login_failure,
    set_flash,
    verify_admin_credentials,
    verify_csrf,
)
from app.system_info import (
    git_info,
    schedule_server_update,
    schedule_service_restart,
    service_logs,
    system_snapshot,
)

router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))
_USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _guard(request: Request) -> RedirectResponse | None:
    return None if is_admin(request) else _login_redirect()


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


def _url_ok(value: str, empty: bool = False) -> bool:
    if not value and empty:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request) -> Response:
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        _ctx(request, admin_ready=fresh_settings().admin_ready, error=""),
    )


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
) -> Response:
    verify_csrf(request, csrf_token)
    if login_is_limited(request):
        write_audit(request, "login_rate_limited", success=False)
        return templates.TemplateResponse(
            "login.html",
            _ctx(request, admin_ready=fresh_settings().admin_ready, error="登录失败次数过多，请 10 分钟后再试。"),
            status_code=429,
        )
    if not verify_admin_credentials(username, password):
        record_login_failure(request)
        write_audit(request, "login", success=False, username=username[:64])
        return templates.TemplateResponse(
            "login.html",
            _ctx(request, admin_ready=fresh_settings().admin_ready, error="用户名或密码错误。"),
            status_code=401,
        )
    clear_login_failures(request)
    login_session(request, fresh_settings().admin_username)
    write_audit(request, "login")
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout", response_model=None)
def logout(request: Request, csrf_token: str = Form(...)) -> Response:
    verify_csrf(request, csrf_token)
    write_audit(request, "logout")
    logout_session(request)
    return RedirectResponse("/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse, response_model=None)
async def dashboard(request: Request) -> Response:
    if redirect := _guard(request):
        return redirect
    settings = fresh_settings()
    system, github = await asyncio.gather(
        asyncio.to_thread(system_snapshot, settings),
        github_status(settings),
    )
    local_sha = str(system.get("git", {}).get("sha", ""))
    remote_sha = str(github.get("remote_commit", {}).get("sha", ""))
    update_available = bool(local_sha and remote_sha and local_sha != "未知" and local_sha != remote_sha)
    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(
            request,
            system=system,
            github=github,
            update_available=update_available,
            masked_api_key=mask_secret(settings.ai_api_key),
        ),
    )


@router.get("/settings", response_class=HTMLResponse, response_model=None)
def settings_page(request: Request) -> Response:
    if redirect := _guard(request):
        return redirect
    settings = fresh_settings()
    return templates.TemplateResponse(
        "settings.html",
        _ctx(
            request,
            masked_api_key=mask_secret(settings.ai_api_key),
            env_path=str(Path(settings.app_dir) / "server" / ".env"),
        ),
    )


@router.post("/settings", response_model=None)
def save_settings(
    request: Request,
    csrf_token: str = Form(...),
    app_name: str = Form(...),
    app_version: str = Form(...),
    environment: str = Form(...),
    public_base_url: str = Form(...),
    api_prefix: str = Form(...),
    cors_origins: str = Form(...),
    fdex_host: str = Form(...),
    fdex_port: str = Form(...),
    fdex_workers: str = Form(...),
    ai_provider: str = Form(...),
    ai_base_url: str = Form(""),
    ai_api_key: str = Form(""),
    ai_model: str = Form(""),
    ai_timeout_seconds: str = Form("60"),
    admin_username: str = Form(...),
    admin_cookie_secure: str | None = Form(None),
    admin_session_hours: str = Form("12"),
    clear_ai_api_key: str | None = Form(None),
) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)

    values = {
        "app_name": app_name.strip(),
        "app_version": app_version.strip(),
        "environment": environment.strip().lower(),
        "public_base_url": public_base_url.strip().rstrip("/"),
        "api_prefix": "/" + api_prefix.strip().strip("/"),
        "cors_origins": cors_origins.strip(),
        "fdex_host": fdex_host.strip(),
        "ai_provider": ai_provider.strip(),
        "ai_base_url": ai_base_url.strip().rstrip("/"),
        "ai_model": ai_model.strip(),
        "admin_username": admin_username.strip(),
    }
    errors: list[str] = []
    if not values["app_name"] or len(values["app_name"]) > 100:
        errors.append("服务名称不能为空且不能超过 100 个字符")
    if not _VERSION.fullmatch(values["app_version"]):
        errors.append("服务版本格式无效")
    if values["environment"] not in {"production", "staging", "development", "test"}:
        errors.append("运行环境无效")
    if not _url_ok(values["public_base_url"]):
        errors.append("公开服务地址格式无效")
    if values["api_prefix"] == "/":
        errors.append("API 前缀不能是根目录")
    if values["fdex_host"] not in {"127.0.0.1", "localhost", "::1"}:
        errors.append("监听地址只能使用本机回环地址")
    if values["ai_base_url"] and not _url_ok(values["ai_base_url"], empty=True):
        errors.append("AI 接口地址格式无效")
    if not _USERNAME.fullmatch(values["admin_username"]):
        errors.append("管理员用户名格式无效")
    try:
        port, workers = int(fdex_port), int(fdex_workers)
        timeout, session_hours = float(ai_timeout_seconds), int(admin_session_hours)
        assert 1 <= port <= 65535 and 1 <= workers <= 16 and 5 <= timeout <= 600 and 1 <= session_hours <= 168
    except (ValueError, AssertionError):
        errors.append("端口、进程数、超时或会话时长超出范围")
        port, workers, timeout, session_hours = 18080, 2, 60.0, 12

    if errors:
        write_audit(request, "save_settings", success=False, errors=errors)
        set_flash(request, "；".join(errors), "error")
        return RedirectResponse("/admin/settings", status_code=303)

    current = read_env()
    updates = {
        "APP_NAME": values["app_name"],
        "APP_VERSION": values["app_version"],
        "ENVIRONMENT": values["environment"],
        "PUBLIC_BASE_URL": values["public_base_url"],
        "API_PREFIX": values["api_prefix"],
        "CORS_ORIGINS": values["cors_origins"],
        "FDEX_HOST": values["fdex_host"],
        "FDEX_PORT": str(port),
        "FDEX_WORKERS": str(workers),
        "AI_PROVIDER": values["ai_provider"],
        "AI_BASE_URL": values["ai_base_url"],
        "AI_MODEL": values["ai_model"],
        "AI_TIMEOUT_SECONDS": str(timeout),
        "ADMIN_USERNAME": values["admin_username"],
        "ADMIN_COOKIE_SECURE": "true" if admin_cookie_secure else "false",
        "ADMIN_SESSION_HOURS": str(session_hours),
    }
    key_changed = False
    if clear_ai_api_key:
        updates["AI_API_KEY"] = ""
        key_changed = bool(current.get("AI_API_KEY"))
    elif ai_api_key.strip():
        updates["AI_API_KEY"] = ai_api_key.strip()
        key_changed = True

    backup = write_env(updates)
    get_settings.cache_clear()
    changed = [key for key, value in updates.items() if current.get(key, "") != value]
    write_audit(
        request,
        "save_settings",
        changed_keys=[key for key in changed if key != "AI_API_KEY"],
        ai_api_key_changed=key_changed,
        backup=str(backup) if backup else "",
    )
    set_flash(request, "配置已安全保存并完成备份。涉及端口、会话或 API 路由的修改需要重启服务。")
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/test-ai", response_model=None)
async def test_ai(request: Request, csrf_token: str = Form(...)) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)
    result = await test_ai_connection(fresh_settings())
    write_audit(request, "test_ai", success=bool(result["ok"]), status=result.get("status"), latency_ms=result.get("latency_ms"))
    set_flash(request, f"{result['message']}（耗时 {result['latency_ms']} ms）", "success" if result["ok"] else "error")
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/password", response_model=None)
def change_password(
    request: Request,
    csrf_token: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)
    settings = fresh_settings()
    if not verify_admin_credentials(settings.admin_username, current_password):
        write_audit(request, "change_password", success=False, reason="wrong_current_password")
        set_flash(request, "当前密码不正确。", "error")
    elif len(new_password) < 12:
        set_flash(request, "新密码至少需要 12 个字符。", "error")
    elif new_password != confirm_password:
        set_flash(request, "两次输入的新密码不一致。", "error")
    elif new_password == current_password:
        set_flash(request, "新密码不能与当前密码相同。", "error")
    else:
        write_env({"ADMIN_PASSWORD": new_password})
        get_settings.cache_clear()
        write_audit(request, "change_password")
        logout_session(request)
        set_flash(request, "管理员密码已修改，请重新登录。")
        return RedirectResponse("/admin/login", status_code=303)
    return RedirectResponse("/admin/settings#security", status_code=303)


@router.get("/logs", response_class=HTMLResponse, response_model=None)
async def logs_page(request: Request, lines: int | None = None) -> Response:
    if redirect := _guard(request):
        return redirect
    settings = fresh_settings()
    logs, audit = await asyncio.gather(
        asyncio.to_thread(service_logs, settings, lines),
        asyncio.to_thread(read_audit, 100),
    )
    return templates.TemplateResponse(
        "logs.html",
        _ctx(request, logs=logs, audit=audit, requested_lines=lines or settings.admin_log_lines),
    )


@router.get("/maintenance", response_class=HTMLResponse, response_model=None)
async def maintenance_page(request: Request, refresh: int = 0) -> Response:
    if redirect := _guard(request):
        return redirect
    settings = fresh_settings()
    github, local_git = await asyncio.gather(
        github_status(settings, force=bool(refresh)),
        asyncio.to_thread(git_info, settings),
    )
    local_sha, remote_sha = str(local_git.get("sha", "")), str(github.get("remote_commit", {}).get("sha", ""))
    update_available = bool(local_sha and remote_sha and local_sha != "未知" and local_sha != remote_sha)
    return templates.TemplateResponse(
        "maintenance.html",
        _ctx(request, github=github, local_git=local_git, update_available=update_available),
    )


@router.post("/restart", response_model=None)
def restart_service(request: Request, csrf_token: str = Form(...), confirm: str = Form("")) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)
    if confirm != "restart":
        set_flash(request, "请勾选确认后再重启服务。", "error")
    else:
        try:
            task = schedule_service_restart(fresh_settings())
            write_audit(request, "restart_service", task=task)
            set_flash(request, "服务将在约 2 秒后重启，页面短暂断开属于正常现象。")
        except (ValueError, RuntimeError) as exc:
            write_audit(request, "restart_service", success=False, error=str(exc))
            set_flash(request, f"无法安排重启：{exc}", "error")
    return RedirectResponse("/admin/maintenance", status_code=303)


@router.post("/update", response_model=None)
def update_server(request: Request, csrf_token: str = Form(...), confirm: str = Form("")) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)
    settings = fresh_settings()
    if confirm != "update":
        set_flash(request, "请勾选确认后再执行服务端更新。", "error")
    elif git_info(settings).get("dirty"):
        write_audit(request, "update_server", success=False, reason="dirty_worktree")
        set_flash(request, "仓库存在未提交修改，为避免数据丢失，已阻止网页更新。", "error")
    else:
        try:
            task = schedule_server_update(settings)
            write_audit(request, "update_server", task=task)
            set_flash(request, "更新任务已在后台启动，服务会自动拉取 main、安装依赖并重启。")
        except (FileNotFoundError, RuntimeError) as exc:
            write_audit(request, "update_server", success=False, error=str(exc))
            set_flash(request, f"无法启动更新：{exc}", "error")
    return RedirectResponse("/admin/maintenance", status_code=303)
