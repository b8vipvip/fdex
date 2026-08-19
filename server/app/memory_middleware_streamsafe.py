from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from app.config import fresh_settings
from app.fdex_memory import MemoryRecall, MemoryScope, memory_coordinator
from app.memory_middleware import (
    FdexMemoryMiddleware,
    _MAX_CAPTURE_BYTES,
    _assistant_from_capture,
    _strip_client_wrapper_preamble,
    compose_system_layers,
    decode_memory_control,
    extract_local_context,
)

logger = logging.getLogger(__name__)


class StreamSafeFdexMemoryMiddleware(FdexMemoryMiddleware):
    """FdexMemoryMiddleware with correct ASGI receive semantics for streaming responses.

    The middleware consumes the original request body so it can remove the private memory
    marker and inject recalled context. The rewritten body must be replayed exactly once.
    After that, every receive() call must delegate to the original ASGI receive callable so
    Starlette's StreamingResponse can block while waiting for a real http.disconnect event.

    Returning an immediate empty http.request forever creates a tight busy loop in
    StreamingResponse's disconnect listener. That loop can pin one worker at 100% CPU,
    starve the response generator, make the admin console sluggish and eventually make the
    Android client time out before stream_begin is reached.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]]):
        super().__init__(app)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        settings = fresh_settings()
        if scope.get("type") != "http" or scope.get("path") not in {"/api/client/ai", "/api/client/ai/stream"}:
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                return
            if message_type != "http.request":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._replay(scope, body, receive, send)
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str):
            await self._replay(scope, body, receive, send)
            return

        original_prompt = payload["prompt"]
        without_marker, control = decode_memory_control(original_prompt)
        if control is None:
            if without_marker != original_prompt:
                payload["prompt"] = without_marker
                sanitized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                await self._replay(scope, sanitized, receive, send)
            else:
                await self._replay(scope, body, receive, send)
            return

        user_prompt, local_context = extract_local_context(without_marker)
        query = user_prompt.strip() or "当前附件或任务"
        recall = MemoryRecall()
        if settings.fdex_memory_enabled:
            try:
                recall = await memory_coordinator().recall(
                    query,
                    MemoryScope(control.scope_token),
                    allowed_employee_ids=control.allowed_employee_ids,
                    include_letta=control.knowledge_read,
                )
            except Exception:
                logger.exception("FDEX memory recall middleware failed open")

        payload["prompt"] = user_prompt
        payload["system"] = compose_system_layers(
            str(payload.get("system") or ""),
            _strip_client_wrapper_preamble(local_context),
            recall,
            max_chars=settings.fdex_memory_system_max_chars,
        )
        rewritten = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        response_type = ""
        captured = bytearray()
        status_code = 0
        replayed_request = False

        async def memory_receive() -> dict[str, Any]:
            nonlocal replayed_request
            if not replayed_request:
                replayed_request = True
                return {"type": "http.request", "body": rewritten, "more_body": False}
            # Critical: do not fabricate another empty request. StreamingResponse runs a
            # disconnect listener that repeatedly calls receive(); delegating here lets it
            # sleep until the server actually reports a disconnect instead of spinning CPU.
            return await receive()

        async def memory_send(message: dict[str, Any]) -> None:
            nonlocal response_type, status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 0)
                for key, value in message.get("headers", []):
                    if key.lower() == b"content-type":
                        response_type = value.decode("latin-1", errors="ignore").lower()
            elif message.get("type") == "http.response.body" and len(captured) < _MAX_CAPTURE_BYTES:
                piece = message.get("body", b"")
                captured.extend(piece[: _MAX_CAPTURE_BYTES - len(captured)])

            await send(message)

            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                if 200 <= status_code < 300 and settings.fdex_memory_enabled:
                    assistant = _assistant_from_capture(bytes(captured), response_type)
                    if assistant:
                        asyncio.create_task(
                            self._remember(
                                control=control,
                                user_text=user_prompt,
                                assistant_text=assistant,
                            )
                        )

        await self.app(scope, memory_receive, memory_send)
