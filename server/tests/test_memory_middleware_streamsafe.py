from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from app.memory_middleware_streamsafe import StreamSafeFdexMemoryMiddleware
from app import memory_middleware_streamsafe


def _memory_marker() -> str:
    payload = {
        "scope": "stream-safe-scope-token-12345678901234567890",
        "conversation_id": "employee:7",
        "employee_id": "7",
        "knowledge_read": False,
        "knowledge_write": False,
        "chat_access_mode": "self",
        "readable_employee_ids": [],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"[[FDEX_MEMORY_V2:{encoded}]]"


def test_rewritten_body_is_replayed_once_then_receive_delegates_to_disconnect(monkeypatch) -> None:
    """Regression for the StreamingResponse CPU busy-loop.

    After the middleware consumes and rewrites the request body, the downstream app gets
    exactly one synthetic http.request. A second receive() must delegate to the real ASGI
    receive callable and therefore observe http.disconnect. Returning another immediate
    empty http.request here would make StreamingResponse's disconnect listener spin forever.
    """

    monkeypatch.setattr(
        memory_middleware_streamsafe,
        "fresh_settings",
        lambda: SimpleNamespace(
            fdex_memory_enabled=False,
            fdex_memory_system_max_chars=12000,
        ),
    )

    downstream_messages: list[dict[str, object]] = []
    original_receive_calls = 0

    async def downstream(_scope, receive, send):
        downstream_messages.append(await receive())
        downstream_messages.append(await asyncio.wait_for(receive(), timeout=0.2))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{}', "more_body": False})

    middleware = StreamSafeFdexMemoryMiddleware(downstream)
    body = json.dumps(
        {
            "system": "你是项目助理。",
            "prompt": f"统计项目\n{_memory_marker()}",
            "task": "auto",
        },
        ensure_ascii=False,
    ).encode("utf-8")

    queue = [
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def original_receive():
        nonlocal original_receive_calls
        original_receive_calls += 1
        if queue:
            return queue.pop(0)
        # A correct implementation never reaches this in the test. Keep the future pending
        # so an accidental busy loop fails by timeout instead of consuming CPU in CI.
        await asyncio.Future()

    sent: list[dict[str, object]] = []

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/client/ai/stream",
                "headers": [],
            },
            original_receive,
            send,
        )
    )

    assert original_receive_calls == 2
    assert downstream_messages[0]["type"] == "http.request"
    rewritten = json.loads(downstream_messages[0]["body"].decode("utf-8"))
    assert rewritten["prompt"] == "统计项目"
    assert "FDEX_MEMORY_V2" not in rewritten["prompt"]
    assert "L2_FDEX_MEMORY_POLICY" in rewritten["system"]
    assert downstream_messages[1] == {"type": "http.disconnect"}
    assert sent[-1]["type"] == "http.response.body"


def test_client_disconnect_while_middleware_reads_body_stops_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(
        memory_middleware_streamsafe,
        "fresh_settings",
        lambda: SimpleNamespace(
            fdex_memory_enabled=False,
            fdex_memory_system_max_chars=12000,
        ),
    )
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        raise AssertionError("no response should be emitted after an early disconnect")

    asyncio.run(
        StreamSafeFdexMemoryMiddleware(downstream)(
            {"type": "http", "method": "POST", "path": "/api/client/ai/stream", "headers": []},
            receive,
            send,
        )
    )
    assert downstream_called is False
