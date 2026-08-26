from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.account_cleanup import purge_owned_agent_resources
from app.account_data_export import build_account_export
from app.account_operations import AccountOperationBusy, account_operation, advance_memory_generation, mark_account_deleted
from app.central_auth import central_auth_store
from app.client_ai import AIRequest, AudioInput, DocumentInput, ImageInput, client_ai
from app.config import SERVER_DIR, fresh_settings
from app.memory_erasure import MemoryErasureError, erase_account_memory, memory_erasure_status
from app.memory_scope_registry import memory_scope_registry
from app.user_portal_routes import _ctx, _current_user, _flash, _login_redirect, _verify_csrf
from app.web_workspace import web_workspace_store

router = APIRouter(prefix="/account", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))

_MAX_WEB_UPLOAD = 12 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user_or_redirect(request: Request) -> tuple[dict[str, object] | None, Response | None]:
    user = _current_user(request)
    if user is None:
        return None, _login_redirect(request)
    return user, None


def _owner(user: dict[str, object]) -> str:
    return str(user["id"])


def _store_for(user: dict[str, object]):
    store = web_workspace_store()
    store.ensure_defaults(_owner(user))
    return store


def _render(request: Request, user: dict[str, object], page: str, **extra: object) -> Response:
    return templates.TemplateResponse(
        "user_web_app.html",
        _ctx(request, user, page=page, preferences=web_workspace_store().preferences(_owner(user)), **extra),
    )


def _clean_text(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _knowledge_hits(owner_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
    store = web_workspace_store()
    entries = store.list(owner_id, "knowledge", newest_first=True, limit=500)
    clean = (query or "").strip().lower()
    if not clean:
        return entries[:limit]
    tokens = [item for item in re.split(r"\s+|[,，。；;：:、]+", clean) if item]
    if not tokens:
        tokens = [clean]
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        haystack = " ".join(
            str(entry.get(key) or "")
            for key in ("title", "summary", "content", "keywords", "source_employee")
        ).lower()
        score = sum(3 if token in str(entry.get("title") or "").lower() else 1 for token in tokens if token in haystack)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], -int(item[1]["id"])))
    return [entry for _, entry in scored[:limit]]


def _conversation_context(messages: list[dict[str, Any]], limit_chars: int = 9000) -> str:
    parts: list[str] = []
    for item in messages[-24:]:
        role = "用户" if str(item.get("role")) == "user" else "AI"
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(f"{role}：{content}")
    text = "\n".join(parts)
    return text[-limit_chars:]


def _employee_system(employee: dict[str, Any], owner_id: str, prompt: str) -> str:
    knowledge = _knowledge_hits(owner_id, prompt, 5) if bool(employee.get("knowledge_read", True)) else []
    knowledge_text = "\n\n".join(
        f"【{item.get('title') or '知识'}】{item.get('summary') or item.get('content') or ''}"[:1600]
        for item in knowledge
    )
    role_prompt = str(employee.get("role_prompt") or "").strip()
    base = (
        f"你是 FDEX AI 员工 {employee.get('name')}，职位是 {employee.get('department')} / {employee.get('position')}。"
        "只处理用户当前请求；未知事实不要编造。输出自然、清楚、可执行。"
    )
    if role_prompt:
        base += "\n\n你的岗位说明：\n" + role_prompt[:6000]
    if knowledge_text:
        base += "\n\n可参考的当前账号企业知识：\n" + knowledge_text[:6000]
    return base[:12000]


def _capture_knowledge(owner_id: str, employee: dict[str, Any], user_text: str, answer: str) -> None:
    if not bool(employee.get("knowledge_write", True)):
        return
    user_text = user_text.strip()
    answer = answer.strip()
    if not user_text or not answer:
        return
    if len(user_text) < 3 and len(answer) < 20:
        return
    store = web_workspace_store()
    title = user_text.replace("\n", " ")[:80] or f"与 {employee.get('name')} 的对话"
    store.create(
        owner_id,
        "knowledge",
        {
            "title": title,
            "summary": answer.replace("\n", " ")[:360],
            "content": f"用户：{user_text}\nAI：{answer}"[:8000],
            "keywords": [],
            "room": "work",
            "source": "chat",
            "source_employee": str(employee.get("name") or ""),
            "shared_for_agents": True,
            "created_at": _now(),
        },
        sort_key=_now(),
    )


