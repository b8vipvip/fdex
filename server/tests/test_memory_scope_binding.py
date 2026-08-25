from __future__ import annotations

import hashlib
from types import SimpleNamespace

from app.memory_middleware import MemoryControl
from app import memory_middleware_streamsafe


def test_http_memory_binding_registers_only_server_derived_scope(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class Registry:
        def register(self, user_id: str, scope_key: str) -> None:
            calls.append((user_id, scope_key))

    monkeypatch.setattr(memory_middleware_streamsafe, "memory_scope_registry", lambda: Registry())
    user_id = "usr_1234567890abcdef12345678"
    local = "local-scope-token-12345678901234567890"
    control = MemoryControl(
        scope_token=local,
        conversation_id="conv",
        employee_id="1",
        knowledge_read=True,
        knowledge_write=True,
        chat_access_mode="self",
        readable_employee_ids=(),
    )

    rebound = memory_middleware_streamsafe.StreamSafeFdexMemoryMiddleware._bind_user_scope(
        {"fdex_user_id": user_id},
        control,
    )
    expected = hashlib.sha256(f"{user_id}:{local}".encode()).hexdigest()
    assert rebound.scope_token == expected
    assert calls == [(user_id, expected)]
