from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.github_app_flow import GitHubAppFlowError, GitHubAppInstallationFlowStore
from app.github_app_agent_projects import agent_project_store
from app.user_portal_routes import _current_user, _flash, _login_redirect, _verify_csrf

router = APIRouter(prefix="/account/github/app", include_in_schema=False)


@router.post("/connect", response_model=None)
def github_app_connect(request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        flow = GitHubAppInstallationFlowStore().start(str(user["id"]))
        return RedirectResponse(str(flow["authorize_url"]), status_code=303)
    except (ValueError, RuntimeError, GitHubAppFlowError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/github", status_code=303)


@router.get("/oauth/callback", response_model=None)
def github_app_oauth_callback(
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
        _flash(request, error_description or error or "GitHub 身份授权已取消", "error")
        return RedirectResponse("/account/github", status_code=303)
    try:
        flow = GitHubAppInstallationFlowStore().complete_identity(str(user["id"]), state=state, code=code)
        return RedirectResponse(str(flow["install_url"]), status_code=303)
    except (ValueError, RuntimeError, GitHubAppFlowError) as exc:
        _flash(request, f"GitHub 身份验证失败：{exc}", "error")
        return RedirectResponse("/account/github", status_code=303)


@router.get("/setup", response_model=None)
def github_app_setup(
    request: Request,
    installation_id: int = 0,
    setup_action: str = "install",
    state: str = "",
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        connection = GitHubAppInstallationFlowStore().complete_installation(
            str(user["id"]),
            installation_id=installation_id,
            setup_action=setup_action,
            state=state,
        )
        selection = str(connection.get("github_app_repository_selection") or "all")
        selection_text = "指定仓库" if selection == "selected" else "全部仓库"
        _flash(
            request,
            f"GitHub App 已连接：{connection.get('login') or connection.get('name')} · {selection_text}",
            "success",
        )
    except (ValueError, RuntimeError, GitHubAppFlowError) as exc:
        _flash(request, f"GitHub App 安装验证失败：{exc}", "error")
    return RedirectResponse("/account/github", status_code=303)


@router.post("/connections/{connection_id}/refresh", response_model=None)
def github_app_refresh(connection_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        connection = agent_project_store().get_connection(owner_id, connection_id)
        if str(connection.get("auth_type") or "") != "github_app":
            raise ValueError("该连接不是 GitHub App 安装")
        installation_id = int(str(connection.get("github_app_installation_id") or "0"))
        refreshed = GitHubAppInstallationFlowStore().complete_installation(
            owner_id,
            installation_id=installation_id,
            setup_action="update",
            state="",
        )
        _flash(request, f"GitHub App 仓库权限已刷新：{refreshed.get('login')}", "success")
    except (ValueError, RuntimeError, GitHubAppFlowError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/github?connection_id={connection_id}", status_code=303)
