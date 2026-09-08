from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_recent_sidebar_endpoint_collects_agents_and_groups() -> None:
    routes = _read("server/app/agent_identity_routes.py")
    assert '@router.get("/sidebar/recent.json"' in routes
    assert '"kind": "employee"' in routes
    assert '"kind": "group"' in routes
    assert 'items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)' in routes
    assert 'return text[:limit] + ("…" if len(text) > limit else "")' in routes
    assert 'items[:20]' in routes


def test_sidebar_script_renders_name_and_short_summary() -> None:
    script = _read("server/app/static/user_sidebar_recent.js")
    assert "fetch('/account/sidebar/recent.json'" in script
    assert "recent-chat-name" in script
    assert "recent-chat-summary" in script
    assert "MutationObserver" in script
    css = _read("server/app/static/user_sidebar_recent.css")
    assert ".recent-section" in css
    assert ".recent-chat-summary" in css


def test_agent_creation_requires_name_and_description_server_side() -> None:
    routes = _read("server/app/agent_identity_routes.py")
    create = routes.split('@router.post("/employees", response_model=None)', 1)[1].split('@router.post("/employees/{employee_id}"', 1)[0]
    assert 'name: str = Form("")' in create
    assert 'description: str = Form("")' in create
    assert 'raise ValueError("请输入智体名称")' in create
    assert 'raise ValueError("请用一句话描述智体")' in create
    assert '"description": clean_description' in create


def test_agent_editor_injects_required_fields_and_ai_prompt_button() -> None:
    script = _read("server/app/static/user_agent_editor.js")
    assert "name.required = true" in script
    assert "description.required = true" in script
    assert "一句话描述智体" in script
    assert "AI整理提示词" in script
    assert "'/account/employees/prompt/organize'" in script
    assert "prompt.value = String(payload.prompt).trim()" in script


def test_ai_prompt_organizer_uses_normal_fdex_provider_runtime() -> None:
    routes = _read("server/app/agent_identity_routes.py")
    assert '@router.post("/employees/prompt/organize"' in routes
    assert "await route_text_protocols(" in routes
    assert "智体身份提示词编辑器" in routes
    assert '"prompt": result.content.strip()[:12000]' in routes
