from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import SERVER_DIR
from app.plugin_code_hosts import (
    CodeHostPluginError,
    connect_code_host,
    disconnect_code_host,
    list_code_host_repositories,
)
from app.plugin_feishu import (
    FeishuPluginError,
    connect_feishu,
    disconnect_feishu,
    list_feishu_chats,
)
from app.plugin_google_drive import (
    GoogleDrivePluginError,
    complete_google_drive_oauth,
    disconnect_google_drive,
    search_google_drive_files,
    start_google_drive_oauth,
)
from app.plugin_notion import (
    NotionPluginError,
    connect_notion,
    disconnect_notion,
    search_notion,
)
from app.plugin_feishu_runtime import install_feishu_runtime
from app.plugin_google_drive_runtime import install_google_drive_runtime
from app.plugin_notion_runtime import install_notion_runtime

# Promote native adapters before Plugin MCP imports plugin_connection_status by value.
install_feishu_runtime()
install_notion_runtime()
install_google_drive_runtime()

from app.plugin_feishu_mcp import install_feishu_mcp_tools
from app.plugin_google_drive_mcp import install_google_drive_mcp_tools
from app.plugin_notion_mcp import install_notion_mcp_tools
from app.plugin_mcp_gateway import _tool_call, _tool_catalog
from app.plugin_runtime import (
    catalog_snapshot,
    effective_agent_grant,
    plugin_audit_rows,
    plugin_connection_status,
    plugin_definition,
    save_agent_grant,
)
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf
from app.web_workspace import web_workspace_store

install_feishu_mcp_tools()
install_notion_mcp_tools()
install_google_drive_mcp_tools()

