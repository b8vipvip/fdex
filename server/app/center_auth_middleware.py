from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Awaitable, Callable

from app.central_auth import central_auth_store
from app.memory_middleware import decode_memory_control

_PROTECTED_HTTP_PATHS = {"/api/client/ai", "/api/client/ai/stream"}
_PROTECTED_WEBSOCKET_PATHS = {"/api/client/voice/realtime"}
_AUTH_MARKER = re.compile(r"\[\[FDEX_AUTH_V1:([A-Za-z0-9_\-=]+)]]")
_MEMORY_MARKER = re.compile(r"\[\[FDEX_MEMORY_V2:([A-Za-z0-9_\-=]+)]]")


class CenterUserAuthMiddleware:
    """Require the FDEX Center identity for user-owned client resources.

    HTTP AI calls authenticate with the normal Authorization Bearer header. Realtime voice
    also accepts that header, and Android may carry the same opaque access token in a
    FDEX-only first-message marker because OkHttp's existing voice session predates center
    authentication. The marker is consumed before the realtime route sees the start payload.

    For both transports the server-validated user id is written into ASGI scope. Remote
    memory control is rebound to that user id before downstream recall/write code sees it,
    so two FDEX accounts cannot share a remote memory namespace even if their local scope
    token happens to be identical.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        scope_type = str(scope.get("type") or "")
        path = str(scope.get("path") or "")
        if scope_type == "http" and path in _PROTECTED_HTTP_PATHS:
            await self._http(scope, receive, send)
            return
        if scope_type == "websocket" and path in _PROTECTED_WEBSOCKET_PATHS:
            await self._websocket(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _http(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        user = self._authenticate(self._bearer(scope))
        if user is None:
            body = json.dumps({"detail": "FDEX login has expired"}, ensure_ascii=False).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        self._bind_scope(scope, user)
        await self.app(scope, receive, send)

    async def _websocket(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        header_user = self._authenticate(self._bearer(scope))
        state: dict[str, Any] = {
            "first_payload_seen": False,
            "auth_failed": False,
            "user": header_user,
        }
        if header_user is not None:
            self._bind_scope(scope, header_user)

        async def authenticated_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") != "websocket.receive" or state["first_payload_seen"]:
                return message
            state["first_payload_seen"] = True

            raw_text = message.get("text")
            if not isinstance(raw_text, str):
                if state["user"] is None:
                    state["auth_failed"] = True
                    return {"type": "websocket.receive", "text": '{"type":"__fdex_auth_failed__"}'}
                return message

            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                if state["user"] is None:
                    state["auth_failed"] = True
                    return {"type": "websocket.receive", "text": '{"type":"__fdex_auth_failed__"}'}
                return message
            if not isinstance(payload, dict):
                if state["user"] is None:
                    state["auth_failed"] = True
                    return {"type": "websocket.receive", "text": '{"type":"__fdex_auth_failed__"}'}
                return message

            marker_token = ""
            for key in ("memory_control", "system"):
                value = payload.get(key)
                if not isinstance(value, str):
                    continue
                clean, token = self._strip_auth_marker(value)
                if token and not marker_token:
                    marker_token = token
                payload[key] = clean

            user = state["user"] or self._authenticate(marker_token)
            if user is None:
                state["auth_failed"] = True
                return {"type": "websocket.receive", "text": '{"type":"__fdex_auth_failed__"}'}

            state["user"] = user
            self._bind_scope(scope, user)
            user_id = str(user.get("id") or "")
            for key in ("memory_control", "system"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    payload[key] = self._bind_memory_marker(value, user_id)
            return {
                **message,
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }

        async def authenticated_send(message: dict[str, Any]) -> None:
            if state["auth_failed"] and message.get("type") == "websocket.send" and isinstance(message.get("text"), str):
                message = {
                    **message,
                    "text": json.dumps(
                        {"type": "error", "message": "FDEX 登录状态已失效，请重新登录"},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            await send(message)

        await self.app(scope, authenticated_receive, authenticated_send)

    @staticmethod
    def _authenticate(token: str) -> dict[str, object] | None:
        clean = (token or "").strip()
        return central_auth_store().authenticate_access(clean) if clean else None

    @staticmethod
    def _bind_scope(scope: dict[str, Any], user: dict[str, object]) -> None:
        scope["fdex_user"] = user
        scope["fdex_user_id"] = str(user.get("id") or "")

    @staticmethod
    def _bearer(scope: dict[str, Any]) -> str:
        headers = scope.get("headers") or []
        for raw_key, raw_value in headers:
            if raw_key.lower() != b"authorization":
                continue
            value = raw_value.decode("latin-1", errors="ignore").strip()
            scheme, _, token = value.partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
        return ""

    @staticmethod
    def _strip_auth_marker(value: str) -> tuple[str, str]:
        match = _AUTH_MARKER.search(value or "")
        if match is None:
            return value, ""
        encoded = match.group(1)
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            token = base64.urlsafe_b64decode(encoded + padding).decode("utf-8").strip()
        except (ValueError, UnicodeDecodeError):
            token = ""
        clean = _AUTH_MARKER.sub("", value, count=1).strip()
        return clean, token

    @staticmethod
    def _bind_memory_marker(value: str, user_id: str) -> str:
        match = _MEMORY_MARKER.search(value or "")
        if match is None or not user_id:
            return value
        clean, control = decode_memory_control(value)
        if control is None:
            return clean
        encoded = match.group(1)
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return clean
        if not isinstance(payload, dict):
            return clean
        payload["scope"] = hashlib.sha256(f"{user_id}:{control.scope_token}".encode("utf-8")).hexdigest()
        rebound = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        marker = f"[[FDEX_MEMORY_V2:{rebound}]]"
        return (value[: match.start()] + marker + value[match.end() :]).strip()
