from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from app.codex_app_server import (
    CodexAppServerClient,
    CodexServerRequestDenied,
    CodexTransportClosed,
    JsonObject,
)

InteractiveServerRequestHandler = Callable[[int | str, str, JsonObject], Awaitable[Any]]


class InteractiveCodexAppServerClient(CodexAppServerClient):
    """Codex client variant that exposes JSON-RPC request identity to FDEX interaction routing.

    The Phase 7.20 transport intentionally offered only method+params because every interactive
    server request was denied. Phase 7.23 needs the actual request id for durable correlation and
    must also cancel outstanding request handlers when the stdio Host closes. Keeping this as a
    subclass preserves the generic schema-light transport for non-interactive callers.
    """

    def __init__(self, *args: Any, interactive_request_handler: InteractiveServerRequestHandler, **kwargs: Any) -> None:
        # The base request handler remains None; this subclass owns the full request lifecycle.
        kwargs["server_request_handler"] = None
        super().__init__(*args, **kwargs)
        self.interactive_request_handler = interactive_request_handler
        self._interactive_tasks: set[asyncio.Task[Any]] = set()

    async def _handle_server_request(self, message: JsonObject) -> None:
        current = asyncio.current_task()
        if current is not None:
            self._interactive_tasks.add(current)
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params")
        normalized = params if isinstance(params, dict) else {}
        try:
            if request_id is None:
                raise CodexServerRequestDenied("Codex server request has no JSON-RPC id")
            result = await self.interactive_request_handler(request_id, method, normalized)
            await self._send({"id": request_id, "result": result})
        except asyncio.CancelledError:
            raise
        except CodexServerRequestDenied as exc:
            try:
                await self._send(
                    {
                        "id": request_id,
                        "error": {"code": -32003, "message": str(exc)[:1000]},
                    }
                )
            except CodexTransportClosed:
                if not self._closed:
                    raise
        except Exception as exc:
            try:
                await self._send(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"FDEX server-request handler failed: {exc}"[:1000],
                        },
                    }
                )
            except CodexTransportClosed:
                if not self._closed:
                    raise
        finally:
            if current is not None:
                self._interactive_tasks.discard(current)

    async def close(self) -> None:
        current = asyncio.current_task()
        pending = [task for task in tuple(self._interactive_tasks) if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The handler already persisted a fail-closed interaction state. Transport close
                # must continue even if a broker cleanup write itself encountered an error.
                pass
        await super().close()
