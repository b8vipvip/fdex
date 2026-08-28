from pathlib import Path


def test_phase713_keeps_phase711_and_phase712_chat_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    js = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    assert "正在连接 FDEX AI 线路" in js
    assert "/send-json" in js
    assert "event.preventDefault()" in js
    assert "Asia/Shanghai" in js
    assert "北京时间" in js
    assert "tool_events" in js
