from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import app.center_auth_middleware as center_auth_module
from app.center_auth_middleware import CenterUserAuthMiddleware
from app.memory_middleware import decode_memory_control


def _marker(name: str, value: str) -> str:
    encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
    return f"[[{name}:{encoded}]]"


def _memory_marker(scope_token: str) -> str:
    payload = {
        "scope": scope_token,
        "conversation_id": "employee:7",
        "employee_id": "7",
        "knowledge_read": True,
        "knowledge_write": True,
        "chat_access_mode": "self",
        "readable_employee_ids": [],
        "future_field": "preserved",
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"[[FDEX_MEMORY_V2:{encoded}]]"


class _FakeStore:
    def authenticate_access(self, token: str):
        if token == "valid-center-access-token-for-test":
            return {"id": "usr_alpha", "email": "alpha@example.com"}
        return None


def test_realtime_first_message_authenticates_and_rebinds_memory(monkeypatch) -> None:
    monkeypatch.setattr(center_auth_module, "central_auth_store", lambda: _FakeStore())
    original_scope = "L" * 32
    start = {
        "type": "start",
        "system": "你是测试员工",
        "memory_control": "\n".join(
            [
                _marker("FDEX_AUTH_V1", "valid-center-access-token-for-test"),
                _memory_marker(original_scope),
            ]
        ),
    }
    incoming = iter(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(start)},
        ]
    )
    sent: list[dict[str, object]] = []
    seen: dict[str, object] = {}

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    async def app(scope, receive_app, send_app):
        assert (await receive_app())["type"] == "websocket.connect"
        await send_app({"type": "websocket.accept"})
        first = await receive_app()
        seen["scope_user_id"] = scope.get("fdex_user_id")
        seen["payload"] = json.loads(first["text"])

    middleware = CenterUserAuthMiddleware(app)
    scope = {"type": "websocket", "path": "/api/client/voice/realtime", "headers": []}
    asyncio.run(middleware(scope, receive, send))

    assert seen["scope_user_id"] == "usr_alpha"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    control_text = str(payload["memory_control"])
    assert "FDEX_AUTH_V1" not in control_text
    _, control = decode_memory_control(control_text)
    assert control is not None
    expected = hashlib.sha256(f"usr_alpha:{original_scope}".encode("utf-8")).hexdigest()
    assert control.scope_token == expected

    match = center_auth_module._MEMORY_MARKER.search(control_text)
    assert match is not None
    encoded = match.group(1)
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    raw = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    assert raw["future_field"] == "preserved"


def test_realtime_invalid_center_session_never_reaches_start(monkeypatch) -> None:
    monkeypatch.setattr(center_auth_module, "central_auth_store", lambda: _FakeStore())
    start = {
        "type": "start",
        "memory_control": _marker("FDEX_AUTH_V1", "expired-token"),
    }
    incoming = iter(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(start)},
        ]
    )
    sent: list[dict[str, object]] = []
    seen_type = ""

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    async def app(scope, receive_app, send_app):
        nonlocal seen_type
        assert (await receive_app())["type"] == "websocket.connect"
        await send_app({"type": "websocket.accept"})
        first = await receive_app()
        seen_type = json.loads(first["text"])["type"]
        await send_app({"type": "websocket.send", "text": '{"type":"error","message":"generic"}'})
        await send_app({"type": "websocket.close", "code": 1008})

    middleware = CenterUserAuthMiddleware(app)
    scope = {"type": "websocket", "path": "/api/client/voice/realtime", "headers": []}
    asyncio.run(middleware(scope, receive, send))

    assert seen_type == "__fdex_auth_failed__"
    outbound = [item for item in sent if item.get("type") == "websocket.send"]
    assert outbound
    body = json.loads(str(outbound[-1]["text"]))
    assert body["type"] == "error"
    assert "重新登录" in body["message"]
