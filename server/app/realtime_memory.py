from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import fresh_settings
from app.fdex_memory import MemoryRecall, MemoryScope, memory_coordinator
from app.memory_middleware import MemoryControl, compose_system_layers, decode_memory_control, extract_local_context
from app.memory_scope_registry import memory_scope_registry

logger = logging.getLogger(__name__)
MemoryWriter = Callable[..., Awaitable[dict[str, Any]]]
Diagnostic = Callable[..., None]


def clean_realtime_user_text(text: str) -> str:
    """Remove FDEX-only local context/control wrappers before long-term storage."""
    without_local, _ = extract_local_context(text or "")
    without_marker, _ = decode_memory_control(without_local)
    return without_marker.strip()


async def prepare_realtime_memory(
    system: str,
    memory_control: str,
) -> tuple[str, MemoryControl | None, MemoryRecall]:
    """Consume opaque control data and bootstrap a new realtime session."""
    clean_system, embedded_control = decode_memory_control(system or "")
    _, separate_control = decode_memory_control(memory_control or "")
    control = separate_control or embedded_control
    if control is None:
        return clean_system, None, MemoryRecall()

    settings = fresh_settings()
    recall = MemoryRecall()
    if settings.fdex_memory_enabled:
        try:
            recall = await memory_coordinator().recall_recent(
                MemoryScope(control.scope_token),
                allowed_employee_ids=control.allowed_employee_ids,
                include_letta=control.knowledge_read,
            )
        except Exception:
            logger.exception("FDEX realtime memory bootstrap failed open")
            recall = MemoryRecall(error_codes=("realtime_memory_bootstrap_exception",))

    if recall.empty:
        return clean_system, control, recall
    rendered = compose_system_layers(
        clean_system,
        "",
        recall,
        max_chars=settings.fdex_memory_system_max_chars,
    )
    return rendered or clean_system, control, recall


class RealtimeMemoryRecorder:
    """Persist only already-transcribed realtime text; audio is never accepted here."""

    def __init__(
        self,
        control: MemoryControl | None,
        *,
        diag: Diagnostic | None = None,
        writer: MemoryWriter | None = None,
    ) -> None:
        self.control = control
        self.diag = diag
        self.writer = writer
        self._pending_users: list[str] = []
        self._assistant_parts: list[str] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._write_generation: int | None = None
        if control is not None:
            try:
                self._write_generation = memory_scope_registry().write_generation(control.scope_token)
            except Exception:
                # An unregistered legacy/test scope has no center-owned generation. Keep the
                # recorder compatible; the write-time ownership guard still runs separately.
                logger.debug("FDEX realtime memory generation unavailable", exc_info=True)

    def add_user(self, text: str, *, source: str) -> None:
        value = clean_realtime_user_text(text)[:30000]
        if not value:
            return
        if self._pending_users and self._pending_users[-1] == value:
            self._emit("realtime_memory_user_deduped", source=source, chars=len(value))
            return
        self._pending_users.append(value)
        if len(self._pending_users) > 20:
            self._pending_users = self._pending_users[-20:]
        self._emit("realtime_memory_user_text", source=source, chars=len(value))

    def add_assistant_delta(self, delta: str) -> None:
        if not delta:
            return
        current = sum(len(item) for item in self._assistant_parts)
        remaining = max(0, 60000 - current)
        if remaining:
            self._assistant_parts.append(delta[:remaining])

    def set_assistant_final(self, text: str) -> None:
        value = (text or "").strip()[:60000]
        if not value:
            return
        current = "".join(self._assistant_parts)
        if not current:
            self._assistant_parts = [value]
        elif value.startswith(current) and len(value) > len(current):
            self._assistant_parts.append(value[len(current):])

    def complete(self, *, reason: str) -> None:
        assistant = "".join(self._assistant_parts).strip()
        self._assistant_parts.clear()
        if not assistant:
            return
        if not self._pending_users:
            self._emit("realtime_memory_orphan_assistant", reason=reason, assistant_chars=len(assistant))
            return
        user = self._pending_users.pop(0)
        if self.control is None:
            self._emit(
                "realtime_memory_no_control",
                reason=reason,
                user_chars=len(user),
                assistant_chars=len(assistant),
            )
            return
        task = asyncio.create_task(self._persist(user, assistant, reason))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _persist(self, user: str, assistant: str, reason: str) -> None:
        assert self.control is not None
        try:
            if memory_scope_registry().write_blocked(
                self.control.scope_token,
                expected_generation=self._write_generation,
            ):
                self._emit("realtime_memory_write_blocked", reason=reason)
                return
        except Exception:
            logger.exception("FDEX realtime memory ownership guard failed; suppressing write")
            self._emit("realtime_memory_write_guard_failed", reason=reason)
            return
        try:
            writer = self.writer or memory_coordinator().remember_exchange
            outcome = await writer(
                scope=MemoryScope(self.control.scope_token),
                conversation_id=self.control.conversation_id,
                employee_id=self.control.employee_id,
                user_text=user,
                assistant_text=assistant,
                write_structured=self.control.knowledge_write,
            )
            self._emit(
                "realtime_memory_saved",
                reason=reason,
                user_chars=len(user),
                assistant_chars=len(assistant),
                mempalace=bool(outcome.get("mempalace")),
                letta=bool(outcome.get("letta")),
                errors=list(outcome.get("errors") or [])[:8],
            )
        except Exception as exc:
            logger.exception("FDEX realtime text memory write failed")
            self._emit(
                "realtime_memory_save_failed",
                reason=reason,
                error_type=type(exc).__name__,
            )

    async def drain_for_test(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def _emit(self, event: str, **details: Any) -> None:
        if self.diag is not None:
            self.diag(event, **details)
