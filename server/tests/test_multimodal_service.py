from __future__ import annotations

from app.multimodal_service import (
    TASK_AUDIO,
    TASK_IMAGE,
    TASK_TEXT,
    TASK_VISION,
    build_chat_messages,
    detect_task,
    infer_audio_protocol,
)


def test_detect_task_prefers_actual_media_inputs() -> None:
    assert detect_task("看一下", has_images=True)[0] == TASK_VISION
    assert detect_task("听一下", has_audio=True)[0] == TASK_AUDIO


def test_detect_task_auto_specialized_intents_are_conservative() -> None:
    assert detect_task("帮我生成一张蓝天白云的图片")[0] == TASK_IMAGE
    assert detect_task("请用语音回复我")[0] == TASK_AUDIO
    assert detect_task("教我怎么写生成图片的 API 代码")[0] == TASK_TEXT
    assert detect_task("普通聊天问题")[0] == TASK_TEXT


def test_explicit_task_overrides_prompt_heuristics() -> None:
    task, explicit = detect_task("生成一张图片", requested_task="text")
    assert task == TASK_TEXT
    assert explicit is True


def test_chat_messages_support_image_and_audio_content_parts() -> None:
    messages = build_chat_messages(
        "system",
        "describe",
        images=[{"url": "data:image/png;base64,AA==", "detail": "low"}],
        audio={"data": "ZmFrZQ==", "format": "wav"},
    )
    assert messages[0] == {"role": "system", "content": "system"}
    user = messages[1]
    assert user["role"] == "user"
    parts = user["content"]
    assert any(part.get("type") == "image_url" for part in parts)
    assert any(part.get("type") == "input_audio" for part in parts)


def test_audio_protocol_auto_inference() -> None:
    assert infer_audio_protocol({"audio_protocol": "auto"}, "gpt-4o-mini-tts") == "speech"
    assert infer_audio_protocol({"audio_protocol": "auto"}, "gpt-audio") == "chat_audio"
    assert infer_audio_protocol({"audio_protocol": "auto"}, "gpt-realtime") == "realtime"
    assert infer_audio_protocol({"audio_protocol": "speech"}, "custom-audio") == "speech"
