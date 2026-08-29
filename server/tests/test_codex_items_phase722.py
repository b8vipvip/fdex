from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.codex_item_store as item_module
import app.codex_notification_bus as bus_module
import app.user_codex_event_routes as event_routes
from app.codex_item_store import CodexItemStore
from app.codex_notification_bus import CodexNotificationCapture


OWNER = "usr_phase722_owner"
OTHER = "usr_phase722_other"
TASK = "7" * 32
THREAD = "019-phase722-thread"
TURN = "019-phase722-turn"
ITEM = "019-phase722-item"


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CodexItemStore:
    host = SimpleNamespace(path=tmp_path / "codex-host.db", init=lambda: None)
    monkeypatch.setattr(item_module, "codex_host_store", lambda: host)
    return CodexItemStore()


def _started(item_type: str = "agentMessage") -> dict[str, Any]:
    return {
        "threadId": THREAD,
        "turnId": TURN,
        "item": {"id": ITEM, "type": item_type, "status": "inProgress"},
        "startedAtMs": 100,
    }


def test_item_projection_persists_started_deltas_and_completed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    first = store.record_notification(owner_id=OWNER, task_id=TASK, method="item/started", params=_started())
    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="item/agentMessage/delta",
        params={"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "delta": "hello "},
    )
    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="item/agentMessage/delta",
        params={"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "delta": "world"},
    )

    live = store.list_items(OWNER, TASK)
    assert len(live) == 1
    assert live[0]["status"] == "inProgress"
    assert live[0]["delta_text"] == "hello world"

    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="item/completed",
        params={
            "threadId": THREAD,
            "turnId": TURN,
            "item": {"id": ITEM, "type": "agentMessage", "status": "completed", "text": "hello world"},
            "completedAtMs": 200,
        },
    )
    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="thread/tokenUsage/updated",
        params={"threadId": THREAD, "turnId": TURN, "futureField": {"kept": True}},
    )

    completed = store.list_items(OWNER, TASK)[0]
    assert completed["status"] == "completed"
    assert completed["payload"]["text"] == "hello world"
    assert completed["delta_text"] == "hello world"
    events = store.list_events(OWNER, TASK, after_seq=int(first["seq"]) - 1, limit=20)
    assert [event["method"] for event in events] == [
        "item/started",
        "item/agentMessage/delta",
        "item/agentMessage/delta",
        "item/completed",
        "thread/tokenUsage/updated",
    ]
    assert events[-1]["params"]["futureField"] == {"kept": True}


def test_turn_completion_marks_missing_item_completed_as_orphaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_notification(owner_id=OWNER, task_id=TASK, method="item/started", params=_started("commandExecution"))
    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="item/commandExecution/outputDelta",
        params={"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "delta": "partial output\n"},
    )
    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="turn/completed",
        params={"threadId": THREAD, "turnId": TURN, "turn": {"id": TURN, "status": "completed"}},
    )
    item = store.list_items(OWNER, TASK)[0]
    assert item["status"] == "orphaned"
    assert item["delta_text"] == "partial output\n"


def test_delta_before_item_started_is_recovered_into_placeholder_then_promoted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="item/reasoning/summaryTextDelta",
        params={"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "delta": "early"},
    )
    placeholder = store.list_items(OWNER, TASK)[0]
    assert placeholder["item_type"] == "stream"
    assert placeholder["delta_text"] == "early"

    store.record_notification(owner_id=OWNER, task_id=TASK, method="item/started", params=_started("reasoning"))
    promoted = store.list_items(OWNER, TASK)[0]
    assert promoted["item_type"] == "reasoning"
    assert promoted["delta_text"] == "early"


def test_owner_and_task_scope_prevents_item_event_leakage_and_cleanup_is_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_notification(owner_id=OWNER, task_id=TASK, method="item/started", params=_started())
    store.record_notification(owner_id=OTHER, task_id=TASK, method="item/started", params=_started())

    assert len(store.list_items(OWNER, TASK)) == 1
    assert len(store.list_items(OTHER, TASK)) == 1
    assert len(store.list_events(OWNER, TASK)) == 1
    assert len(store.list_events(OTHER, TASK)) == 1

    removed = store.delete_owner(OWNER)
    assert removed == {"events": 1, "items": 1}
    assert store.list_items(OWNER, TASK) == []
    assert store.list_events(OWNER, TASK) == []
    assert len(store.list_items(OTHER, TASK)) == 1


