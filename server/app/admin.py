from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")


def _redirect_login() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _require_admin(request: Request) -> RedirectResponse | None:
    return None if is_admin(request) else _redirect_login()


def _context(request: Request, **values: object) -> dict[str, object]:
    settings = fresh_settings()
    return {
        "request": request,
        "settings": settings,
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **values,
    }


def _valid_url(value: str, *, allow_empty: bool = False) -> bool:
    if not value and allow_empty:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse | RedirectResponse:
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        _context(request, admin_ready=fresh_settings().admin_ready, error=""),
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    verify_csrf(request, csrf_token)
    if login_is_limited(request):
        write_audit(request, "login_rate_limited", success=False)
        return templates.TemplateResponse(
            "login.html",
            _context(
                request,
                admin_ready=fresh_settings().admin_ready,
                error="登录失败次数过多，请 10 分钟后再试。",
            ),
            status_code=429,
        )
    if not verify_admin_credentials(username, password):
        record_login_failure(request)
        write_audit(request, "login", success=False, username=username[:64])
        return templates.TemplateResponse(
            "login.html",
            _context(
                request,
                admin_ready=fresh_settings().admin_ready,
                error="用户名或密码错误。",
            ),
            status_code=401,
        )
    clear_login_failures(request)
    login_session(request, fresh_settings().admin_username)
    write_audit(request, "login", success=True)
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    write_audit(request, "logout")
    logout_session(request)
    return RedirectResponse("/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse | RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
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
        _context(
            request,
            system=system,
            github=github,
            update_available=update_available,
            masked_api_key=mask_secret(settings.ai_api_key),
        ),
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse | RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    settings = fresh_settings()
    return templates.TemplateResponse(
        "settings.html",
        _context(
            request,
            masked_api_key=mask_secret(settings.ai_api_key),
            env_path=str(Path(settings.app_dir) / "server" / ".env"),
        ),
    )


@router.post("/settings")
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
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf_token)

    errors: list[str] = []
    app_name = app_name.strip()
    app_version = app_version.strip()
    environment = environment.strip().lower()
    public_base_url = public_base_url.strip().rstrip("/")
    api_prefix = "/" + api_prefix.strip().strip("/")
    cors_origins = cors_origins.strip()
    fdex_host = fdex_host.strip()
    ai_provider = ai_provider.strip()
    ai_base_url = ai_base_url.strip().rstrip("/")
    ai_model = ai_model.strip()
    admin_username = admin_username.strip()

    if not app_name or len(app_name) > 100:
        errors.append("服务名称不能为空且不能超过 100 个字符")
    if not _VERSION_PATTERN.fullmatch(app_version):
        errors.append("服务版本格式无效")
    if environment not in {"production", "staging", "development", "test"}:
        errors.append("运行环境必须是 production、staging、development 或 test")
    if not _valid_url(public_base_url):
        errors.append("公开服务地址必须是有效的 HTTP/HTTPS 地址")
    if not api_prefix or api_prefix == "/":
        errors.append("API 前缀不能为空或根目录")
    if fdex_host not in {"127.0.0.1", "localhost", "::1"}:
        errors.append("监听地址只能使用本机回环地址，避免服务端口直接暴露公网")
    if ai_base_url and not _valid_url(ai_base_url, allow_empty=True):
        errors.append("AI 接口地址格式无效")
    if not _USERNAME_PATTERN.fullmatch(admin_username):
        errors.append("管理员用户名只能包含字母、数字及 . _ @ -，长度为 3-64")

    try:
        port = int(fdex_port)
        workers = int(fdex_workers)
        timeout = float(ai_timeout_seconds)
        session_hours = int(admin_session_hours)
        if not 1 <= port <= 65535:
            raise ValueError
        if not 1 <= workers <= 16:
            raise ValueError
        if not 5 <= timeout <= 600:
            raise ValueError
        if not 1 <= session_hours <= 168:
            raise ValueError
    except ValueError:
        errors.append("端口、进程数、超时时间或会话时长超出允许范围")
        port, workers, timeout, session_hours = 18080, 2, 60.0, 12

    if errors:
        set_flash(request, "；".join(errors), "error")
        write_audit(request, "save_settings", success=False, errors=errors)
        return RedirectResponse("/admin/settings", status_code=303)

    current_env = read_env()
    updates = {
        "APP_NAME": app_name,
        "APP_VERSION": app_version,
        "ENVIRONMENT": environment,
        "PUBLIC_BASE_URL": public_base_url,
        "API_PREFIX": api_prefix,
        "CORS_ORIGINS": cors_origins,
        "FDEX_HOST": fdex_host,
        "FDEX_PORT": str(port),
        "FDEX_WORKERS": str(workers),
        "AI_PROVIDER": ai_provider,
        "AI_BASE_URL": ai_base_url,
        "AI_MODEL": ai_model,
        "AI_TIMEOUT_SECONDS": str(timeout),
        "ADMIN_USERNAME": admin_username,
        "ADMIN_COOKIE_SECURE": "true" if admin_cookie_secure else "false",
        "ADMIN_SESSION_HOURS": str(session_hours),
    }
    key_changed = False
    if clear_ai_api_key:
        updates["AI_API_KEY"] = ""
        key_changed = bool(current_env.get("AI_API_KEY"))
    elif ai_api_key.strip():
        updates["AI_API_KEY"] = ai_api_key.strip()
        key_changed = True

    backup = write_env(updates)
    get_settings.cache_clear()
    changed = [key for key, value in updates.items() if current_env.get(key, "") != value]
    write_audit(
        request,
        "save_settings",
        changed_keys=[key for key in changed if key != "AI_API_KEY"],
        ai_api_key_changed=key_changed,
        backup=str(backup) if backup else "",
    )
    set_flash(request, "配置已安全保存并完成备份。涉及端口、会话或 API 路由的修改需要重启服务。")
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/test-ai")
async def test_ai(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf_token)
    result = await test_ai_connection(fresh_settings())
    write_audit(
        request,
        "test_ai",
        success=bool(result["ok"]),
        status=result.get("status"),
        latency_ms=result.get("latency_ms"),
    )
    category = "success" if result["ok"] else "error"
    set_flash(
        request,
        f"{result['message']}（耗时 {result['latency_ms']} ms）",
        category,
    )
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/password")
def change_password(
    request: Request,
    csrf_token: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf_token)
    settings = fresh_settings()
    if not verify_admin_credentials(settings.admin_username, current_password):
        set_flash(request, "当前密码不正确。", "error")
        write_audit(request, "change_password", success=False, reason="wrong_current_password")
        return RedirectResponse("/admin/settings#security", status_code=303)
    if len(new_password) < 12:
        set_flash(request, "新密码至少需要 12 个字符。", "error")
        return RedirectResponse("/admin/settings#security", status_code=303)
    if new_password != confirm_password:
        set_flash(request, "两次输入的新密码不一致。", "error")
        return RedirectResponse("/admin/settings#security", status_code=303)
    if new_password == current_password:
        set_flash(request, "新密码不能与当前密码相同。", "error")
        return RedirectResponse("/admin/settings#security", status_code=303)

    write_env({"ADMIN_PASSWORD": new_password})
    get_settings.cache_clear()
    write_audit(request, "change_password")
    logout_session(request)
    set_flash(request, "管理员密码已修改，请重新登录。")
    return RedirectResponse("/admin/login", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, lines: int | None = None) -> HTMLResponse | RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    settings = fresh_settings()
    logs = await asyncio.to_thread(service_logs, settings, lines)
    audit = await asyncio.to_thread(read_audit, 100)
    return templates.TemplateResponse(
        "logs.html",
        _context(request, logs=logs, audit=audit, requested_lines=lines or settings.admin_log_lines),
    )