async def _attachment_inputs(upload: UploadFile | None) -> tuple[list[ImageInput], AudioInput | None, list[DocumentInput], str]:
    if upload is None or not upload.filename:
        return [], None, [], ""
    raw = await upload.read(_MAX_WEB_UPLOAD + 1)
    if len(raw) > _MAX_WEB_UPLOAD:
        raise ValueError("单个 Web 聊天附件不能超过 12 MB")
    if not raw:
        return [], None, [], ""
    mime = (upload.content_type or "application/octet-stream").lower()
    encoded = base64.b64encode(raw).decode("ascii")
    name = Path(upload.filename).name[:200]
    if mime.startswith("image/"):
        return [ImageInput(url=f"data:{mime};base64,{encoded}")], None, [], name
    if mime in {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"}:
        fmt = "mp3" if "mpeg" in mime or "mp3" in mime else "wav"
        return [], AudioInput(data=encoded, format=fmt), [], name
    return [], None, [DocumentInput(name=name, mime_type=mime, data=encoded)], name


async def _ask_employee(
    request: Request,
    owner_id: str,
    employee: dict[str, Any],
    prompt: str,
    history: list[dict[str, Any]],
    upload: UploadFile | None = None,
) -> str:
    images, audio, documents, attachment_name = await _attachment_inputs(upload)
    display_prompt = prompt.strip()
    if attachment_name:
        display_prompt = (display_prompt + f"\n[附件：{attachment_name}]").strip()
    request.scope["fdex_user_id"] = owner_id
    request.scope["fdex_user"] = {"id": owner_id}
    contextual = _conversation_context(history)
    effective_prompt = prompt.strip()
    if contextual:
        effective_prompt = f"最近会话：\n{contextual}\n\n当前用户请求：\n{effective_prompt}".strip()
    result = await client_ai(
        request,
        AIRequest(
            system=_employee_system(employee, owner_id, prompt),
            prompt=effective_prompt,
            max_tokens=1600,
            task="auto",
            images=images,
            audio=audio,
            documents=documents,
        ),
    )
    answer = result.content.strip()
    if result.media:
        media_lines = [f"[{item.kind}] {item.url}" for item in result.media if item.url]
        if media_lines:
            answer = (answer + "\n" + "\n".join(media_lines)).strip()
    return answer


@router.get("/messages", response_class=HTMLResponse, response_model=None)
def messages_page(request: Request, query: str = "") -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    owner_id = _owner(user)
    clean = query.strip().lower()[:100]
    employees = [item for item in store.list(owner_id, "employee") if bool(item.get("active", True))]
    groups = store.list(owner_id, "group", newest_first=True)
    if clean:
        employees = [item for item in employees if clean in f"{item.get('name')} {item.get('department')} {item.get('position')}".lower()]
        groups = [item for item in groups if clean in f"{item.get('name')} {item.get('description')}".lower()]
    for employee in employees:
        last = store.list(owner_id, "message", parent_id=int(employee["id"]), newest_first=True, limit=1)
        employee["last_message"] = str(last[0].get("content") or "") if last else "开始与 AI 员工沟通"
    for group in groups:
        last = store.list(owner_id, "group_message", parent_id=int(group["id"]), newest_first=True, limit=1)
        group["last_message"] = str(last[0].get("content") or "") if last else "工作群已创建"
    return _render(request, user, "messages", employees=employees, groups=groups, query=query[:100])


@router.get("/employees", response_class=HTMLResponse, response_model=None)
def employees_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    return _render(request, user, "employees", employees=store.list(_owner(user), "employee", include_deleted=True))


@router.post("/employees", response_model=None)
def employee_create(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    department: str = Form(""),
    position: str = Form(""),
    role_prompt: str = Form(""),
    industry: str = Form(""),
    knowledge_read: bool = Form(False),
    knowledge_write: bool = Form(False),
    coding_agent: bool = Form(False),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        if not name.strip():
            raise ValueError("请输入员工名称")
        web_workspace_store().create(
            _owner(user),
            "employee",
            {
                "name": _clean_text(name, 80),
                "department": _clean_text(department, 100),
                "position": _clean_text(position, 100),
                "role_prompt": _clean_text(role_prompt, 12000),
                "industry": _clean_text(industry, 100),
                "active": True,
                "knowledge_read": knowledge_read,
                "knowledge_write": knowledge_write,
                "coding_agent": coding_agent,
            },
            sort_key=name.strip().lower(),
        )
        _flash(request, "AI 员工已创建", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/employees", status_code=303)


@router.post("/employees/{employee_id}", response_model=None)
def employee_update(
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    department: str = Form(""),
    position: str = Form(""),
    role_prompt: str = Form(""),
    industry: str = Form(""),
    active: bool = Form(False),
    knowledge_read: bool = Form(False),
    knowledge_write: bool = Form(False),
    coding_agent: bool = Form(False),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        current = web_workspace_store().get(_owner(user), "employee", employee_id, include_deleted=True)
        current.update(
            {
                "name": _clean_text(name, 80),
                "department": _clean_text(department, 100),
                "position": _clean_text(position, 100),
                "role_prompt": _clean_text(role_prompt, 12000),
                "industry": _clean_text(industry, 100),
                "active": active,
                "knowledge_read": knowledge_read,
                "knowledge_write": knowledge_write,
                "coding_agent": coding_agent,
            }
        )
        current.pop("_parent_id", None)
        current.pop("_deleted", None)
        web_workspace_store().upsert(_owner(user), "employee", employee_id, current, sort_key=str(current.get("name") or ""))
        _flash(request, "员工资料与权限已保存", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/employees", status_code=303)


@router.get("/chat/employee/{employee_id}", response_class=HTMLResponse, response_model=None)
def employee_chat_page(employee_id: int, request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    try:
        employee = store.get(_owner(user), "employee", employee_id)
    except KeyError:
        return RedirectResponse("/account/messages", status_code=303)
    history = store.list(_owner(user), "message", parent_id=employee_id, limit=500)
    return _render(request, user, "employee_chat", employee=employee, messages=history)


@router.post("/chat/employee/{employee_id}/send", response_model=None)
async def employee_chat_send(
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    message: str = Form(""),
    attachment: UploadFile | None = File(default=None),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    try:
        _verify_csrf(request, csrf_token)
        employee = store.get(owner_id, "employee", employee_id)
        if not message.strip() and (attachment is None or not attachment.filename):
            raise ValueError("请输入消息或选择附件")
        history = store.list(owner_id, "message", parent_id=employee_id, limit=500)
        display = message.strip()
        if attachment is not None and attachment.filename:
            display = (display + f"\n[附件：{Path(attachment.filename).name[:200]}]").strip()
        store.create(owner_id, "message", {"employee_id": employee_id, "role": "user", "content": display, "created_at": _now()}, parent_id=employee_id, sort_key=_now())
        answer = await _ask_employee(request, owner_id, employee, message, history, attachment)
        store.create(owner_id, "message", {"employee_id": employee_id, "role": "assistant", "content": answer, "created_at": _now()}, parent_id=employee_id, sort_key=_now())
        _capture_knowledge(owner_id, employee, display, answer)
    except HTTPException as exc:
        _flash(request, str(exc.detail), "error")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/chat/employee/{employee_id}", status_code=303)


@router.post("/chat/employee/{employee_id}/clear", response_model=None)
def employee_chat_clear(employee_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        store = web_workspace_store()
        store.get(_owner(user), "employee", employee_id)
        for item in store.list(_owner(user), "message", parent_id=employee_id, include_deleted=True, limit=5000):
            if not bool(item.get("_deleted")):
                store.set_deleted(_owner(user), "message", int(item["id"]), True)
        _flash(request, "聊天记录已移入最近删除", "success")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/chat/employee/{employee_id}", status_code=303)


@router.post("/messages/{message_id}/delete", response_model=None)
def message_delete(message_id: int, request: Request, csrf_token: str = Form(...), employee_id: int = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        item = web_workspace_store().get(_owner(user), "message", message_id)
        if int(item.get("employee_id") or item.get("_parent_id") or 0) != int(employee_id):
            raise ValueError("消息不属于当前会话")
        web_workspace_store().set_deleted(_owner(user), "message", message_id, True)
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/chat/employee/{employee_id}", status_code=303)


@router.get("/groups", response_class=HTMLResponse, response_model=None)
def groups_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    owner_id = _owner(user)
    employees = [item for item in store.list(owner_id, "employee") if bool(item.get("active", True))]
    groups = store.list(owner_id, "group", newest_first=True)
    return _render(request, user, "groups", employees=employees, groups=groups)


@router.post("/groups", response_model=None)
def group_create(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    member_ids: list[int] = Form(default=[]),
    auto_mode: bool = Form(False),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    try:
        _verify_csrf(request, csrf_token)
        valid_ids = {int(item["id"]) for item in store.list(owner_id, "employee") if bool(item.get("active", True))}
        selected = [int(value) for value in member_ids if int(value) in valid_ids]
        if not name.strip():
            raise ValueError("请输入工作群名称")
        if not selected:
            raise ValueError("至少选择 1 名 AI 员工")
        group = store.create(
            owner_id,
            "group",
            {
                "name": _clean_text(name, 100),
                "description": _clean_text(description, 1000),
                "member_ids": selected,
                "auto_mode": auto_mode,
                "created_at": _now(),
                "updated_at": _now(),
            },
            sort_key=_now(),
        )
        store.create(owner_id, "group_message", {"group_id": int(group["id"]), "role": "system", "employee_name": "", "content": "工作群已创建。", "created_at": _now()}, parent_id=int(group["id"]), sort_key=_now())
        return RedirectResponse(f"/account/chat/group/{group['id']}", status_code=303)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/groups", status_code=303)


@router.get("/chat/group/{group_id}", response_class=HTMLResponse, response_model=None)
def group_chat_page(group_id: int, request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    owner_id = _owner(user)
    try:
        group = store.get(owner_id, "group", group_id)
    except KeyError:
        return RedirectResponse("/account/groups", status_code=303)
    employees = {int(item["id"]): item for item in store.list(owner_id, "employee")}
    members = [employees[item_id] for item_id in group.get("member_ids", []) if int(item_id) in employees]
    history = store.list(owner_id, "group_message", parent_id=group_id, limit=1000)
    return _render(request, user, "group_chat", group=group, members=members, messages=history)


@router.post("/chat/group/{group_id}/send", response_model=None)
async def group_chat_send(group_id: int, request: Request, csrf_token: str = Form(...), message: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    try:
        _verify_csrf(request, csrf_token)
        clean = message.strip()
        if not clean:
            raise ValueError("请输入群消息")
        group = store.get(owner_id, "group", group_id)
        store.create(owner_id, "group_message", {"group_id": group_id, "role": "user", "employee_name": "", "content": clean, "created_at": _now()}, parent_id=group_id, sort_key=_now())
        history = store.list(owner_id, "group_message", parent_id=group_id, limit=500)
        employee_map = {int(item["id"]): item for item in store.list(owner_id, "employee") if bool(item.get("active", True))}
        members = [employee_map[int(item_id)] for item_id in group.get("member_ids", []) if int(item_id) in employee_map][:8]
        for employee in members:
            pseudo_history = [
                {"role": item.get("role"), "content": f"{item.get('employee_name') + '：' if item.get('employee_name') else ''}{item.get('content') or ''}"}
                for item in history[-20:]
            ]
            answer = await _ask_employee(request, owner_id, employee, f"你正在工作群“{group.get('name')}”中。用户刚说：{clean}", pseudo_history)
            store.create(owner_id, "group_message", {"group_id": group_id, "role": "assistant", "employee_name": str(employee.get("name") or "AI"), "content": answer, "created_at": _now()}, parent_id=group_id, sort_key=_now())
            _capture_knowledge(owner_id, employee, clean, answer)
    except HTTPException as exc:
        _flash(request, str(exc.detail), "error")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/chat/group/{group_id}", status_code=303)


@router.get("/knowledge", response_class=HTMLResponse, response_model=None)
def knowledge_page(request: Request, query: str = "", room: str = "all") -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    entries = _knowledge_hits(owner_id, query, 200) if query.strip() else store.list(owner_id, "knowledge", newest_first=True, limit=500)
    if room != "all":
        entries = [item for item in entries if str(item.get("room") or "general") == room]
    projects = store.list(owner_id, "project", newest_first=True, limit=100)
    return _render(request, user, "knowledge", entries=entries, projects=projects, query=query[:200], room=room[:40])


@router.post("/knowledge", response_model=None)
def knowledge_add(
    request: Request,
    csrf_token: str = Form(...),
    title: str = Form(""),
    content: str = Form(...),
    keywords: str = Form(""),
    room: str = Form("general"),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        clean = content.strip()
        if not clean:
            raise ValueError("请输入知识内容")
        web_workspace_store().create(
            _owner(user),
            "knowledge",
            {
                "title": _clean_text(title, 120) or clean.replace("\n", " ")[:80],
                "summary": clean.replace("\n", " ")[:360],
                "content": clean[:12000],
                "keywords": [item.strip()[:80] for item in re.split(r"[,，]", keywords) if item.strip()][:30],
                "room": room if room in {"general", "work", "customer", "product", "operation", "casual"} else "general",
                "source": "manual",
                "source_employee": "",
                "shared_for_agents": True,
                "created_at": _now(),
            },
            sort_key=_now(),
        )
        _flash(request, "知识已保存", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/knowledge", status_code=303)


@router.post("/knowledge/{entry_id}/archive", response_model=None)
def knowledge_archive(entry_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        web_workspace_store().set_deleted(_owner(user), "knowledge", entry_id, True)
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/knowledge", status_code=303)


@router.get("/work", response_class=HTMLResponse, response_model=None)
def work_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    return _render(request, user, "work", projects=store.list(_owner(user), "project", newest_first=True, limit=300))


@router.post("/work", response_model=None)
def work_create(
    request: Request,
    csrf_token: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    professional_level: str = Form("business"),
    storage_mode: str = Form("center"),
    retention_policy: str = Form("keep"),
    allow_third_party_ai: bool = Form(False),
    auto_desensitize: bool = Form(False),
    auto_operation: bool = Form(False),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        if not title.strip():
            raise ValueError("请输入工作标题")
        now = _now()
        project = web_workspace_store().create(
            _owner(user),
            "project",
            {
                "title": _clean_text(title, 160),
                "description": _clean_text(description, 6000),
                "professional_level": _clean_text(professional_level, 40),
                "storage_mode": _clean_text(storage_mode, 40),
                "retention_policy": _clean_text(retention_policy, 40),
                "allow_third_party_ai": allow_third_party_ai,
                "auto_desensitize": auto_desensitize,
                "status": "created",
                "requirement_score": min(90, 25 + len(description.strip()) // 80),
                "auto_operation": auto_operation,
                "created_at": now,
                "updated_at": now,
            },
            sort_key=now,
        )
        return RedirectResponse(f"/account/work/{project['id']}", status_code=303)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/account/work", status_code=303)


@router.get("/work/{project_id}", response_class=HTMLResponse, response_model=None)
def work_detail(project_id: int, request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    store = _store_for(user)
    owner_id = _owner(user)
    try:
        project = store.get(owner_id, "project", project_id)
    except KeyError:
        return RedirectResponse("/account/work", status_code=303)
    return _render(
        request,
        user,
        "work_detail",
        project=project,
        notes=store.list(owner_id, "project_note", parent_id=project_id, newest_first=True),
        assets=store.list(owner_id, "project_asset", parent_id=project_id, newest_first=True),
        reports=store.list(owner_id, "report", parent_id=project_id, newest_first=True),
    )


@router.post("/work/{project_id}/notes", response_model=None)
def work_note_add(project_id: int, request: Request, csrf_token: str = Form(...), content: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    try:
        _verify_csrf(request, csrf_token)
        store = web_workspace_store()
        project = store.get(owner_id, "project", project_id)
        if not content.strip():
            raise ValueError("请输入补充要求")
        store.create(owner_id, "project_note", {"project_id": project_id, "content": _clean_text(content, 12000), "created_at": _now()}, parent_id=project_id, sort_key=_now())
        project["requirement_score"] = min(100, int(project.get("requirement_score") or 25) + 5)
        project["updated_at"] = _now()
        project.pop("_parent_id", None)
        project.pop("_deleted", None)
        store.upsert(owner_id, "project", project_id, project, sort_key=str(project["updated_at"]))
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/work/{project_id}", status_code=303)


@router.post("/work/{project_id}/assets", response_model=None)
async def work_asset_upload(project_id: int, request: Request, csrf_token: str = Form(...), asset: UploadFile = File(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = web_workspace_store()
    try:
        _verify_csrf(request, csrf_token)
        store.get(owner_id, "project", project_id)
        raw = await asset.read(_MAX_WEB_UPLOAD + 1)
        if len(raw) > _MAX_WEB_UPLOAD:
            raise ValueError("单个工作资料不能超过 12 MB")
        if not raw:
            raise ValueError("上传文件为空")
        cfg = fresh_settings()
        owner_dir = hashlib.sha256(owner_id.encode()).hexdigest()[:24]
        base = Path(cfg.app_dir).expanduser().resolve() / "server" / "data" / "web-assets" / owner_dir / str(project_id)
        base.mkdir(parents=True, exist_ok=True)
        safe_name = _SAFE_NAME.sub("_", Path(asset.filename or "asset.bin").name)[:160] or "asset.bin"
        storage_name = f"{secrets.token_hex(8)}-{safe_name}"
        path = (base / storage_name).resolve()
        if base not in path.parents:
            raise ValueError("文件名无效")
        path.write_bytes(raw)
        record = store.create(
            owner_id,
            "project_asset",
            {
                "project_id": project_id,
                "name": safe_name,
                "size": len(raw),
                "mime_type": asset.content_type or "application/octet-stream",
                "status": "uploaded",
                "privacy_decision": "center_private",
                "analysis": "",
                "storage_path": str(path),
                "created_at": _now(),
            },
            parent_id=project_id,
            sort_key=_now(),
        )
        _flash(request, f"资料已上传：{record['name']}", "success")
    except (KeyError, ValueError, OSError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/work/{project_id}", status_code=303)


@router.get("/work/{project_id}/assets/{asset_id}/download", response_model=None)
def work_asset_download(project_id: int, asset_id: int, request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    try:
        record = web_workspace_store().get(owner_id, "project_asset", asset_id)
        if int(record.get("project_id") or record.get("_parent_id") or 0) != project_id:
            raise ValueError("资料不属于当前工作")
        path = Path(str(record.get("storage_path") or "")).resolve()
        cfg = fresh_settings()
        owner_dir = hashlib.sha256(owner_id.encode()).hexdigest()[:24]
        allowed = (Path(cfg.app_dir).expanduser().resolve() / "server" / "data" / "web-assets" / owner_dir).resolve()
        if allowed not in path.parents or not path.is_file():
            raise ValueError("资料文件不存在")
        filename = str(record.get("name") or "download.bin").replace('"', "")
        return Response(
            path.read_bytes(),
            media_type=str(record.get("mime_type") or "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except (KeyError, ValueError, OSError) as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/account/work/{project_id}", status_code=303)


@router.post("/work/{project_id}/report", response_model=None)
async def work_generate_report(project_id: int, request: Request, csrf_token: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    try:
        _verify_csrf(request, csrf_token)
        project = store.get(owner_id, "project", project_id)
        notes = store.list(owner_id, "project_note", parent_id=project_id)
        prompt = (
            f"工作标题：{project.get('title')}\n工作说明：{project.get('description')}\n"
            + "\n".join(f"补充要求：{item.get('content')}" for item in notes[-20:])
            + "\n\n请生成一份结构清晰、可执行的阶段方案/报告，包含目标、现状、关键风险、实施步骤和下一步。"
        )
        request.scope["fdex_user_id"] = owner_id
        result = await client_ai(request, AIRequest(system="你是 FDEX 工作项目分析助手。不要编造未提供的事实。", prompt=prompt[:40000], max_tokens=2200))
        report = store.create(owner_id, "report", {"project_id": project_id, "title": f"{project.get('title')} · AI 报告", "content": result.content, "created_at": _now()}, parent_id=project_id, sort_key=_now())
        project["status"] = "generated"
        project["updated_at"] = _now()
        project.pop("_parent_id", None)
        project.pop("_deleted", None)
        store.upsert(owner_id, "project", project_id, project, sort_key=str(project["updated_at"]))
        _flash(request, f"AI 报告已生成：{report['title']}", "success")
    except HTTPException as exc:
        _flash(request, str(exc.detail), "error")
    except (KeyError, ValueError) as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse(f"/account/work/{project_id}", status_code=303)


@router.get("/discover", response_class=HTMLResponse, response_model=None)
def discover_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    return _render(request, user, "discover")


@router.get("/me", response_class=HTMLResponse, response_model=None)
def me_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    stats = {
        "employees": len(store.list(owner_id, "employee")),
        "groups": len(store.list(owner_id, "group")),
        "knowledge": len(store.list(owner_id, "knowledge")),
        "projects": len(store.list(owner_id, "project")),
    }
    return _render(request, user, "me", stats=stats)


@router.get("/settings", response_class=HTMLResponse, response_model=None)
def settings_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    return _render(request, user, "settings")


@router.post("/settings", response_model=None)
def settings_save(
    request: Request,
    csrf_token: str = Form(...),
    industry: str = Form(""),
    professional_level: str = Form("business"),
    default_home: str = Form("messages"),
    auto_company_mode: bool = Form(False),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        if default_home not in {"messages", "knowledge", "discover", "me"}:
            default_home = "messages"
        web_workspace_store().save_preferences(
            _owner(user),
            industry=_clean_text(industry, 120),
            professional_level=_clean_text(professional_level, 40),
            default_home=default_home,
            auto_company_mode=auto_company_mode,
        )
        _flash(request, "Web 用户设置已保存", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/settings", status_code=303)


@router.get("/security", response_class=HTMLResponse, response_model=None)
def security_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    sessions = central_auth_store().list_sessions(owner_id)
    current = str(user.get("session_id") or "")
    for item in sessions:
        item["current"] = str(item.get("id") or "") == current
    return _render(
        request,
        user,
        "security",
        sessions=sessions,
        security_events=central_auth_store().security_events(owner_id, limit=30),
        memory_status=memory_erasure_status(owner_id),
        registered_scopes=memory_scope_registry().scope_count(owner_id),
    )


@router.post("/security/password", response_model=None)
def security_change_password(
    request: Request,
    csrf_token: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        if new_password != confirm_password:
            raise ValueError("两次输入的新密码不一致")
        revoked = central_auth_store().change_password(_owner(user), current_password, new_password, current_session_id=str(user.get("session_id") or ""))
        _flash(request, f"密码已修改，已注销其它 {revoked} 个 Session", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/security", status_code=303)


@router.post("/security/sessions/{session_id}/revoke", response_model=None)
def security_revoke_session(session_id: str, request: Request, csrf_token: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        if not session_id.startswith("ses_"):
            raise ValueError("Session ID 无效")
        if not central_auth_store().revoke_session(_owner(user), session_id):
            raise ValueError("Session 不存在或已经注销")
        if session_id == str(user.get("session_id") or ""):
            request.session.clear()
            return RedirectResponse("/account/login", status_code=303)
        _flash(request, "该设备 Session 已注销", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/security", status_code=303)


@router.post("/security/logout-all", response_model=None)
def security_logout_all(request: Request, csrf_token: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        central_auth_store().revoke_user_sessions(_owner(user))
    finally:
        request.session.clear()
    return RedirectResponse("/account/login", status_code=303)


@router.post("/security/memory/clear", response_model=None)
def security_clear_memory(
    request: Request,
    csrf_token: str = Form(...),
    password: str = Form(...),
    confirmation: str = Form(""),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    try:
        _verify_csrf(request, csrf_token)
        if confirmation.strip() != "CLEAR MY FDEX MEMORY":
            raise ValueError("请输入 CLEAR MY FDEX MEMORY 确认")
        with account_operation(owner_id, "memory_clear"):
            if not central_auth_store().verify_password(owner_id, password):
                raise ValueError("密码错误，无法清空长期记忆")
            asyncio.run(erase_account_memory(owner_id))
            advance_memory_generation(owner_id)
        _flash(request, "服务器 MemPalace / Qdrant / Letta 长期记忆已清空", "success")
    except AccountOperationBusy:
        _flash(request, "当前账号正在执行其它数据操作，请稍后重试", "error")
    except MemoryErasureError as exc:
        _flash(request, f"远程长期记忆尚未完全清除：{exc.code}", "error")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/security", status_code=303)


@router.get("/security/data-export", response_model=None)
def security_data_export(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    try:
        with account_operation(owner_id, "data_export"):
            payload = build_account_export(owner_id)
            payload["web_workspace"] = {
                kind: web_workspace_store().list(owner_id, kind, include_deleted=True, limit=5000)
                for kind in ("employee", "message", "group", "group_message", "knowledge", "project", "project_note", "project_asset", "report", "preferences")
            }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=fdex-data-export.json"},
        )
    except AccountOperationBusy:
        _flash(request, "当前账号正在执行其它数据操作，请稍后重试", "error")
        return RedirectResponse("/account/security", status_code=303)


@router.post("/security/account/delete", response_model=None)
def security_delete_account(
    request: Request,
    csrf_token: str = Form(...),
    password: str = Form(...),
    confirmation: str = Form(""),
) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    try:
        _verify_csrf(request, csrf_token)
        if confirmation.strip() != "DELETE MY FDEX":
            raise ValueError("请输入 DELETE MY FDEX 确认注销")
        with account_operation(owner_id, "account_delete"):
            if not central_auth_store().verify_password(owner_id, password):
                raise ValueError("密码错误，无法注销账号")
            cleanup = purge_owned_agent_resources(owner_id)
            web_workspace_store().clear_owner(owner_id)
            central_auth_store().delete_account(owner_id, password)
            mark_account_deleted(owner_id)
        request.session.clear()
        return RedirectResponse("/account/login?deleted=1", status_code=303)
    except AccountOperationBusy:
        _flash(request, "当前账号正在执行其它数据操作，请稍后重试", "error")
    except MemoryErasureError as exc:
        _flash(request, f"远程长期记忆清理失败，账号尚未注销：{exc.code}", "error")
    except (ValueError, RuntimeError) as exc:
        _flash(request, str(exc), "error")
    except Exception:
        _flash(request, "账号资源清理失败，账号尚未注销", "error")
    return RedirectResponse("/account/security", status_code=303)


@router.get("/deleted", response_class=HTMLResponse, response_model=None)
def deleted_page(request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    owner_id = _owner(user)
    store = _store_for(user)
    deleted_messages = [item for item in store.list(owner_id, "message", include_deleted=True, newest_first=True, limit=500) if bool(item.get("_deleted"))]
    deleted_group_messages = [item for item in store.list(owner_id, "group_message", include_deleted=True, newest_first=True, limit=500) if bool(item.get("_deleted"))]
    return _render(request, user, "deleted", deleted_messages=deleted_messages, deleted_group_messages=deleted_group_messages)


@router.post("/deleted/restore", response_model=None)
def deleted_restore(request: Request, csrf_token: str = Form(...)) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    try:
        _verify_csrf(request, csrf_token)
        count = web_workspace_store().restore_all_deleted_messages(_owner(user))
        _flash(request, f"已恢复 {count} 条聊天记录", "success")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return RedirectResponse("/account/deleted", status_code=303)


@router.get("/info/{slug}", response_class=HTMLResponse, response_model=None)
def info_page(slug: str, request: Request) -> Response:
    user, redirect = _user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    pages = {
        "guide": ("使用说明", "Web 用户端与 Android 使用同一个 FDEX 中心账号。消息、AI 员工、工作群、知识库、工作项目、GitHub 与 Coding Agent、账号安全均可从顶部导航或“我的”进入。"),
        "privacy": ("隐私说明", "FDEX 中心账号、GitHub/Coding Agent 与远程长期记忆按 user_id 隔离。Web 工作区数据保存在中心服务端账号空间；密码、GitHub 安装 Token 与 AI API Key 不会作为 Web 工作区内容保存。"),
        "contact": ("联系我们", "如遇到登录、邮件验证码、GitHub App、Coding Agent 或数据操作问题，请先保留页面提示与服务端运行日志，再联系 FDEX 管理员。"),
        "update": ("版本与更新", "Web 用户端随中心服务端版本更新，无需单独下载安装。Android 客户端仍通过 FDEX 正式 Release 检查更新。"),
    }
    title, content = pages.get(slug, ("FDEX", "页面不存在"))
    return _render(request, user, "info", info_title=title, info_content=content)
