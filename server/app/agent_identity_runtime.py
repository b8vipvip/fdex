from __future__ import annotations

from typing import Any

from app.web_workspace import WebWorkspaceStore

_LEGACY_AGENT_FIELDS = ("department", "position", "industry")
_LEGACY_PREFERENCE_FIELDS = ("industry", "auto_company_mode")


def next_agent_name(store: WebWorkspaceStore, owner_id: str) -> str:
    """Return a stable user-facing default name without requiring business metadata."""
    names = {
        str(item.get("name") or "").strip()
        for item in store.list(owner_id, "employee", include_deleted=True, limit=5000)
    }
    index = 1
    while f"智体 {index}" in names:
        index += 1
    return f"智体 {index}"


def _general_preferences(self: WebWorkspaceStore, owner_id: str) -> dict[str, Any]:
    try:
        current = self.get(owner_id, "preferences", 1)
    except KeyError:
        return self.upsert(
            owner_id,
            "preferences",
            1,
            {"professional_level": "auto", "default_home": "messages"},
            sort_key="preferences",
        )

    parent_id = current.pop("_parent_id", None)
    deleted = bool(current.pop("_deleted", False))
    changed = False
    for key in _LEGACY_PREFERENCE_FIELDS:
        if key in current:
            current.pop(key, None)
            changed = True
    if not str(current.get("professional_level") or "").strip():
        current["professional_level"] = "auto"
        changed = True
    if not str(current.get("default_home") or "").strip():
        current["default_home"] = "messages"
        changed = True
    if changed:
        return self.upsert(
            owner_id,
            "preferences",
            1,
            current,
            parent_id=parent_id,
            sort_key="preferences",
            deleted=deleted,
        )
    return current


def _general_save_preferences(self: WebWorkspaceStore, owner_id: str, **values: Any) -> dict[str, Any]:
    current = _general_preferences(self, owner_id)
    parent_id = current.pop("_parent_id", None)
    deleted = bool(current.pop("_deleted", False))
    for key in _LEGACY_PREFERENCE_FIELDS:
        current.pop(key, None)
        values.pop(key, None)
    current.update(values)
    return self.upsert(
        owner_id,
        "preferences",
        1,
        current,
        parent_id=parent_id,
        sort_key="preferences",
        deleted=deleted,
    )


def _general_ensure_defaults(self: WebWorkspaceStore, owner_id: str) -> None:
    """Stop seeding company-style employees and scrub obsolete taxonomy from Web records.

    The durable record kind remains ``employee`` for database and route compatibility. Only the
    product model changes: it is presented as a general-purpose 智体 and no longer carries company,
    industry, department or position metadata.
    """
    self.init()
    _general_preferences(self, owner_id)
    for item in self.list(owner_id, "employee", include_deleted=True, limit=5000):
        record_id = int(item["id"])
        parent_id = item.pop("_parent_id", None)
        deleted = bool(item.pop("_deleted", False))
        changed = False
        for key in _LEGACY_AGENT_FIELDS:
            if key in item:
                item.pop(key, None)
                changed = True
        if not str(item.get("name") or "").strip():
            item["name"] = f"智体 {record_id}"
            changed = True
        item.setdefault("role_prompt", "")
        item.setdefault("active", True)
        item.setdefault("knowledge_read", True)
        item.setdefault("knowledge_write", True)
        item.setdefault("coding_agent", False)
        if changed:
            self.upsert(
                owner_id,
                "employee",
                record_id,
                item,
                parent_id=parent_id,
                sort_key=str(item.get("name") or record_id),
                deleted=deleted,
            )


def _install_web_workspace_model() -> None:
    if getattr(WebWorkspaceStore, "_fdex_agent_identity_v1", False):
        return
    WebWorkspaceStore.preferences = _general_preferences  # type: ignore[assignment]
    WebWorkspaceStore.save_preferences = _general_save_preferences  # type: ignore[assignment]
    WebWorkspaceStore.ensure_defaults = _general_ensure_defaults  # type: ignore[assignment]
    WebWorkspaceStore._fdex_agent_identity_v1 = True  # type: ignore[attr-defined]


def _install_account_identity_model() -> None:
    """Keep old auth wire fields compatible, but permanently retire company identity values."""
    from app.central_auth import CentralAuthStore, central_auth_store

    if not getattr(CentralAuthStore, "_fdex_general_identity_v1", False):
        original_register = CentralAuthStore.register

        def register_without_company(self: CentralAuthStore, *args: Any, **kwargs: Any) -> dict[str, object]:
            kwargs["company_name"] = ""
            return original_register(self, *args, **kwargs)

        CentralAuthStore.register = register_without_company  # type: ignore[assignment]
        CentralAuthStore._fdex_general_identity_v1 = True  # type: ignore[attr-defined]

    store = central_auth_store()
    store.init()
    with store.db() as conn:
        conn.execute("UPDATE users SET company_name='' WHERE company_name<>''")


def _install_general_memory_context_protocol() -> None:
    """Accept the neutral wrapper from new clients while retaining old-client compatibility."""
    import re

    from app import memory_middleware

    memory_middleware._LOCAL_CONTEXT = re.compile(
        r"(?s)\s*<fdex_(?:agent|company)_context>\s*(.*?)\s*</fdex_(?:agent|company)_context>\s*"
    )


def _install_general_web_render() -> None:
    from app import user_app_routes as routes

    if getattr(routes._render, "_fdex_agent_identity_v1", False):
        return

    def generalized_render(request: Any, user: dict[str, object], page: str, **extra: object) -> Any:
        return routes.templates.TemplateResponse(
            "user_web_app_general.html",
            routes._ctx(
                request,
                user,
                page=page,
                preferences=routes.web_workspace_store().preferences(routes._owner(user)),
                **extra,
            ),
        )

    generalized_render._fdex_agent_identity_v1 = True  # type: ignore[attr-defined]
    routes._render = generalized_render


def _install_employee_system_prompt() -> None:
    from app import user_app_routes as routes

    if getattr(routes._employee_system, "_fdex_agent_identity_v1", False):
        return

    def generalized_agent_system(agent: dict[str, Any], owner_id: str, prompt: str) -> str:
        knowledge = routes._knowledge_hits(owner_id, prompt, 5) if bool(agent.get("knowledge_read", True)) else []
        knowledge_text = "\n\n".join(
            f"【{item.get('title') or '知识'}】{item.get('summary') or item.get('content') or ''}"[:1600]
            for item in knowledge
        )
        name = str(agent.get("name") or "智体").strip() or "智体"
        identity_prompt = str(agent.get("role_prompt") or "").strip()
        base = (
            f"你是 FDEX 智体 {name}。"
            "智体是用户自定义的通用 AI 身份，可以是老师、学习伙伴、生活助手、创作伙伴、Coding Agent 或其他角色。"
            "只处理用户当前请求；未知事实不要编造。输出自然、清楚、可执行。"
        )
        if identity_prompt:
            base += "\n\n用户为你设置的身份定义提示词：\n" + identity_prompt[:8000]
        if knowledge_text:
            base += "\n\n可参考的当前账号知识：\n" + knowledge_text[:6000]
        return base[:14000]

    generalized_agent_system._fdex_agent_identity_v1 = True  # type: ignore[attr-defined]
    routes._employee_system = generalized_agent_system


def install_agent_identity_runtime() -> None:
    _install_account_identity_model()
    _install_web_workspace_model()
    _install_general_memory_context_protocol()
    _install_general_web_render()
    _install_employee_system_prompt()
