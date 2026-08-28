from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_web_navigation_exposes_agent_center_directly() -> None:
    base = _read("server/app/templates/user_base.html")
    assert 'href="/account/employees"' in base
    assert ">智体</a>" in base


def test_general_web_surface_covers_work_deleted_and_info_without_dom_rewrites() -> None:
    template = _read("server/app/templates/user_web_app_general.html")
    assert "{% elif page == 'work' %}" in template
    assert "{% elif page == 'work_detail' %}" in template
    assert "{% elif page == 'deleted' %}" in template
    assert "{% elif page == 'info' %}" in template
    assert "自动协作" in template
    assert "智体聊天" in template
    for retired in (
        "AI 员工",
        "员工名称",
        "部门",
        "岗位",
        "行业",
        "企业知识库",
        "自动公司模式",
        "自动运营模式",
    ):
        assert retired not in template


def test_web_javascript_only_handles_chat_runtime_not_legacy_copy_rewrites() -> None:
    script = _read("server/app/static/user_chat.js")
    assert "submitAgentChat" in script
    assert "北京时间" in script
    assert "rewriteStructuralTerminology" not in script
    assert "simplifyAgentForms" not in script
    assert "simplifyGeneralSettings" not in script
    assert "replaceAll('AI 员工', '智体')" not in script


def test_server_runtime_uses_neutral_agent_prompt_and_general_template() -> None:
    runtime = _read("server/app/agent_identity_runtime.py")
    assert '"user_web_app_general.html"' in runtime
    assert "你是 FDEX 智体" in runtime
    assert "用户为你设置的身份定义提示词" in runtime
    assert "可参考的当前账号知识" in runtime


def test_general_create_update_routes_precede_legacy_compat_router() -> None:
    main = _read("server/app/main.py")
    identity = main.index("app.include_router(agent_identity_router)")
    legacy = main.index("app.include_router(user_app_router)")
    assert identity < legacy
