from __future__ import annotations

from pathlib import Path

from app.agent_identity_runtime import install_agent_identity_runtime, next_agent_name
from app.web_workspace import WebWorkspaceStore

OWNER = "usr_1234567890abcdef12345678"


def test_workspace_no_longer_seeds_company_employees_or_business_preferences(tmp_path: Path) -> None:
    install_agent_identity_runtime()
    store = WebWorkspaceStore(tmp_path / "workspace.db")
    store.ensure_defaults(OWNER)

    assert store.list(OWNER, "employee") == []
    preferences = store.preferences(OWNER)
    assert preferences["professional_level"] == "auto"
    assert preferences["default_home"] == "messages"
    assert "industry" not in preferences
    assert "auto_company_mode" not in preferences


def test_existing_records_are_migrated_without_losing_identity_or_permissions(tmp_path: Path) -> None:
    install_agent_identity_runtime()
    store = WebWorkspaceStore(tmp_path / "workspace.db")
    legacy = store.create(
        OWNER,
        "employee",
        {
            "name": "语文老师",
            "department": "教学部",
            "position": "老师",
            "industry": "教育",
            "role_prompt": "你是我的语文老师",
            "active": True,
            "knowledge_read": True,
            "knowledge_write": False,
            "coding_agent": False,
        },
        sort_key="语文老师",
    )

    store.ensure_defaults(OWNER)
    migrated = store.get(OWNER, "employee", int(legacy["id"]))
    assert migrated["name"] == "语文老师"
    assert migrated["role_prompt"] == "你是我的语文老师"
    assert migrated["knowledge_read"] is True
    assert migrated["knowledge_write"] is False
    assert "department" not in migrated
    assert "position" not in migrated
    assert "industry" not in migrated


def test_agent_name_is_auto_assigned_and_identity_prompt_may_be_blank(tmp_path: Path) -> None:
    install_agent_identity_runtime()
    store = WebWorkspaceStore(tmp_path / "workspace.db")
    assert next_agent_name(store, OWNER) == "智体 1"
    store.create(OWNER, "employee", {"name": "智体 1", "role_prompt": ""}, sort_key="智体 1")
    assert next_agent_name(store, OWNER) == "智体 2"


def test_generalized_system_prompt_has_no_company_taxonomy(monkeypatch) -> None:
    install_agent_identity_runtime()
    from app import user_app_routes as routes

    monkeypatch.setattr(routes, "_knowledge_hits", lambda *_args, **_kwargs: [])
    system = routes._employee_system(
        {"name": "数学老师", "role_prompt": "你是我的数学老师", "knowledge_read": True},
        OWNER,
        "讲一下二次函数",
    )
    assert "FDEX 智体 数学老师" in system
    assert "你是我的数学老师" in system
    assert "部门" not in system
    assert "职位" not in system
    assert "行业" not in system
    assert "企业知识" not in system


def test_web_registration_and_agent_ui_do_not_collect_company_taxonomy() -> None:
    root = Path(__file__).resolve().parents[2]
    register = (root / "server/app/templates/user_register.html").read_text(encoding="utf-8")
    js = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    routes = (root / "server/app/agent_identity_routes.py").read_text(encoding="utf-8")

    assert 'name="company_name"' not in register
    assert "公司 / 团队" not in register
    assert "身份定义提示词（可选）" in js
    assert "创建智体" in js
    assert "['name', 'department', 'position', 'industry'" in js
    assert 'role_prompt: str = Form("")' in routes
    assert 'name: str = Form("")' in routes
    assert '"department"' not in routes.split("store.create", 1)[1].split("sort_key", 1)[0]
