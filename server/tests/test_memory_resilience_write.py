from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anyio

from app.fdex_memory import MemoryOperationError, MemoryScope
from app import memory_resilience


def test_letta_structured_write_updates_block_without_model_generation() -> None:
    calls: dict[str, object] = {}

    class FakeBlocks:
        def retrieve(self, **kwargs):
            calls["retrieve"] = kwargs
            return SimpleNamespace(value="已有长期记忆")

        def update(self, **kwargs):
            calls["update"] = kwargs
            return SimpleNamespace(value=kwargs["value"])

    class ForbiddenMessages:
        def create(self, **_kwargs):
            raise AssertionError("Letta structured write must not call a generation model")

    fake_client = SimpleNamespace(
        agents=SimpleNamespace(blocks=FakeBlocks(), messages=ForbiddenMessages())
    )

    class FakeLetta:
        settings = SimpleNamespace(
            fdex_letta_enabled=True,
            fdex_letta_timeout_seconds=3.0,
            fdex_memory_context_max_chars=4000,
        )
        _lock = anyio.Lock()

        async def ensure_agent(self, _scope):
            return "agent-1"

        def _get_client(self):
            return fake_client

        @staticmethod
        def _operation_error(exc, operation):
            return MemoryOperationError(f"letta_{operation}_failed")

    accepted = asyncio.run(
        memory_resilience._letta_remember_to_block(
            FakeLetta(),
            scope=MemoryScope("account-token-123456789012345678901234"),
            user_text="帮我统计之前的项目记录",
            assistant_text="目前有 3 个历史项目。",
            conversation_id="employee:7",
        )
    )

    assert accepted is True
    assert "update" in calls
    update = calls["update"]
    assert update["agent_id"] == "agent-1"
    assert update["block_label"] == "human"
    assert "[FDEX_STRUCTURED_MEMORY_V2]" in update["value"]
    assert "USER_STATEMENT=帮我统计之前的项目记录" in update["value"]
    assert "ASSISTANT_RESULT=目前有 3 个历史项目。" in update["value"]
    assert len(update["value"]) <= 4000


def test_structured_memory_marks_assistant_result_as_context_not_user_fact() -> None:
    entry = memory_resilience._structured_entry(
        "预算改成 37 万",
        "好的，预算已经调整为 37 万。",
        "employee:5",
    )
    block = memory_resilience._bounded_block("", entry, 2000)
    assert "USER_STATEMENT=预算改成 37 万" in block
    assert "ASSISTANT_RESULT=" in block
    assert "预算已经调整为 37 万。" in block
    assert "ASSISTANT_RESULT is prior AI context and is not automatically a user fact" in block
