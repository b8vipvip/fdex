from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.agent_identity_runtime import next_agent_name
from app.config import SERVER_DIR
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf
from app.web_workspace import web_workspace_store

router = APIRouter(prefix="/account", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _owner(user: dict[str, object]) -> str:
    return str(user["id"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _render(request: Request, user: dict[str, object], page: str, **extra: object) -> Response:
    owner_id = _owner(user)
    return templates.TemplateResponse(
        "user_web_app_general.html",
        _ctx(request, user, page=page, preferences=web_workspace_store().preferences(owner_id), **extra),
    )


@router.get("/messages", response_class=HTMLResponse, response_model=None)
def general_messages_page(request: Request, query: str = "") -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    store.ensure_defaults(owner_id)
    clean = query.strip().casefold()[:100]
    agents = [item for item in store.list(owner_id, "employee") if bool(item.get("active", True))]
    groups = store.list(owner_id, "group", newest_first=True)
    if clean:
        agents = [item for item in agents if clean in f"{item.get('name') or ''} {item.get('role_prompt') or ''}".casefold()]
        groups = [item for item in groups if clean in f"{item.get('name') or ''} {item.get('description') or ''}".casefold()]
    for agent in agents:
        last = store.list(owner_id, "message", parent_id=int(agent["id"]), newest_first=True, limit=1)
        agent["last_message"] = str(last[0].get("content") or "") if last else "开始与智体沟通"
    for group in groups:
        last = store.list(owner_id, "group_message", parent_id=int(group["id"]), newest_first=True, limit=1)
        group["last_message"] = str(last[0].get("content") or "") if last else "工作群已创建"
    return _render(request, user, "messages", employees=agents, groups=groups, query=query[:100])


@router.get("/employees", response_class=HTMLResponse, response_model=None)
def general_agents_page(request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    store.ensure_defaults(owner_id)
    return _render(request, user, "employees", employees=store.list(owner_id, "employee", include_deleted=True))


@router.post("/employees", response_model=None)
def create_agent(
    request: Request,
    csrf_token: str = Form(...),
    role_prompt: str = Form(""),
    name: str = Form(""),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        display_name = (name or "").strip()[:80] or next_agent_name(store, owner_id)
        store.create(
            owner_id,
            "employee",
            {
                "name": display_name,
                "role_prompt": (role_prompt or "").strip()[:12000],
                "active": True,
                "knowledge_read": True,
                "knowledge_write": True,
                "coding_agent": False,
            },
            sort_key=display_name.casefold(),
        )
        _flash(request, "智体已创建", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/employees", status_code=303)


@router.post("/employees/{employee_id}", response_model=None)
def update_agent(
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(""),
    role_prompt: str = Form(""),
    active: bool = Form(False),
    knowledge_read: bool = Form(False),
    knowledge_write: bool = Form(False),
    coding_agent: bool = Form(False),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        current = store.get(owner_id, "employee", employee_id, include_deleted=True)
        current.pop("_parent_id", None)
        deleted = bool(current.pop("_deleted", False))
        for legacy in ("department", "position", "industry"):
            current.pop(legacy, None)
        display_name = (name or "").strip()[:80] or str(current.get("name") or "").strip()[:80] or f"智体 {employee_id}"
        current.update(
            {
                "name": display_name,
                "role_prompt": (role_prompt or "").strip()[:12000],
                "active": active,
                "knowledge_read": knowledge_read,
                "knowledge_write": knowledge_write,
                "coding_agent": coding_agent,
            }
        )
        store.upsert(
            owner_id,
            "employee",
            employee_id,
            current,
            sort_key=display_name.casefold(),
            deleted=deleted,
        )
        _flash(request, "智体设置已保存", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/employees", status_code=303)


@router.get("/groups", response_class=HTMLResponse, response_model=None)
def general_groups_page(request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    store.ensure_defaults(owner_id)
    agents = [item for item in store.list(owner_id, "employee") if bool(item.get("active", True))]
    groups = store.list(owner_id, "group", newest_first=True)
    return _render(request, user, "groups", employees=agents, groups=groups)


@router.post("/groups", response_model=None)
def general_group_create(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    member_ids: list[int] = Form(default=[]),
    auto_mode: bool = Form(False),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        valid_ids = {int(item["id"]) for item in store.list(owner_id, "employee") if bool(item.get("active", True))}
        selected = [int(value) for value in member_ids if int(value) in valid_ids]
        clean_name = (name or "").strip()[:100]
        if not clean_name:
            raise ValueError("请输入工作群名称")
        if not selected:
            raise ValueError("至少选择 1 个智体")
        group = store.create(
            owner_id,
            "group",
            {
                "name": clean_name,
                "description": (description or "").strip()[:1000],
                "member_ids": selected,
                "auto_mode": auto_mode,
                "created_at": _now(),
                "updated_at": _now(),
            },
            sort_key=_now(),
        )
        store.create(
            owner_id,
            "group_message",
            {
                "group_id": int(group["id"]),
                "role": "system",
                "employee_name": "",
                "content": "工作群已创建。",
                "created_at": _now(),
            },
            parent_id=int(group["id"]),
            sort_key=_now(),
        )
        return RedirectResponse(f"/account/chat/group/{group['id']}", status_code=303)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/groups", status_code=303)


@router.post("/settings", response_model=None)
def save_general_settings(
    request: Request,
    csrf_token: str = Form(...),
    professional_level: str = Form("auto"),
    default_home: str = Form("messages"),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    try:
        _verify_csrf(request, csrf_token)
        if professional_level not in {"business", "professional", "expert", "auto"}:
            professional_level = "auto"
        if default_home not in {"messages", "knowledge", "discover", "me"}:
            default_home = "messages"
        web_workspace_store().save_preferences(
            _owner(user),
            professional_level=professional_level,
            default_home=default_home,
        )
        _flash(request, "Web 用户设置已保存", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/settings", status_code=303)


@router.get("/info/{slug}", response_class=HTMLResponse, response_model=None)
def general_info_page(slug: str, request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    pages = {
        "guide": (
            "使用说明",
            "Web 用户端与 Android 使用同一个 FDEX 中心账号。消息、智体、工作群、知识库、工作项目、GitHub 与 Coding Agent、账号安全均可从顶部导航或“我的”进入。",
        ),
        "privacy": (
            "隐私说明",
            "FDEX 中心账号、GitHub/Coding Agent 与远程长期记忆按 user_id 隔离。Web 工作区数据保存在中心服务端账号空间；密码、GitHub 安装 Token 与 AI API Key 不会作为 Web 工作区内容保存。",
        ),
        "contact": (
            "联系我们",
            "如遇到登录、邮件验证码、GitHub App、Coding Agent 或数据操作问题，请先保留页面提示与服务端运行日志，再联系 FDEX 管理员。",
        ),
        "update": (
            "版本与更新",
            "Web 用户端随中心服务端版本更新，无需单独下载安装。Android 客户端仍通过 FDEX 正式 Release 检查更新。",
        ),
    }
    title, content = pages.get(slug, ("FDEX", "页面不存在"))
    return _render(request, user, "info", info_title=title, info_content=content)
