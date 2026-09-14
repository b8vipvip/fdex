from __future__ import annotations

from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_chat_route_exposes_beijing_message_time() -> None:
    root = _root()
    runtime = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    tuning = (root / "server/app/static/user_chatgpt_tuning.css").read_text(encoding="utf-8")

    assert "timeZone: 'Asia/Shanghai'" in runtime
    assert "北京时间" in runtime
    assert ".is-chat-route .chat-bubble>.fine" in tuning
    assert "height:auto" in tuning
    assert "opacity:1" in tuning


def test_chat_composer_no_longer_overlays_messages() -> None:
    tuning = (_root() / "server/app/static/user_chatgpt_tuning.css").read_text(encoding="utf-8")

    composer = tuning.split(".is-chat-route .composer{", 1)[1].split("}", 1)[0]
    history = tuning.split(".is-chat-route .chat-history{", 1)[1].split("}", 1)[0]

    assert "position:relative" in composer
    assert "bottom:auto" in composer
    assert "flex:0 0 auto" in composer
    assert "padding-bottom:14px" in history
    assert "padding-bottom:172px" not in tuning


def test_chat_composer_uses_compact_autogrowing_input_and_centered_icons() -> None:
    tuning = (_root() / "server/app/static/user_chatgpt_tuning.css").read_text(encoding="utf-8")

    assert "field-sizing:content" in tuning
    assert "min-height:40px" in tuning
    assert "max-height:180px" in tuning

    plus = tuning.split(".is-chat-route .file-picker::before{", 1)[1].split("}", 1)[0]
    send = tuning.split(".is-chat-route .composer button.primary::before{", 1)[1].split("}", 1)[0]
    assert "position:absolute" in plus
    assert "left:50%" in plus and "top:50%" in plus
    assert "transform:translate(-50%,-50%)" in plus
    assert "M10.75 4.5h2.5" in plus
    assert "position:absolute" in send
    assert "left:50%" in send and "top:50%" in send
    assert "calc(-50% + .5px)" in send
    assert "M10.75 19.5V9.2" in send

    assert ".is-chat-route .composer>.fine{display:none}" in tuning
