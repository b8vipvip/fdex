from __future__ import annotations

import asyncio

from app.config import Settings
from app.fdex_memory import MemoryScope, MemPalaceStore
from app.memory_middleware import MemoryControl
from app.realtime_memory import RealtimeMemoryRecorder, clean_realtime_user_text


def _control() -> MemoryControl:
    return MemoryControl(
        scope_token="R" * 32,
        conversation_id="realtime:employee:7",
        employee_id="7",
        knowledge_read=True,
        knowledge_write=True,
        chat_access_mode="self",
        readable_employee_ids=(),
    )


def test_realtime_storage_cleans_fdex_context_and_keeps_plain_text_only() -> None:
    wrapped = (
        "继续说这个项目\n\n<fdex_company_context>\n"
        "知识库候选资料：内部资料\n</fdex_company_context>"
    )
    assert clean_realtime_user_text(wrapped) == "继续说这个项目"


def test_realtime_recorder_pairs_transcripts_and_never_needs_audio() -> None:
    saved: list[dict[str, object]] = []

    async def writer(**kwargs):
        saved.append(kwargs)
        return {"mempalace": True, "letta": True, "errors": []}

    async def scenario() -> None:
        recorder = RealtimeMemoryRecorder(_control(), writer=writer)
        recorder.add_user("我今天决定把预算改成 100 万", source="voice")
        recorder.add_assistant_delta("好的，")
        recorder.add_assistant_delta("我记住这个决定。")
        recorder.complete(reason="done")
        await recorder.drain_for_test()

    asyncio.run(scenario())
    assert len(saved) == 1
    assert saved[0]["user_text"] == "我今天决定把预算改成 100 万"
    assert saved[0]["assistant_text"] == "好的，我记住这个决定。"
    assert saved[0]["write_structured"] is True
    assert all("audio" not in key.lower() for key in saved[0])


def test_interrupt_then_done_does_not_duplicate_partial_reply() -> None:
    saved: list[dict[str, object]] = []

    async def writer(**kwargs):
        saved.append(kwargs)
        return {"mempalace": True, "letta": True, "errors": []}

    async def scenario() -> None:
        recorder = RealtimeMemoryRecorder(_control(), writer=writer)
        recorder.add_user("先回答这一句", source="voice")
        recorder.add_assistant_delta("回答到一半")
        recorder.complete(reason="interrupt")
        recorder.complete(reason="done")
        await recorder.drain_for_test()

    asyncio.run(scenario())
    assert len(saved) == 1
    assert saved[0]["assistant_text"] == "回答到一半"


def test_mempalace_recent_obeys_employee_acl_without_embedding(tmp_path) -> None:
    async def scenario() -> None:
        settings = Settings(
            fdex_memory_data_dir=str(tmp_path),
            fdex_memory_proxy_token="test-token-for-local-unit-test-only",
        )
        store = MemPalaceStore(settings)
        scope = MemoryScope("scope-token-123456789012345678901234")
        await store.initialize()
        items7 = store._build_items(scope, "realtime:7", "员工7用户文本", "员工7回复", "7")
        items9 = store._build_items(scope, "realtime:9", "员工9用户文本", "员工9回复", "9")
        store._store_drawers_sync(items7 + items9)
        only7 = await store.recent(scope, {"7"}, limit=20)
        assert only7
        assert {item["employee_id"] for item in only7} == {"7"}
        assert any("员工7用户文本" in item["text"] for item in only7)
        assert all("员工9" not in item["text"] for item in only7)
        await store.aclose()

    asyncio.run(scenario())
