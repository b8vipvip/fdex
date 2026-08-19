from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace

from app.fdex_memory import MemoryOperationError, MemoryScope
from app import memory_provider_proxy_v2
from app import memory_resilience


def test_local_text_hash_embedding_is_deterministic_and_normalized() -> None:
    first = memory_provider_proxy_v2._hash_text("帮我统计我之前的项目记录", 128)
    second = memory_provider_proxy_v2._hash_text("帮我统计我之前的项目记录", 128)
    other = memory_provider_proxy_v2._hash_text("今天天气", 128)
    assert first == second
    assert first != other
    assert len(first) == 128
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-9)


def test_embedding_fallback_never_calls_a_chat_completion(monkeypatch) -> None:
    async def no_native(_request, _values):
        return None

    monkeypatch.setattr(memory_provider_proxy_v2, "_native_embeddings", no_native)
    monkeypatch.setattr(
        memory_provider_proxy_v2,
        "_settings",
        lambda: SimpleNamespace(
            fdex_memory_recall_timeout_seconds=12.0,
            fdex_memory_embedding_dimension=128,
            fdex_memory_embedding_model="text-embedding-3-small",
        ),
    )

    class FakeRequest:
        async def json(self):
            return {"input": ["帮我统计我之前的项目记录"]}

    response = asyncio.run(memory_provider_proxy_v2.embeddings(FakeRequest()))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["fdex_fallback"] == "local_text_hash"
    assert len(payload["data"]) == 1
    assert len(payload["data"][0]["embedding"]) == 128
    # The v2 path deliberately has no semantic-tag/chat fallback function.
    assert not hasattr(memory_provider_proxy_v2, "_semantic_tags")


def test_mempalace_uses_raw_lexical_history_when_semantic_recall_fails(monkeypatch) -> None:
    async def semantic_failure(_self, _query, _scope, _allowed):
        raise MemoryOperationError("mempalace_embedding_unavailable")

    monkeypatch.setattr(memory_resilience, "_ORIGINAL_MEMPALACE_SEARCH", semantic_failure)

    class FakeStore:
        settings = SimpleNamespace(fdex_memory_recall_timeout_seconds=12.0, fdex_memory_recall_limit=6)

        async def recent(self, _scope, _allowed_employee_ids=None, limit=None):
            assert limit is not None and limit >= 30
            return [
                {
                    "drawer_id": "project",
                    "wing": "company",
                    "room": "conversation",
                    "role": "user",
                    "conversation_id": "employee:7",
                    "text": "之前的项目记录包括 FDEX 客户端设置中心和长期记忆接入。",
                },
                {
                    "drawer_id": "weather",
                    "wing": "company",
                    "room": "conversation",
                    "role": "user",
                    "conversation_id": "employee:7",
                    "text": "今天天气不错。",
                },
            ]

    result = asyncio.run(
        memory_resilience._mempalace_search_resilient(
            FakeStore(),
            "统计之前的项目记录",
            MemoryScope("account-token-123456789012345678901234"),
            {"7"},
        )
    )
    assert result
    assert result[0]["drawer_id"] == "project"
    assert result[0]["similarity"] > 0


def test_letta_recall_reads_human_block_without_messages_create(monkeypatch) -> None:
    class FakeBlocks:
        def retrieve(self, **kwargs):
            assert kwargs["agent_id"] == "agent-1"
            assert kwargs["block_label"] == "human"
            return SimpleNamespace(value="项目：FDEX；状态：设置中心已完成。")

    class ForbiddenMessages:
        def create(self, **_kwargs):
            raise AssertionError("request-time recall must not generate a Letta model message")

    fake_client = SimpleNamespace(
        agents=SimpleNamespace(blocks=FakeBlocks(), messages=ForbiddenMessages())
    )

    class FakeLetta:
        settings = SimpleNamespace(
            fdex_letta_enabled=True,
            fdex_letta_timeout_seconds=3.0,
            fdex_memory_context_max_chars=8000,
        )

        async def ensure_agent(self, _scope):
            return "agent-1"

        def _get_client(self):
            return fake_client

        @staticmethod
        def _operation_error(exc, operation):
            return MemoryOperationError(f"letta_{operation}_failed")

    value = asyncio.run(
        memory_resilience._letta_recall_from_block(
            FakeLetta(),
            "之前的项目记录",
            MemoryScope("account-token-123456789012345678901234"),
        )
    )
    assert "FDEX" in value
    assert "设置中心已完成" in value
