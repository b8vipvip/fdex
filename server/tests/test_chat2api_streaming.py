import json

from app.client_ai import AIRequest, _extract_chat_chunk, _messages, _sse


def test_private_assistant_request_has_no_system_prompt() -> None:
    payload = AIRequest(system=None, prompt="你好")
    assert _messages(payload) == [{"role": "user", "content": "你好"}]


def test_employee_request_keeps_system_prompt() -> None:
    payload = AIRequest(system="你是资料管理员", prompt="整理资料")
    assert _messages(payload) == [
        {"role": "system", "content": "你是资料管理员"},
        {"role": "user", "content": "整理资料"},
    ]


def test_openai_chat_delta_is_forwarded() -> None:
    status, reasoning, content = _extract_chat_chunk(
        {"choices": [{"delta": {"content": "## 标题\n\n- 项目"}}]}
    )
    assert status == ""
    assert reasoning == ""
    assert content == "## 标题\n\n- 项目"


def test_chat2api_public_status_and_reasoning_are_forwarded() -> None:
    status, reasoning, content = _extract_chat_chunk(
        {
            "chat2api": {
                "reasoning_status": "正在搜索资料",
                "reasoning_summary": "正在比较两个公开来源",
            },
            "choices": [{"delta": {"content": "正文"}}],
        }
    )
    assert status == "正在搜索资料"
    assert reasoning == "正在比较两个公开来源"
    assert content == "正文"


def test_responses_style_public_reasoning_summary_is_supported() -> None:
    assert _extract_chat_chunk(
        {"type": "response.reasoning_summary_text.delta", "delta": "公开推理摘要"}
    ) == ("", "公开推理摘要", "")
    assert _extract_chat_chunk(
        {"type": "response.output_text.delta", "delta": "最终正文"}
    ) == ("", "", "最终正文")


def test_fdex_sse_payload_preserves_markdown_and_unicode() -> None:
    packet = _sse("content", delta="**粗体**\n\n- 列表")
    assert packet.startswith("data: ")
    assert packet.endswith("\n\n")
    data = json.loads(packet.removeprefix("data: ").strip())
    assert data == {"type": "content", "delta": "**粗体**\n\n- 列表"}
