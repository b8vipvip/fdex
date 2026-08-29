from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from app.codex_item_store import codex_item_store

_SENTINEL = object()


@dataclass(slots=True)
class _Envelope:
    method: str
    params: dict[str, Any]


class CodexNotificationCapture:
    """Ordered bounded async sink between Codex stdout and durable SQLite events.

    The app-server reader must keep reading JSON-RPC responses/notifications even when disk I/O
    briefly stalls. A dedicated writer task preserves event order and makes shutdown drain
    explicit, while bounded queue backpressure prevents unbounded RAM growth. Publish/close also
    watch the writer task so a SQLite failure can never strand a producer on a full queue.
    """

    def __init__(self, owner_id: str, task_id: str, *, max_pending: int = 4096) -> None:
        self.owner_id = owner_id
        self.task_id = task_id
        self.queue: asyncio.Queue[_Envelope | object] = asyncio.Queue(maxsize=max(128, max_pending))
        self.worker: asyncio.Task[None] | None = None
        self.error: Exception | None = None

    async def start(self) -> None:
        if self.worker is None:
            self.worker = asyncio.create_task(self._run(), name=f"fdex-codex-events-{self.task_id[:8]}")

    async def _put_while_writer_alive(self, value: _Envelope | object) -> None:
        if self.error is not None:
            raise self.error
        if self.worker is None:
            await self.start()
        assert self.worker is not None
        if self.worker.done():
            await self.worker
            if self.error is not None:
                raise self.error
            raise RuntimeError("Codex notification writer stopped unexpectedly")

        put_task = asyncio.create_task(self.queue.put(value))
        done, _pending = await asyncio.wait(
            {put_task, self.worker},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self.worker in done and not put_task.done():
            put_task.cancel()
            try:
                await put_task
            except asyncio.CancelledError:
                pass
            await self.worker
            if self.error is not None:
                raise self.error
            raise RuntimeError("Codex notification writer stopped unexpectedly")
        await put_task
        if self.error is not None:
            raise self.error

    async def publish(self, method: str, params: dict[str, Any]) -> None:
        if self.worker is None:
            await self.start()
        await self._put_while_writer_alive(_Envelope(str(method or "unknown"), dict(params)))

    async def close(self) -> None:
        worker = self.worker
        if worker is None:
            if self.error is not None:
                raise self.error
            return
        if not worker.done():
            try:
                await self._put_while_writer_alive(_SENTINEL)
            except Exception:
                # The writer already owns the durable error. Do not hide it behind a queue
                # shutdown race or wait forever for a consumer that has exited.
                if not worker.done():
                    worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        finally:
            self.worker = None
        if self.error is not None:
            raise self.error

    async def _run(self) -> None:
        store = codex_item_store()
        while True:
            item = await self.queue.get()
            try:
                if item is _SENTINEL:
                    return
                assert isinstance(item, _Envelope)
                await asyncio.to_thread(
                    store.record_notification,
                    owner_id=self.owner_id,
                    task_id=self.task_id,
                    method=item.method,
                    params=item.params,
                )
            except Exception as exc:
                self.error = exc
                # Stop accepting a stream that cannot be durably represented. Losing arbitrary
                # Item events while continuing execution would make approvals/debugging unsafe.
                return
            finally:
                self.queue.task_done()


_current_capture: ContextVar[CodexNotificationCapture | None] = ContextVar(
    "fdex_codex_notification_capture",
    default=None,
)


def install_capture(capture: CodexNotificationCapture) -> Token[CodexNotificationCapture | None]:
    return _current_capture.set(capture)


def reset_capture(token: Token[CodexNotificationCapture | None]) -> None:
    _current_capture.reset(token)


async def publish_transport_notification(method: str, params: dict[str, Any]) -> None:
    capture = _current_capture.get()
    if capture is not None:
        await capture.publish(method, params)
