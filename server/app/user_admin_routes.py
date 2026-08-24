from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_projects import agent_project_store
from app.audit import write_audit
from app.central_auth import central_auth_store
from app.config import SERVER_DIR, fresh_settings
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/admin/users", include_in_schema=False)
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


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{size:.0f} {unit}" if unit == "B" or size >= 10 else f"{size:.1f} {unit}"


def _resource_snapshot(user_id: str) -> dict[str, object]:
    github_count = 0
    project_count = 0
    try:
        store = agent_project_store()
        github_count = len(store.list_connections(user_id))
        project_count = len(store.list_projects(user_id))
    except Exception:
        # Account administration must remain available even if Agent storage is damaged.
        pass
    owner_root = Path(fresh_settings().fdex_agent_sandbox_root).expanduser().resolve() / "owners" / user_id
    sandbox_bytes = _directory_size(owner_root)
    return {
        "github_count": github_count,
        "project_count": project_count,
        "sandbox_bytes": sandbox_bytes,
        "sandbox_size": _format_bytes(sandbox_bytes),
    }


@router.get("", response_class=HTMLResponse, response_model=None)
def users_page(request: Request, user: str = Query("")) -> Response:
    if not is_admin(request):
        return _login_redirect()
    auth = central_auth_store()
    users = auth.list_users()
    for item in users:
        item.update(_resource_snapshot(str(item["id"])))

    selected_user: dict[str, object] | None = None
    sessions: list[dict[str, object]] = []
    if user.strip():
        try:
            selected_user = auth.get_user(user.strip())
            selected_user.update(_resource_snapshot(str(selected_user["id"])))
            sessions = auth.list_sessions(str(selected_user["id"]))
        except KeyError:
            set_flash(request, "指定的 FDEX 用户不存在。", "error")

    return templates.TemplateResponse(
        "users.html",
        _ctx(
            request,
            users=users,
            user_stats=auth.user_stats(),
            selected_user=selected_user,
            sessions=sessions,
        ),
    )


@router.post("/{user_id}/status", response_model=None)
def update_user_status(
    user_id: str,
    request: Request,
    csrf_token: str = Form(...),
    enabled: str = Form(...),
) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    target_enabled = enabled.strip().lower() == "true"
    try:
        auth = central_auth_store()
        before = auth.get_user(user_id)
        after = auth.set_user_enabled(user_id, target_enabled)
        write_audit(
            request,
            "fdex_user_status_changed",
            user_id=user_id,
            email=after.get("email"),
            enabled=target_enabled,
            previous_enabled=before.get("enabled"),
        )
        if target_enabled:
            set_flash(request, f"已恢复 FDEX 用户 {after.get('email')}。用户可重新登录。")
        else:
            set_flash(request, f"已禁用 FDEX 用户 {after.get('email')}，并注销该用户全部现有会话。")
    except KeyError:
        write_audit(request, "fdex_user_status_changed", success=False, user_id=user_id, error="not_found")
        set_flash(request, "FDEX 用户不存在。", "error")
    return RedirectResponse(f"/admin/users?user={user_id}", status_code=303)


@router.post("/{user_id}/revoke-sessions", response_model=None)
def revoke_user_sessions(user_id: str, request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        auth = central_auth_store()
        user = auth.get_user(user_id)
        count = auth.revoke_user_sessions(user_id)
        write_audit(
            request,
            "fdex_user_sessions_revoked",
            user_id=user_id,
            email=user.get("email"),
            session_count=count,
        )
        set_flash(request, f"已注销 {user.get('email')} 的 {count} 个未撤销会话。")
    except KeyError:
        write_audit(request, "fdex_user_sessions_revoked", success=False, user_id=user_id, error="not_found")
        set_flash(request, "FDEX 用户不存在。", "error")
    return RedirectResponse(f"/admin/users?user={user_id}", status_code=303)
