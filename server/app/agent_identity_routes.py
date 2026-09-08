from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import SERVER_DIR
from app.provider_protocol_runtime import route_text_protocols
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


def _recent_summary(value: object, limit: int = 10) -> str:
    text = str(value or "")
    text = re.sub(r"\[附件：[^\]]+\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "附件消息"
    return text[:limit] + ("…" if len(text) > limit else "")


@router.get("/sidebar/recent.json", response_model=None)
def recent_sidebar_chats(request: Request) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效"}, status_code=401)
    owner_id = _owner(user)
    store = web_workspace_store()
    store.ensure_defaults(owner_id)
    items: list[dict[str, str]] = []

    for agent in store.list(owner_id, "employee", limit=300):
        if not bool(agent.get("active", True)):
            continue
        messages = store.list(owner_id, "message", parent_id=int(agent["id"]), newest_first=True, limit=1)
        if not messages:
            continue
        latest = messages[0]
        items.append(
            {
                "kind": "employee",
                "name": str(agent.get("name") or "智体")[:80],
                "summary": _recent_summary(latest.get("content")),
                "url": f"/account/chat/employee/{int(agent['id'])}",
                "updated_at": str(latest.get("created_at") or latest.get("updated_at") or ""),
            }
        )

    for group in store.list(owner_id, "group", newest_first=True, limit=300):
        messages = store.list(owner_id, "group_message", parent_id=int(group["id"]), newest_first=True, limit=12)
        latest = next((item for item in messages if str(item.get("role") or "") != "system"), None)
        if latest is None:
            continue
        items.append(
            {
                "kind": "group",
                "name": str(group.get("name") or "工作群")[:100],
                "summary": _recent_summary(latest.get("content")),
                "url": f"/account/chat/group/{int(group['id'])}",
                "updated_at": str(latest.get("created_at") or latest.get("updated_at") or ""),
            }
        )

    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return JSONResponse({"ok": True, "items": items[:20]})


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


@router.post("/employees/prompt/organize", response_model=None)
async def organize_agent_prompt(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    role_prompt: str = Form(""),
) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "error": "登录状态已失效，请重新登录"}, status_code=401)
    try:
        _verify_csrf(request, csrf_token)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    clean_name = (name or "").strip()[:80]
    clean_description = (description or "").strip()[:160]
    clean_prompt = (role_prompt or "").strip()[:12000]
    if not clean_name:
        return JSONResponse({"ok": False, "error": "请输入智体名称"}, status_code=400)
    if not clean_description:
        return JSONResponse({"ok": False, "error": "请用一句话描述智体"}, status_code=400)

    source = clean_prompt or "（当前没有手写提示词，请根据名称和一句话描述生成。）"
    result = await route_text_protocols(
        system=(
            "你是 FDEX 的智体身份提示词编辑器。你的任务是把用户提供的智体名称、一句话用途和现有提示词，"
            "整理成可以直接作为 AI 智体身份定义使用的中文提示词。保留用户原始意图，不虚构用户未提供的业务事实。"
            "提示词应明确身份、核心职责、工作方式、必要边界、遇到信息不足时的处理方式和输出偏好。"
            "不要解释你的修改，不要使用 Markdown 代码块，不要输出标题前缀，只输出整理后的提示词正文。"
        ),
        prompt=(
            f"智体名称：{clean_name}\n"
            f"一句话描述：{clean_description}\n"
            f"现有身份定义提示词：\n{source}\n\n"
            "请整理为简洁但完整的身份定义提示词。"
        ),
        max_tokens=1200,
    )
    if not result.ok or not result.content.strip():
        error = "；".join(str(item) for item in (result.errors or [])[-3:])[:900] or "当前没有可用的 AI 供应商"
        return JSONResponse({"ok": False, "error": f"AI整理提示词失败：{error}"}, status_code=502)
    return JSONResponse(
        {
            "ok": True,
            "prompt": result.content.strip()[:12000],
            "provider": result.provider or "FDEX AI",
            "model": result.model or "",
        }
    )


@router.post("/employees", response_model=None)
def create_agent(
    request: Request,
    csrf_token: str = Form(...),
    role_prompt: str = Form(""),
    name: str = Form(""),
    description: str = Form(""),
) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        display_name = (name or "").strip()[:80]
        clean_description = (description or "").strip()[:160]
        if not display_name:
            raise ValueError("请输入智体名称")
        if not clean_description:
            raise ValueError("请用一句话描述智体")
        store.create(
            owner_id,
            "employee",
            {
                "name": display_name,
                "description": clean_description,
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
            "Web 用户端与 Android 使用同一个 FDEX 中心账号。消息、智体、工作群、知识库、工作项目、GitHub 与 Coding Agent、账号安全均可从侧边导航或底部用户名进入。",
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