def test_large_future_protocol_event_is_bounded_without_malformed_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    event = store.record_notification(
        owner_id=OWNER,
        task_id=TASK,
        method="item/futureHugeNotification",
        params={"threadId": THREAD, "payload": "x" * (1024 * 1024 + 100_000)},
    )
    assert event["params"]["fdex_truncated"] is True
    assert event["params"]["original_bytes"] > 1024 * 1024
    assert isinstance(event["params"]["preview"], str)


def test_event_route_scope_uses_authenticated_owner_not_task_id_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTasks:
        def get(self, owner_id: str, task_id: str) -> dict[str, object] | None:
            if owner_id == OWNER and task_id == TASK:
                return {"id": TASK, "owner_id": OWNER}
            return None

    monkeypatch.setattr(event_routes, "agent_task_store", lambda: FakeTasks())
    monkeypatch.setattr(event_routes, "_current_user", lambda _request: {"id": OWNER})
    owner_id, task, error = event_routes._scope(object(), TASK)  # type: ignore[arg-type]
    assert owner_id == OWNER
    assert task is not None
    assert error is None

    monkeypatch.setattr(event_routes, "_current_user", lambda _request: {"id": OTHER})
    owner_id, task, error = event_routes._scope(object(), TASK)  # type: ignore[arg-type]
    assert owner_id == OTHER
    assert task is None
    assert error is not None and error.status_code == 404


def test_sse_resumes_from_cursor_and_emits_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTasks:
        def get(self, owner_id: str, task_id: str) -> dict[str, object] | None:
            return {"id": task_id} if owner_id == OWNER and task_id == TASK else None

    class FakeStore:
        def list_events(self, owner_id: str, task_id: str, *, after_seq: int, limit: int) -> list[dict[str, Any]]:
            assert owner_id == OWNER and task_id == TASK and limit == 200
            if after_seq < 9:
                return [{"seq": 9, "method": "item/agentMessage/delta", "params": {"delta": "x"}}]
            return []

    class FakeRequest:
        headers = {"last-event-id": "7"}

        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr(event_routes, "agent_task_store", lambda: FakeTasks())
    monkeypatch.setattr(event_routes, "_current_user", lambda _request: {"id": OWNER})
    monkeypatch.setattr(event_routes, "codex_item_store", lambda: FakeStore())

    async def run() -> str:
        response = await event_routes.codex_item_events(TASK, FakeRequest(), after=8)  # type: ignore[arg-type]
        chunk = await anext(response.body_iterator)  # type: ignore[attr-defined]
        await response.body_iterator.aclose()  # type: ignore[attr-defined]
        return chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)

    first = asyncio.run(run())
    assert "id: 9" in first
    assert "event: codex" in first
    assert '"method":"item/agentMessage/delta"' in first


def test_capture_writer_failure_does_not_deadlock_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingStore:
        def record_notification(self, **_kwargs: Any) -> None:
            raise RuntimeError("sqlite unavailable")

    monkeypatch.setattr(bus_module, "codex_item_store", lambda: FailingStore())

    async def run() -> None:
        capture = CodexNotificationCapture(OWNER, TASK, max_pending=128)
        await capture.start()
        await capture.publish("item/started", {"threadId": THREAD})
        await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError, match="sqlite unavailable"):
            await asyncio.wait_for(capture.close(), timeout=1.0)

    asyncio.run(run())


def test_phase722_ui_transport_and_account_erasure_wiring() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/user_agent.html").read_text(encoding="utf-8")
    javascript = (root / "server/app/static/codex_items.js").read_text(encoding="utf-8")
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    transport = (root / "server/app/codex_app_server.py").read_text(encoding="utf-8")
    guard = (root / "server/app/codex_host_guard.py").read_text(encoding="utf-8")
    cleanup = (root / "server/app/account_cleanup.py").read_text(encoding="utf-8")

    assert 'id="codex-item-panel"' in template
    assert 'src="/static/codex_items.js"' in template
    assert "new EventSource(" in javascript
    assert "textContent" in javascript
    assert "replaceChildren" in javascript
    assert ".innerHTML" not in javascript
    for item_type in (
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
        "subAgentActivity",
        "webSearch",
        "imageView",
        "imageGeneration",
        "reasoning",
        "plan",
        "contextCompaction",
    ):
        assert item_type in javascript
    assert "user_codex_event_router" in main
    assert "publish_transport_notification" in transport
    assert "CodexNotificationCapture" in guard
    assert "codex_item_store().delete_owner(clean)" in cleanup
