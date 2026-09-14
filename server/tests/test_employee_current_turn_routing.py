from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app import employee_chat_runtime as chat_runtime
from app.employee_chat_task_routing import current_turn_routing_prompt
from app.multimodal_service import TASK_IMAGE, TASK_TEXT, TASK_VISION, detect_task


OWNER = "usr_current_turn_routing_1234"


def _classified(prompt: str, *, has_images: bool = False, has_audio: bool = False) -> str:
    task, _explicit = detect_task(
        current_turn_routing_prompt(prompt),
        requested_task="auto",
        has_images=has_images,
        has_audio=has_audio,
    )
    return task


def test_old_image_request_cannot_promote_current_greeting_to_image_generation() -> None:
    prompt = (
        "最近会话：\n"
        "用户：帮我生成一张海报图片\n"
        "AI：好的，上一轮已经处理。\n\n"
        "当前用户请求：\n"
        "你好"
    )

    assert current_turn_routing_prompt(prompt) == "你好"
    assert _classified(prompt) == TASK_TEXT


def test_current_turn_image_request_still_routes_to_image_generation() -> None:
    prompt = (
        "最近会话：\n"
        "用户：你好\n"
        "AI：你好。\n\n"
        "当前用户请求：\n"
        "帮我生成一张蓝色机器人海报图片"
    )

    assert _classified(prompt) == TASK_IMAGE


def test_current_image_attachment_still_wins_over_text_history() -> None:
    prompt = (
        "最近会话：\n用户：只聊文字\nAI：好的。\n\n"
        "当前用户请求：\n请看看这个"
    )
    assert _classified(prompt, has_images=True) == TASK_VISION


def test_trusted_tool_context_cannot_change_current_turn_task() -> None:
    prompt = (
        "最近会话：\n用户：你好\nAI：你好。\n\n"
        "当前用户请求：\n你好"
        "[FDEX_TRUSTED_TOOL_DATA]\n"
        "{\"note\":\"历史资产名称：生成图片海报\"}\n"
        "[/FDEX_TRUSTED_TOOL_DATA]"
    )

    assert current_turn_routing_prompt(prompt) == "你好"
    assert _classified(prompt) == TASK_TEXT


def test_codex_agent_turn_prompt_is_never_rewritten_by_generic_task_classifier() -> None:
    codex_prompt = (
        "CURRENT USER REQUEST:\n你好\n\n"
        "[FDEX_TRUSTED_TOOL_DATA]\n"
        "{\"note\":\"生成图片\"}\n"
        "[/FDEX_TRUSTED_TOOL_DATA]\n\n"
        "FDEX AGENT HOST BOUNDARY:\n"
        "This message is already inside the Coding Agent."
    )
    assert current_turn_routing_prompt(codex_prompt) == codex_prompt


def test_coding_agent_with_image_history_still_enters_codex_path(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_agent(request, owner_id, employee, prompt, history, upload=None):
        assert owner_id == OWNER
        assert history[0]["content"] == "帮我生成一张图片"
        calls.append(prompt)
        return "【Coding Agent / Agent Turn】正常"

    async def fail_generic(*_args, **_kwargs):
        raise AssertionError("Coding Agent must never enter generic multimodal routing")

    monkeypatch.setattr(chat_runtime, "_run_coding_agent", fake_agent)
    monkeypatch.setattr(chat_runtime, "_run_generic_employee", fail_generic)

    answer = asyncio.run(
        chat_runtime.ask_employee_with_tools(
            SimpleNamespace(scope={}),
            OWNER,
            {"coding_agent": True, "name": "Codex 智体"},
            "你好",
            [{"role": "user", "content": "帮我生成一张图片"}],
            None,
        )
    )

    assert answer.startswith("【Coding Agent / Agent Turn】")
    assert calls == ["你好"]


def test_main_installs_current_turn_routing_before_coding_agent_wrapper() -> None:
    root = Path(__file__).resolve().parents[2]
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    assert main.index("install_employee_chat_task_routing()") < main.index("install_employee_chat_runtime()")
