from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = SERVER_DIR / "app" / "static"
TEMPLATE_DIR = SERVER_DIR / "app" / "templates"


def test_phase744_theme_toggle_is_global_persistent_and_next_to_avatar() -> None:
    base = (TEMPLATE_DIR / "user_base.html").read_text(encoding="utf-8")

    assert '/static/user_theme.css' in base
    assert 'data-theme-toggle' in base
    assert 'theme-icon-sun' in base
    assert 'theme-icon-moon' in base
    assert "fdex-user-theme" in base
    assert "localStorage.setItem(key, theme)" in base
    assert "document.documentElement.dataset.theme = theme" in base
    assert "切换到白天模式" in base
    assert "切换到夜间模式" in base

    toggle_pos = base.index('data-theme-toggle')
    avatar_pos = base.index('class="topbar-avatar"', toggle_pos)
    assert toggle_pos < avatar_pos


def test_phase744_light_theme_covers_shell_chat_and_management_surfaces() -> None:
    css = (STATIC_DIR / "user_theme.css").read_text(encoding="utf-8")

    assert 'html[data-theme="light"]' in css
    assert 'html[data-theme="light"] .user-sidebar' in css
    assert 'html[data-theme="light"] .user-topbar' in css
    assert 'html[data-theme="light"] .management-modal-panel' in css
    assert 'html[data-theme="light"] .plugin-card' in css
    assert 'html[data-theme="light"] .is-chat-route .composer' in css
    assert 'html[data-theme="light"] .is-chat-route .chat-bubble.user' in css
    assert 'html[data-theme="light"] .is-chat-route .composer button.primary' in css


def test_phase744_composer_icons_use_absolute_optical_centering() -> None:
    css = (STATIC_DIR / "user_chatgpt_tuning.css").read_text(encoding="utf-8")

    plus = css.split('.is-chat-route .file-picker::before{', 1)[1].split('}', 1)[0]
    send = css.split('.is-chat-route .composer button.primary::before{', 1)[1].split('}', 1)[0]

    assert 'position:absolute' in plus
    assert 'left:50%' in plus and 'top:50%' in plus
    assert 'transform:translate(-50%,-50%)' in plus
    assert "M10.75 4.5h2.5" in plus

    assert 'position:absolute' in send
    assert 'left:50%' in send and 'top:50%' in send
    assert 'calc(-50% + .5px)' in send
    assert "M10.75 19.5V9.2" in send
