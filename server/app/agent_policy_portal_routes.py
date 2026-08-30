from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_projects import agent_project_store
from app.config import SERVER_DIR
from app.remote_mcp_credentials import remote_mcp_credential_store
from app.remote_mcp_registry import remote_mcp_registry
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf

router = APIRouter(prefix="/account/agent/runtime", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _authority_store():
    store = agent_project_store()
    if not all(hasattr(store, name) for name in ("account_policy", "save_account_policy", "sync_owner_installations")):
        raise RuntimeError("当前服务尚未启用 GitHub App Installation 权限模型")
    return store


@router.get("", response_class=HTMLResponse, response_model=None)
def runtime_policy_page(request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        store = _authority_store()
        policy = store.account_policy(owner_id)
        sync_status = store.sync_status(owner_id)
        projects = store.list_projects(owner_id, enabled_only=True)
        remote_mcp_servers = remote_mcp_registry().list(owner_id)
        remote_mcp_credentials = remote_mcp_credential_store().list_metadata(owner_id)
    except (ValueError, RuntimeError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/agent", status_code=303)
    return templates.TemplateResponse(
        "user_agent_settings.html",
        _ctx(
            request,
            user,
            policy=policy,
            sync_status=sync_status,
            repository_count=len(projects),
            remote_mcp_servers=remote_mcp_servers,
            remote_mcp_credentials=remote_mcp_credentials,
        ),
    )


@router.post("/policy", response_model=None)
def runtime_policy_save(
    request: Request,
    csrf_token: str = Form(...),
    allow_network: bool = Form(default=False),
    sandbox_memory_mb: int = Form(default=2048),
    sandbox_cpu_percent: int = Form(default=150),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        policy = _authority_store().save_account_policy(
            str(user["id"]),
            allow_network=allow_network,
            sandbox_memory_mb=sandbox_memory_mb,
            sandbox_cpu_percent=sandbox_cpu_percent,
        )
        _flash(
            request,
            f"Coding Agent 运行策略已保存：内存 {policy['sandbox_memory_mb']} MB · "
            f"CPU {policy['sandbox_cpu_percent']}% · 构建联网{'允许' if policy['allow_network'] else '隔离'}",
            "success",
        )
    except (ValueError, RuntimeError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/agent/runtime", status_code=303)


@router.post("/sync", response_model=None)
def runtime_repository_sync(request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        status = _authority_store().sync_owner_installations(owner_id, force=True, strict=True)
        _flash(
            request,
            f"GitHub App 仓库已刷新：当前 Coding Agent 自动可用 {int(status.get('repository_count') or 0)} 个仓库",
            "success",
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"GitHub 仓库刷新失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime", status_code=303)


@router.post("/mcp", response_model=None)
def remote_mcp_create(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    enabled_tools: str = Form(default=""),
    enabled: bool = Form(default=False),
    startup_timeout_sec: int = Form(default=15),
    tool_timeout_sec: int = Form(default=60),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        server = remote_mcp_registry().save(
            str(user["id"]),
            name=name,
            url=url,
            enabled_tools=enabled_tools,
            enabled=enabled,
            startup_timeout_sec=startup_timeout_sec,
            tool_timeout_sec=tool_timeout_sec,
        )
        suffix = "；后续新启动/续接的 Codex 任务将通过 FDEX 安全网关使用它" if server["enabled"] else ""
        _flash(request, f"Remote MCP 已保存：{server['name']}{suffix}", "success")
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"Remote MCP 保存失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime#remote-mcp", status_code=303)


@router.post("/mcp/{server_id}", response_model=None)
def remote_mcp_update(
    server_id: str,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    enabled_tools: str = Form(default=""),
    enabled: bool = Form(default=False),
    startup_timeout_sec: int = Form(default=15),
    tool_timeout_sec: int = Form(default=60),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        if remote_mcp_registry().get(owner_id, server_id) is None:
            raise KeyError("Remote MCP 不存在")
        server = remote_mcp_registry().save(
            owner_id,
            name=name,
            url=url,
            enabled_tools=enabled_tools,
            enabled=enabled,
            startup_timeout_sec=startup_timeout_sec,
            tool_timeout_sec=tool_timeout_sec,
            server_id=server_id,
        )
        if server["enabled"]:
            detail = "旧任务 lease 已立即失效；新配置只会由后续新启动/续接任务重新签发"
        else:
            detail = "已停用，现存 task lease 的后续请求会立即失效"
        _flash(request, f"Remote MCP 已更新：{server['name']}；{detail}", "success")
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"Remote MCP 更新失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime#remote-mcp", status_code=303)


@router.post("/mcp/{server_id}/bearer", response_model=None)
def remote_mcp_bearer_save(
    server_id: str,
    request: Request,
    csrf_token: str = Form(...),
    bearer_token: str = Form(...),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        server = remote_mcp_registry().get(owner_id, server_id)
        if server is None:
            raise KeyError("Remote MCP 不存在")
        metadata = remote_mcp_credential_store().set_bearer(owner_id, server_id, bearer_token)
        _flash(
            request,
            f"Remote MCP Bearer 已加密保存：{server['name']} · 指纹 {metadata['fingerprint']}。"
            "当前任务旧 lease 已立即失效；后续新任务 / resume / fork 才会使用新凭据。",
            "success",
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"Remote MCP Bearer 保存失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime#remote-mcp", status_code=303)


@router.post("/mcp/{server_id}/bearer/delete", response_model=None)
def remote_mcp_bearer_delete(server_id: str, request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        server = remote_mcp_registry().get(owner_id, server_id)
        if server is None:
            raise KeyError("Remote MCP 不存在")
        removed = remote_mcp_credential_store().delete(owner_id, server_id)
        if not removed:
            raise KeyError("当前 Remote MCP 没有已保存的 Bearer")
        _flash(
            request,
            f"Remote MCP Bearer 已删除：{server['name']}。当前任务旧 lease 已立即失效；"
            "后续新任务将以匿名模式连接，除非再次配置凭据。",
            "success",
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"Remote MCP Bearer 删除失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime#remote-mcp", status_code=303)


@router.post("/mcp/{server_id}/disable", response_model=None)
def remote_mcp_disable(server_id: str, request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        server = remote_mcp_registry().set_enabled(str(user["id"]), server_id, False)
        _flash(
            request,
            f"Remote MCP 已立即停用：{server['name']}；当前任务已有 lease 的后续请求也会被拒绝",
            "success",
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"Remote MCP 停用失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime#remote-mcp", status_code=303)


@router.post("/mcp/{server_id}/delete", response_model=None)
def remote_mcp_delete(server_id: str, request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        _verify_csrf(request, csrf_token)
        if not remote_mcp_credential_store().delete_server(owner_id, server_id):
            raise KeyError("Remote MCP 不存在")
        _flash(request, "Remote MCP 及其 FDEX 凭据已原子移除；对应现存 lease 将立即失效", "success")
    except (KeyError, ValueError, RuntimeError) as exc:
        _flash(request, f"Remote MCP 删除失败：{exc}", "error")
    return RedirectResponse("/account/agent/runtime#remote-mcp", status_code=303)
