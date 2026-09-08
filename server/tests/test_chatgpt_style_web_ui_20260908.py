from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_user_shell_uses_chatgpt_style_sidebar_navigation() -> None:
    base = _read("server/app/templates/user_base.html")
    assert 'class="user-sidebar"' in base
    assert 'class="new-chat-link"' in base
    assert '>新建智体</span>' in base
    assert 'href="/account/employees?new=1#new-agent"' in base
    assert 'class="user-workspace"' in base
    assert 'class="user-topbar"' in base
    assert 'data-sidebar-toggle' in base
    assert 'fdex-user-sidebar-closed' in base
    assert 'id="fdex-recent-chat-list"' in base
    assert '<div class="recent-title">最近</div>' in base
    assert 'nav-group-title">聊天' not in base
    assert 'nav-group-title">工作区' not in base
    assert '<a href="/account/messages"' not in base
    for href in (
        "/account/employees",
        "/account/groups",
        "/account/knowledge",
        "/account/work",
        "/account/agent",
        "/account/agent/inputs",
        "/account/github",
        "/account/discover",
    ):
        assert f'href="{href}"' in base
    # “我的” no longer occupies a navigation row; the profile at the bottom remains the settings entry.
    assert 'class="account-profile" href="/account/me"' in base


def test_chat_route_uses_centered_chatgpt_message_canvas_and_floating_composer() -> None:
    css = _read("server/app/static/user_portal.css")
    assert "--sidebar:#171717" in css
    assert "--bg:#212121" in css
    assert "--chat-width:768px" in css
    assert ".is-chat-route .page>.hero{display:none}" in css
    assert ".is-chat-route .chat-bubble.assistant" in css
    assert ".is-chat-route .chat-bubble.user" in css
    assert ".is-chat-route .composer{position:absolute" in css
    assert ".is-chat-route .file-picker::before{content:\"+\"" in css
    assert ".is-chat-route .composer button.primary::before{content:\"↑\"" in css


def test_chat_topbar_preserves_employee_controls_while_legacy_hero_is_hidden() -> None:
    base = _read("server/app/templates/user_base.html")
    assert "current_path.startswith('/account/chat/employee/') and employee is defined" in base
    assert 'href="/account/employees" title="智体设置"' in base
    assert 'href="/account/agent" title="Coding Agent 项目任务"' in base
    assert 'action="/account/chat/employee/{{ employee.id }}/clear"' in base
    assert "确认把当前聊天记录移入最近删除" in base
