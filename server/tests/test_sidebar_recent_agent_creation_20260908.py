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


def test_agent_creation_requires_only_name_server_side() -> None:
    routes = _read("server/app/agent_identity_routes.py")
    create = routes.split('@router.post("/employees", response_model=None)', 1)[1].split('@router.get("/employees/{employee_id}.json"', 1)[0]
    assert 'name: str = Form("")' in create
    assert 'description: str = Form("")' in create
    assert 'raise ValueError("请输入智体名称")' in create
    assert 'raise ValueError("请用一句话描述智体")' not in create
    assert '"description": clean_description' in create


def test_agent_editor_keeps_only_name_required_and_ai_prompt_button() -> None:
    script = _read("server/app/static/user_agent_editor.js")
    assert "name.required = true" in script
    assert "description.required = false" in script
    assert "一句话描述智体" in script
    assert "AI整理提示词" in script
    assert "'/account/employees/prompt/organize'" in script
    assert "prompt.value = String(payload.prompt).trim()" in script
    assert "请先填写智体名称。" in script
    assert "请先填写智体名称和一句话描述。" not in script


def test_ai_prompt_organizer_uses_normal_fdex_provider_runtime() -> None:
    routes = _read("server/app/agent_identity_routes.py")
    assert '@router.post("/employees/prompt/organize"' in routes
    assert "await route_text_protocols(" in routes
    assert "智体身份提示词编辑器" in routes
    assert '"prompt": result.content.strip()[:12000]' in routes
    organizer = routes.split('@router.post("/employees/prompt/organize"', 1)[1].split('@router.post("/employees",', 1)[0]
    assert 'return JSONResponse({"ok": False, "error": "请用一句话描述智体"}' not in organizer


def test_agent_list_is_click_to_chat_with_separate_chat_and_edit_actions() -> None:
    script = _read("server/app/static/user_agent_editor.js")
    assert "智体列表" in script
    assert "location.href = chatUrl" in script
    assert "compact-button', '对话'" in script
    assert "compact-button', '编辑'" in script
    assert "fetch(`/account/employees/${id}.json`" in script
    assert "createCard.remove()" in script
    routes = _read("server/app/agent_identity_routes.py")
    assert '@router.get("/employees/{employee_id}.json"' in routes
    update = routes.split('@router.post("/employees/{employee_id}"', 1)[1].split('@router.get("/groups"', 1)[0]
    assert 'description: str = Form("")' in update
    assert '"description": (description or "").strip()[:160]' in update


def test_knowledge_page_becomes_list_first_with_add_modal() -> None:
    script = _read("server/app/static/user_agent_editor.js")
    assert "if (path !== '/account/knowledge') return;" in script
    assert "knowledge-create-modal" in script
    assert "＋ 新增知识" in script
    assert "createCard.remove()" in script
    assert "knowledge-list-card" in script
    css = _read("server/app/static/user_sidebar_recent.css")
    assert ".management-modal" in css
    assert ".management-list-card" in css
    assert ".knowledge-list-card" in css