router = APIRouter(prefix="/account/plugins", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _owner(user: dict[str, object]) -> str:
    return str(user["id"])


def _code_host(plugin_id: str) -> str:
    clean = (plugin_id or "").strip().lower()
    if clean not in {"gitlab", "gitee"}:
        raise ValueError("当前代码托管连接入口仅支持 GitLab / Gitee")
    return clean


def _native_plugin(plugin_id: str) -> str:
    clean = (plugin_id or "").strip().lower()
    if clean not in {"gitlab", "gitee", "feishu", "notion", "google-drive"}:
        raise ValueError("当前插件尚未提供原生连接入口")
    return clean


def _mcp_error_text(result: dict[str, object]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return "Plugin MCP 返回未知错误"
    for item in content:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            return str(item["text"])[:800]
    return "Plugin MCP 返回未知错误"


@router.get("", response_class=HTMLResponse, response_model=None)
def plugin_center(request: Request, category: str = "all") -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    store.ensure_defaults(owner_id)
    employees = [item for item in store.list(owner_id, "employee", limit=500) if not bool(item.get("_deleted"))]
    catalog = catalog_snapshot(owner_id)
    categories = ["代码仓库", "沟通与知识", "项目管理", "部署与运维", "可观测性"]
    clean_category = category if category in categories else "all"
    visible = catalog if clean_category == "all" else [item for item in catalog if item["category"] == clean_category]
    grants: dict[str, dict[str, str]] = {}
    for employee in employees:
        grants[str(employee["id"])] = {
            item["id"]: str(effective_agent_grant(owner_id, employee, item["id"])["mode"])
            for item in catalog
        }
    return templates.TemplateResponse(
        "user_plugins.html",
        _ctx(
            request,
            user,
            page="plugins",
            plugins=visible,
            plugin_catalog=catalog,
            employees=employees,
            grants=grants,
            categories=categories,
            selected_category=clean_category,
            audit_rows=plugin_audit_rows(owner_id, 60),
            preferences=store.preferences(owner_id),
        ),
    )


@router.get("/catalog.json", response_model=None)
def plugin_catalog_json(request: Request) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    return JSONResponse({"ok": True, "plugins": catalog_snapshot(_owner(user))})


@router.post("/{plugin_id}/connect", response_model=None)
def connect_native_plugin(
    plugin_id: str,
    request: Request,
    csrf_token: str = Form(...),
    access_token: str = Form(""),
    base_url: str = Form(""),
    app_id: str = Form(""),
    app_secret: str = Form(""),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    clean_plugin = (plugin_id or "").strip().lower()
    try:
        _verify_csrf(request, csrf_token)
        clean_plugin = _native_plugin(clean_plugin)
        definition = plugin_definition(clean_plugin)
        if clean_plugin == "feishu":
            saved = connect_feishu(_owner(user), app_id, app_secret)
            label = str(saved.get("app_id") or "")
            _flash(request, f"{definition.name} 已连接：{label}。App Secret 已加密保存，tenant_access_token 将自动续期。", "success")
        elif clean_plugin == "notion":
            saved = connect_notion(_owner(user), access_token)
            label = str(saved.get("workspace_name") or saved.get("bot_name") or "Notion")
            _flash(request, f"Notion 已连接：{label}。Integration Token 已加密保存，仅能访问你在 Notion 中明确共享给该 Integration 的内容。", "success")
        elif clean_plugin == "google-drive":
            raise ValueError("Google Drive 使用 OAuth 授权入口连接，不接受手工访问令牌")
        else:
            saved = connect_code_host(_owner(user), clean_plugin, access_token, base_url=base_url)
            count = int(saved.get("repository_count") or 0)
            login = str(saved.get("account_login") or saved.get("account_name") or "")
            _flash(request, f"{definition.name} 已连接：{login}，已验证 {count} 个可访问仓库/项目。", "success")
    except (CodeHostPluginError, FeishuPluginError, NotionPluginError, GoogleDrivePluginError, KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/plugins#plugin-{clean_plugin}", status_code=303)


@router.post("/google-drive/oauth/start", response_model=None)
def google_drive_oauth_start(request: Request, csrf_token: str = Form(...)) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        flow = start_google_drive_oauth(_owner(user))
        return RedirectResponse(str(flow["authorize_url"]), status_code=303)
    except (GoogleDrivePluginError, KeyError, ValueError) as exc:
        _flash(request, f"Google Drive 授权启动失败：{exc}", "error")
        return RedirectResponse("/account/plugins#plugin-google-drive", status_code=303)


@router.get("/google-drive/oauth/callback", response_model=None)
def google_drive_oauth_callback(request: Request, state: str = "", code: str = "", error: str = "") -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        if error:
            raise GoogleDrivePluginError(f"Google 拒绝授权：{str(error)[:300]}")
        saved = complete_google_drive_oauth(_owner(user), state=state, code=code)
        label = str(saved.get("account_email") or saved.get("display_name") or "Google Drive")
        _flash(
            request,
            f"Google Drive 已连接：{label}。访问令牌和 refresh_token 已加密保存，FDEX 将按需自动续期。",
            "success",
        )
    except (GoogleDrivePluginError, KeyError, ValueError) as exc:
        _flash(request, f"Google Drive OAuth 连接失败：{exc}", "error")
    return RedirectResponse("/account/plugins#plugin-google-drive", status_code=303)


@router.post("/{plugin_id}/disconnect", response_model=None)
def disconnect_native_plugin(
    plugin_id: str,
    request: Request,
    csrf_token: str = Form(...),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    clean_plugin = (plugin_id or "").strip().lower()
    try:
        _verify_csrf(request, csrf_token)
        clean_plugin = _native_plugin(clean_plugin)
        definition = plugin_definition(clean_plugin)
        if clean_plugin == "feishu":
            removed = disconnect_feishu(_owner(user))
        elif clean_plugin == "notion":
            removed = disconnect_notion(_owner(user))
        elif clean_plugin == "google-drive":
            removed = disconnect_google_drive(_owner(user))
        else:
            removed = disconnect_code_host(_owner(user), clean_plugin)
        _flash(request, f"{definition.name} 已断开。" if removed else f"{definition.name} 当前没有连接。", "success")
    except (CodeHostPluginError, FeishuPluginError, NotionPluginError, GoogleDrivePluginError, KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/plugins#plugin-{clean_plugin}", status_code=303)


@router.get("/{plugin_id}/repositories.json", response_model=None)
def plugin_code_host_repositories(plugin_id: str, request: Request) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    try:
        clean_plugin = _code_host(plugin_id)
        repositories = list_code_host_repositories(_owner(user), clean_plugin)
        return JSONResponse({"ok": True, "plugin_id": clean_plugin, "count": len(repositories), "repositories": repositories})
    except (CodeHostPluginError, KeyError, PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/feishu/chats.json", response_model=None)
def plugin_feishu_chats(request: Request) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    try:
        payload = list_feishu_chats(_owner(user), page_size=50)
        return JSONResponse({"ok": True, **payload})
    except (FeishuPluginError, KeyError, PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/notion/search.json", response_model=None)
def plugin_notion_search(request: Request, q: str = "") -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    try:
        payload = search_notion(_owner(user), q, page_size=50)
        return JSONResponse({"ok": True, **payload})
    except (NotionPluginError, KeyError, PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/google-drive/files.json", response_model=None)
def plugin_google_drive_files(request: Request, q: str = "") -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    try:
        payload = search_google_drive_files(_owner(user), q, page_size=50)
        return JSONResponse({"ok": True, **payload})
    except (GoogleDrivePluginError, KeyError, PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/{plugin_id}/agents/{employee_id}/verify", response_model=None)
def verify_plugin_agent_mcp(
    plugin_id: str,
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
) -> Response:
    """Run a real read-only request through the same Plugin MCP dispatcher used by Codex.

    This is intentionally not a synthetic connection check: the selected 智体's current grant is
    evaluated, the MCP catalog is generated, `_tool_call` enters Plugin Runtime authorization/audit,
    and the native provider adapter performs a live read. No write tool is invoked and no
    third-party credential is returned to the browser.
    """
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    clean_plugin = (plugin_id or "").strip().lower()
    try:
        _verify_csrf(request, csrf_token)
        clean_plugin = _native_plugin(clean_plugin)
        owner_id = _owner(user)
        definition = plugin_definition(clean_plugin)
        status = plugin_connection_status(owner_id, clean_plugin)
        if not bool(status.get("connected")):
            raise ValueError(f"{definition.name} 尚未连接")
        employee = web_workspace_store().get(owner_id, "employee", int(employee_id))
        if bool(employee.get("_deleted")) or not bool(employee.get("active", True)):
            raise ValueError("智体已停用，不能验证插件 MCP")
        grant = effective_agent_grant(owner_id, employee, clean_plugin)
        if str(grant.get("mode") or "none") == "none":
            raise ValueError(f"请先给智体授权 {definition.name} 的只读或读写权限")

        if clean_plugin == "feishu":
            tool_name = "feishu_list_chats"
        elif clean_plugin == "notion":
            tool_name = "notion_search"
        elif clean_plugin == "google-drive":
            tool_name = "drive_search_files"
        else:
            tool_name = f"{clean_plugin}_list_repositories"
        catalog_names = {str(item.get("name") or "") for item in _tool_catalog(owner_id, employee)}
        if tool_name not in catalog_names:
            raise ValueError(f"{definition.name} MCP Tool 当前未向该智体开放")
        result = _tool_call({"owner_id": owner_id, "employee": employee}, tool_name, {})
        if bool(result.get("isError")):
            raise RuntimeError(_mcp_error_text(result))
        content = result.get("content")
        text = ""
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and str(item.get("text") or "").strip():
                    text = str(item["text"])
                    break
        try:
            payload = json.loads(text) if text else []
        except json.JSONDecodeError as exc:
            raise RuntimeError("Plugin MCP 验证返回了无效 JSON") from exc
        if clean_plugin == "feishu":
            count = int(payload.get("count") or 0) if isinstance(payload, dict) else 0
            unit = "个群聊"
        elif clean_plugin == "notion":
            count = int(payload.get("count") or 0) if isinstance(payload, dict) else 0
            unit = "个页面/Data Source"
        elif clean_plugin == "google-drive":
            count = int(payload.get("count") or 0) if isinstance(payload, dict) else 0
            unit = "个 Drive 文件"
        else:
            count = len(payload) if isinstance(payload, list) else 0
            unit = "个仓库/项目"
        mode_label = "读写" if str(grant.get("mode")) == "write" else "只读"
        _flash(
            request,
            f"{definition.name} MCP 验证通过：智体「{str(employee.get('name') or employee_id)}」当前为{mode_label}权限，实时读取到 {count} {unit}。",
            "success",
        )
    except (
        CodeHostPluginError,
        FeishuPluginError,
        NotionPluginError,
        GoogleDrivePluginError,
        KeyError,
        PermissionError,
        RuntimeError,
        ValueError,
    ) as exc:
        _flash(request, f"Plugin MCP 验证失败：{exc}", "error")
    return RedirectResponse(f"/account/plugins#plugin-{clean_plugin}", status_code=303)


@router.post("/{plugin_id}/agents/{employee_id}", response_model=None)
def save_plugin_agent_grant(
    plugin_id: str,
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    mode: str = Form("none"),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        definition = plugin_definition(plugin_id)
        status = plugin_connection_status(_owner(user), definition.id)
        if not bool(status.get("connected")):
            raise ValueError(f"{definition.name} 尚未连接，不能授权给智体")
        save_agent_grant(_owner(user), employee_id, definition.id, mode)
        label = {"none": "不授权", "read": "只读", "write": "读写"}.get(mode, mode)
        _flash(request, f"{definition.name} 智体权限已更新为：{label}", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/plugins", status_code=303)
