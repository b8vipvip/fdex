from __future__ import annotations

from typing import Callable


_CURRENT_USER_MARKER = "\n\n当前用户请求：\n"
_TRUSTED_TOOL_MARKER = "[FDEX_TRUSTED_TOOL_DATA]"
_CODEX_BOUNDARY_MARKER = "FDEX AGENT HOST BOUNDARY:"
_CODEX_CURRENT_REQUEST_MARKER = "CURRENT USER REQUEST:\n"


def current_turn_routing_prompt(prompt: str) -> str:
    """Return only the current generic-employee turn for multimodal task classification.

    Generic Web employee chat keeps recent conversation and trusted host facts inside the actual
    model prompt so the model can answer with context. Those older/contextual strings must not,
    however, decide whether this *new* turn is text, image generation, vision, or audio. Otherwise a
    historical request such as "生成一张图片" can make a later plain "你好" call the image model.

    Native Coding Agent / Codex prompts have their own Turn semantics and must remain byte-for-byte
    untouched here. Coding Agent traffic normally never reaches client_ai at all; the explicit guard
    below keeps that boundary safe even if a future caller accidentally reuses this helper.
    """
    text = str(prompt or "")
    if _CODEX_BOUNDARY_MARKER in text and _CODEX_CURRENT_REQUEST_MARKER in text:
        return text

    if _CURRENT_USER_MARKER in text:
        text = text.rsplit(_CURRENT_USER_MARKER, 1)[1]

    # Ordinary employees can append deterministic host facts after the user message. They are useful
    # model context but are not user intent and therefore must not promote a text turn to image/audio.
    if _TRUSTED_TOOL_MARKER in text:
        text = text.split(_TRUSTED_TOOL_MARKER, 1)[0]

    return text.strip()


def install_employee_chat_task_routing() -> None:
    """Install current-turn-only task classification into the shared client_ai runtime."""
    from app import client_ai

    current: Callable[..., tuple[str, bool]] = client_ai.detect_task
    if getattr(current, "_fdex_current_turn_employee_routing", False):
        return

    def detect_current_turn_task(
        prompt: str,
        *,
        requested_task: str = "auto",
        has_images: bool = False,
        has_audio: bool = False,
    ) -> tuple[str, bool]:
        return current(
            current_turn_routing_prompt(prompt),
            requested_task=requested_task,
            has_images=has_images,
            has_audio=has_audio,
        )

    detect_current_turn_task._fdex_current_turn_employee_routing = True  # type: ignore[attr-defined]
    client_ai.detect_task = detect_current_turn_task
