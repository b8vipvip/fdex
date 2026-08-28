from pathlib import Path


def test_web_agent_create_contract_is_prompt_only_for_user() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/user_web_app_general.html").read_text(encoding="utf-8")
    js = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    create_section = template.split('<form action="/account/employees"', 1)[1].split("</form>", 1)[0]

    assert "身份定义提示词（可选）" in create_section
    assert "也可以留空" in template
    assert 'name="role_prompt"' in create_section
    assert 'name="department"' not in create_section
    assert 'name="position"' not in create_section
    assert 'name="industry"' not in create_section
    assert 'name="knowledge_read"' not in create_section
    assert 'name="knowledge_write"' not in create_section
    assert 'name="coding_agent"' not in create_section
    assert "simplifyAgentForms" not in js
