from pathlib import Path


def test_web_agent_create_contract_is_prompt_only_for_user() -> None:
    root = Path(__file__).resolve().parents[2]
    js = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    assert "身份定义提示词（可选）" in js
    assert "只需填写身份定义提示词，也可以留空" in js
    assert "['name', 'department', 'position', 'industry', 'knowledge_read', 'knowledge_write', 'coding_agent']" in js
