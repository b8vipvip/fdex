from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from app.central_auth import central_auth_store

_PROTECTED_HTTP_PATHS = {"/api/client/ai", "/api/client/ai/stream"}


class CenterUserAuthMiddleware:
    """Require the FDEX Center access token for user-owned client AI resources.

    Public health/update/auth routes stay compatible. The authenticated user id is written
    into the ASGI scope so downstream memory/account layers can derive ownership from the
    server-validated identity instead of trusting a client-supplied user id.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _PROTECTED_HTTP_PATHS:
            await self.app(scope, receive, send)
            return

        token = self._bearer(scope)
        user = central_auth_store().authenticate_access(token) if token else None
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

        scope["fdex_user"] = user
        scope["fdex_user_id"] = str(user.get("id") or "")
        await self.app(scope, receive, send)

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
