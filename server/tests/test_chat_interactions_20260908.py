from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_coding_agent_github_inventory_bypasses_codex_provider_gate() -> None:
    source = (ROOT / "server/app/user_chat_api_routes.py").read_text(encoding="utf-8")
    assert "def _direct_github_inventory_answer(" in source
    assert "collect_employee_tool_context(owner_id, employee, clean)" in source
    assert 'request.scope["fdex_employee_tool_events"]' in source
    direct_call = source.index("answer = _direct_github_inventory_answer(")
    codex_call = source.index("answer = await _ask_employee(")
    assert direct_call < codex_call
    assert "if not answer:" in source[direct_call:codex_call]
    assert "_GITHUB_EXECUTION_HINTS" in source
    assert '"修改"' in source and '"push"' in source and '"测试"' in source


def test_web_employee_chat_supports_enter_paste_and_chatgpt_style_actions() -> None:
    source = (ROOT / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    assert "event.key !== 'Enter' || event.shiftKey || event.isComposing" in source
    assert "event.clipboardData?.files" in source
    assert "new DataTransfer()" in source
    for action in ("copy", "like", "share", "retry"):
        assert f"actionButton('{action}'" in source
    assert "navigator.share" in source
    assert "submitAgentChat(targetForm)" in source


def test_android_chat_supports_clipboard_enter_and_message_actions() -> None:
    composer = (ROOT / "app/src/main/java/com/b8vipvip/fdex/ui/AttachmentChatComposer.kt").read_text(encoding="utf-8")
    actions = (ROOT / "app/src/main/java/com/b8vipvip/fdex/ui/ChatMessageActions.kt").read_text(encoding="utf-8")
    assert "ContentPaste" in composer
    assert "pasteClipboardAttachment" in composer
    assert "Key.Enter" in composer and "!event.isShiftPressed" in composer
    assert "ImeAction.Send" in composer
    assert "ChatRegenerateBus.requests.collect" in composer
    for icon in ("ContentCopy", "ThumbUp", "Share", "Refresh"):
        assert f"Icons.Default.{icon}" in actions
    assert "ChatRegenerateBus.request(content)" in actions