@router.get("/maintenance", response_class=HTMLResponse)
async def maintenance_page(request: Request, refresh: int = 0) -> HTMLResponse | RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    settings = fresh_settings()
    github, local_git = await asyncio.gather(
        github_status(settings, force=bool(refresh)),
        asyncio.to_thread(git_info, settings),
    )
    local_sha = str(local_git.get("sha", ""))
    remote_sha = str(github.get("remote_commit", {}).get("sha", ""))
    update_available = bool(local_sha and remote_sha and local_sha != "未知" and local_sha != remote_sha)
    return templates.TemplateResponse(
        "maintenance.html",
        _context(
            request,
            github=github,
            local_git=local_git,
            update_available=update_available,
        ),
    )


@router.post("/restart")
def restart_service(
    request: Request,
    csrf_token: str = Form(...),
    confirm: str = Form(""),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf_token)
    if confirm != "restart":
        set_flash(request, "请勾选确认后再重启服务。", "error")
        return RedirectResponse("/admin/maintenance", status_code=303)
    try:
        task = schedule_service_restart(fresh_settings())
    except (ValueError, RuntimeError) as exc:
        write_audit(request, "restart_service", success=False, error=str(exc))
        set_flash(request, f"无法安排重启：{exc}", "error")
    else:
        write_audit(request, "restart_service", task=task)
        set_flash(request, "服务将在约 2 秒后重启，页面短暂断开属于正常现象。")
    return RedirectResponse("/admin/maintenance", status_code=303)


@router.post("/update")
def update_server(
    request: Request,
    csrf_token: str = Form(...),
    confirm: str = Form(""),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect
    verify_csrf(request, csrf_token)
    if confirm != "update":
        set_flash(request, "请勾选确认后再执行服务端更新。", "error")
        return RedirectResponse("/admin/maintenance", status_code=303)
    settings = fresh_settings()
    local_git = git_info(settings)
    if local_git.get("dirty"):
        set_flash(request, "仓库存在未提交修改，为避免数据丢失，已阻止网页更新。", "error")
        write_audit(request, "update_server", success=False, reason="dirty_worktree")
        return RedirectResponse("/admin/maintenance", status_code=303)
    try:
        task = schedule_server_update(settings)
    except (FileNotFoundError, RuntimeError) as exc:
        write_audit(request, "update_server", success=False, error=str(exc))
        set_flash(request, f"无法启动更新：{exc}", "error")
    else:
        write_audit(request, "update_server", task=task)
        set_flash(request, "更新任务已在后台启动，服务会自动拉取 main、安装依赖并重启。")
    return RedirectResponse("/admin/maintenance", status_code=303)
