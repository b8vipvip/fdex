from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_projects import agent_project_store
from app.central_auth import AuthRateLimitError, central_auth_store
from app.config import SERVER_DIR, fresh_settings
from app.github_web_oauth import GitHubWebOAuthError, GitHubWebOAuthStore

router = APIRouter(prefix="/account", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))

_USER_SESSION = "fdex_user_session_id"
_USER_CSRF = "fdex_user_csrf"
_USER_FLASH = "fdex_user_flash"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "")


def _csrf(request: Request) -> str:
    token = str(request.session.get(_USER_CSRF) or "")
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[_USER_CSRF] = token
    return token


def _verify_csrf(request: Request, provided: str) -> None:
    expected = str(request.session.get(_USER_CSRF) or "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise ValueError("页面已过期，请刷新后重试")


def _flash(request: Request, message: str, category: str = "info") -> None:
    request.session[_USER_FLASH] = {"message": (message or "")[:500], "category": category[:20]}


def _pop_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop(_USER_FLASH, None)
    return value if isinstance(value, dict) else None


def _current_user(request: Request) -> dict[str, object] | None:
    session_id = str(request.session.get(_USER_SESSION) or "").strip()
    if not session_id.startswith("ses_"):
        return None
    store = central_auth_store()
    store.init()
    with store.db() as conn:
        row = conn.execute(
            """SELECT s.id AS session_id,u.id,u.email,u.name,u.company_name,u.enabled,
                      s.refresh_expires_at,s.revoked_at
               FROM user_sessions s JOIN users u ON u.id=s.user_id
               WHERE s.id=? AND s.revoked_at='' AND s.refresh_expires_at>? AND u.enabled=1""",
            (session_id, _now_iso()),
        ).fetchone()
    if row is None:
        request.session.pop(_USER_SESSION, None)
        return None
    return {
        "id": str(row["id"]),
        "email": str(row["email"]),
        "name": str(row["name"]),
        "company_name": str(row["company_name"] or ""),
        "session_id": str(row["session_id"]),
    }


def _login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(f"/account/login?next={quote(target, safe='/%?=&')}", status_code=303)


def _ctx(request: Request, user: dict[str, object] | None = None, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "user": user,
        "csrf_token": _csrf(request),
        "flash": _pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


def _connection_by_id(owner_id: str, connection_id: int) -> dict[str, object]:
    return agent_project_store().get_connection(owner_id, connection_id)


def _repository_by_name(owner_id: str, connection_id: int, full_name: str) -> dict[str, object]:
    needle = (full_name or "").strip()
    if not needle:
        raise ValueError("请选择 GitHub 仓库")
    # User portal only accepts a repository returned by GitHub for this exact account-bound connection.
    for page in range(1, 6):
        repositories = agent_project_store().list_repositories(owner_id, connection_id, page=page, per_page=100)
        for repository in repositories:
            if str(repository.get("full_name") or "") == needle:
                return repository
        if len(repositories) < 100:
            break
    raise ValueError("当前 GitHub 授权无法访问该仓库")


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request, next: str = "/account/github") -> Response:
    if _current_user(request) is not None:
        return RedirectResponse("/account/github", status_code=303)
    return templates.TemplateResponse(
        "user_login.html",
        _ctx(request, None, error="", next_path=next if next.startswith("/account") else "/account/github"),
    )


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    next_path: str = Form(default="/account/github"),
) -> Response:
    try:
        _verify_csrf(request, csrf_token)
        result = central_auth_store().login(
            email=email,
            password=password,
            device_name="FDEX Web",
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except AuthRateLimitError as exc:
        return templates.TemplateResponse(
            "user_login.html",
            _ctx(request, None, error=str(exc), next_path="/account/github"),
            status_code=429,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "user_login.html",
            _ctx(request, None, error=str(exc), next_path="/account/github"),
            status_code=401,
        )
    request.session[_USER_SESSION] = str(result["session_id"])
    request.session[_USER_CSRF] = secrets.token_urlsafe(32)
    target = next_path if next_path.startswith("/account") else "/account/github"
    return RedirectResponse(target, status_code=303)


@router.post("/logout", response_model=None)
def logout(request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    try:
        _verify_csrf(request, csrf_token)
    except ValueError:
        pass
    if user is not None:
        central_auth_store().revoke_session(str(user["id"]), str(user["session_id"]))
    for key in (_USER_SESSION, _USER_CSRF, _USER_FLASH):
        request.session.pop(key, None)
    return RedirectResponse("/account/login", status_code=303)


@router.get("", response_model=None)
def account_home(request: Request) -> Response:
    if _current_user(request) is None:
        return _login_redirect(request)
    return RedirectResponse("/account/github", status_code=303)


@router.get("/github", response_class=HTMLResponse, response_model=None)
def github_center(
    request: Request,
    connection_id: int | None = None,
    query: str = "",
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    store = agent_project_store()
    connections = store.list_connections(owner_id)
    selected = next((item for item in connections if int(item["id"]) == int(connection_id or 0)), None)
    if selected is None:
        selected = next((item for item in connections if not bool(item.get("needs_reconnect"))), connections[0] if connections else None)
    repositories: list[dict[str, object]] = []
    repository_error = ""
    if selected is not None and not bool(selected.get("needs_reconnect")):
        try:
            repositories = store.list_repositories(owner_id, int(selected["id"]), query=query, per_page=100)
        except (KeyError, ValueError, RuntimeError) as exc:
            repository_error = str(exc)
    projects = store.list_projects(owner_id)
    settings = fresh_settings()
    return templates.TemplateResponse(
        "user_github.html",
        _ctx(
            request,
            user,
            connections=connections,
            selected_connection=selected,
            repositories=repositories,
            repository_error=repository_error,
            projects=projects,
            query=query[:100],
            oauth_ready=settings.github_web_oauth_ready,
        ),
    )


@router.post("/github/connect", response_model=None)
def github_connect(request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        flow = GitHubWebOAuthStore().start(str(user["id"]))
        return RedirectResponse(str(flow["authorize_url"]), status_code=303)
    except (ValueError, GitHubWebOAuthError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/github", status_code=303)


@router.get("/github/callback", response_model=None)
def github_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    if error:
        _flash(request, error_description or error or "GitHub 授权已取消", "error")
        return RedirectResponse("/account/github", status_code=303)
    try:
        connection = GitHubWebOAuthStore().complete(str(user["id"]), state=state, code=code)
        _flash(request, f"GitHub 已连接：{connection.get('login') or connection.get('name')}", "success")
    except (ValueError, RuntimeError, GitHubWebOAuthError) as exc:
        _flash(request, f"GitHub 授权失败：{exc}", "error")
    return RedirectResponse("/account/github", status_code=303)


@router.post("/github/connections/{connection_id}/disconnect", response_model=None)
def github_disconnect(connection_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        agent_project_store().delete_connection(str(user["id"]), connection_id)
        _flash(request, "GitHub 连接已从当前 FDEX 账号移除", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/github", status_code=303)


@router.post("/github/projects", response_model=None)
def project_create(
    request: Request,
    csrf_token: str = Form(...),
    connection_id: int = Form(...),
    repo_full_name: str = Form(...),
    name: str = Form(default=""),
    base_branch: str = Form(default="main"),
    allow_push: bool = Form(default=False),
    allow_pr: bool = Form(default=False),
    allow_network: bool = Form(default=False),
    sandbox_memory_mb: int = Form(default=2048),
    sandbox_cpu_percent: int = Form(default=150),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        _connection_by_id(owner_id, connection_id)
        repository = _repository_by_name(owner_id, connection_id, repo_full_name)
        can_push = bool(repository.get("can_push"))
        if (allow_push or allow_pr) and not can_push:
            raise ValueError("当前 GitHub 账号对该仓库没有写权限")
        project = agent_project_store().save_project(
            owner_id,
            name=name.strip() or str(repository.get("name") or repo_full_name.rsplit("/", 1)[-1]),
            repo_full_name=repo_full_name,
            base_branch=base_branch.strip() or str(repository.get("default_branch") or "main"),
            connection_id=connection_id,
            allow_push=allow_push and can_push,
            allow_pr=allow_pr and can_push,
            allow_network=allow_network,
            sandbox_memory_mb=max(128, min(16384, sandbox_memory_mb)),
            sandbox_cpu_percent=max(10, min(800, sandbox_cpu_percent)),
        )
        _flash(request, f"Agent 项目已添加：{project['name']}", "success")
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/github?connection_id={connection_id}", status_code=303)


@router.post("/github/projects/{project_id}", response_model=None)
def project_update(
    project_id: int,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    base_branch: str = Form(default="main"),
    allow_push: bool = Form(default=False),
    allow_pr: bool = Form(default=False),
    allow_network: bool = Form(default=False),
    sandbox_memory_mb: int = Form(default=2048),
    sandbox_cpu_percent: int = Form(default=150),
    enabled: bool = Form(default=False),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        current = agent_project_store().get_project(owner_id, project_id)
        connection_id = int(current["connection_id"]) if current.get("connection_id") else None
        if connection_id is None:
            raise ValueError("该项目没有 GitHub 授权连接")
        repository = _repository_by_name(owner_id, connection_id, str(current["repo_full_name"]))
        can_push = bool(repository.get("can_push"))
        if (allow_push or allow_pr) and not can_push:
            raise ValueError("当前 GitHub 授权已没有该仓库的写权限")
        agent_project_store().save_project(
            owner_id,
            name=name,
            repo_full_name=str(current["repo_full_name"]),
            base_branch=base_branch,
            connection_id=connection_id,
            allow_push=allow_push and can_push,
            allow_pr=allow_pr and can_push,
            allow_network=allow_network,
            sandbox_memory_mb=max(128, min(16384, sandbox_memory_mb)),
            sandbox_cpu_percent=max(10, min(800, sandbox_cpu_percent)),
            enabled=enabled,
            project_id=project_id,
        )
        _flash(request, "项目权限已更新", "success")
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/github", status_code=303)


@router.post("/github/projects/{project_id}/delete", response_model=None)
def project_delete(project_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        agent_project_store().delete_project(str(user["id"]), project_id)
        _flash(request, "Agent 项目已移除；历史任务、远端 Commit/PR 不受影响", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/github", status_code=303)
