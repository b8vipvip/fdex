from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import app.codex_interactions as interactions_module
import app.user_codex_event_routes as event_routes
from app.agent_runtime import AgentRuntimeError, AgentTask
from app.codex_host_store import CodexHostStore
from app.codex_interaction_store import CodexInteractionStore
from app.codex_interactions import (
    CodexInteractionBroker,
    approval_response,
    permissions_response,
    user_input_response,
)


OWNER = "usr_phase723_owner"
OTHER = "usr_phase723_other"
TASK = "a" * 32
OTHER_TASK = "b" * 32
THREAD = "019-phase723-thread"
TURN = "019-phase723-turn"
ITEM = "019-phase723-item"


def _stores(tmp_path: Path) -> tuple[CodexHostStore, CodexInteractionStore]:
    path = tmp_path / "codex-host.db"
    host = CodexHostStore(path)
    host.init()
    store = CodexInteractionStore(path, tmp_path / "codex-interactions.key")
    store.init()
    return host, store


def _user_input_params(*, secret: bool = True, blocking: bool = True) -> dict[str, Any]:
    return {
        "threadId": THREAD,
        "turnId": TURN,
        "itemId": ITEM,
        "isBlocking": blocking,
        "autoResolutionMs": None,
        "questions": [
            {
                "id": "token",
                "header": "Credential",
                "question": "Enter the temporary token",
                "isOther": False,
                "isSecret": secret,
                "options": None,
            }
        ],
    }


def test_secret_user_input_is_encrypted_then_destroyed_after_correct_host_claim(tmp_path: Path) -> None:
    _host, store = _stores(tmp_path)
    row = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-one",
        rpc_id=17,
        method="item/tool/requestUserInput",
        params=_user_input_params(),
    )
    response, summary = user_input_response(row, {"token": ["super-secret-value"]})
    assert response == {"answers": {"token": {"answers": ["super-secret-value"]}}}
    assert summary == {"answeredQuestionIds": ["token"], "secretQuestionIds": ["token"]}
    assert "super-secret-value" not in repr(summary)

    answered = store.submit_response(
        owner_id=OWNER,
        interaction_id=str(row["id"]),
        response=response,
        summary=summary,
    )
    assert answered["state"] == "answered"
    assert "response_cipher" not in answered
    assert "super-secret-value" not in repr(answered)

    with store.db() as conn:
        raw = conn.execute(
            "SELECT request_json,response_cipher,response_summary_json FROM codex_interactions WHERE id=?",
            (row["id"],),
        ).fetchone()
    assert raw is not None
    assert raw["response_cipher"]
    assert "super-secret-value" not in str(raw["request_json"])
    assert "super-secret-value" not in str(raw["response_cipher"])
    assert "super-secret-value" not in str(raw["response_summary_json"])

    assert store.claim_response(owner_id=OTHER, interaction_id=str(row["id"]), host_session_id="host-one") is None
    assert store.claim_response(owner_id=OWNER, interaction_id=str(row["id"]), host_session_id="wrong-host") is None
    claimed = store.claim_response(owner_id=OWNER, interaction_id=str(row["id"]), host_session_id="host-one")
    assert claimed == response

    with store.db() as conn:
        final = conn.execute("SELECT state,response_cipher FROM codex_interactions WHERE id=?", (row["id"],)).fetchone()
    assert final is not None
    assert final["state"] == "responded"
    assert final["response_cipher"] == ""
    assert store.claim_response(owner_id=OWNER, interaction_id=str(row["id"]), host_session_id="host-one") is None


def test_jsonrpc_numeric_and_string_ids_do_not_collide_inside_one_host(tmp_path: Path) -> None:
    _host, store = _stores(tmp_path)
    first = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-one",
        rpc_id=1,
        method="item/fileChange/requestApproval",
        params={"threadId": THREAD, "turnId": TURN, "itemId": "file-one", "startedAtMs": 1},
    )
    second = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-one",
        rpc_id="1",
        method="item/fileChange/requestApproval",
        params={"threadId": THREAD, "turnId": TURN, "itemId": "file-two", "startedAtMs": 2},
    )
    assert first["id"] != second["id"]
    with store.db() as conn:
        ids = [str(row[0]) for row in conn.execute("SELECT rpc_id FROM codex_interactions ORDER BY created_at").fetchall()]
    assert ids == ["i:1", "s:1"]


def test_interaction_protocol_payloads_fail_closed_instead_of_shape_truncation(tmp_path: Path) -> None:
    _host, store = _stores(tmp_path)
    with pytest.raises(ValueError, match="1 MiB"):
        store.create(
            owner_id=OWNER,
            task_id=TASK,
            host_session_id="host-one",
            rpc_id=1,
            method="item/fileChange/requestApproval",
            params={"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "reason": "x" * (1024 * 1024 + 10)},
        )

    row = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-one",
        rpc_id=2,
        method="item/tool/requestUserInput",
        params=_user_input_params(),
    )
    with pytest.raises(ValueError, match="1 MiB"):
        store.submit_response(
            owner_id=OWNER,
            interaction_id=str(row["id"]),
            response={"answers": {"token": {"answers": ["y" * (1024 * 1024 + 10)]}}},
        )
    assert store.get(OWNER, str(row["id"]))["state"] == "pending"  # type: ignore[index]


def test_orphan_interaction_is_terminalized_only_after_thread_is_not_active(tmp_path: Path) -> None:
    host, store = _stores(tmp_path)
    host.upsert_thread(owner_id=OWNER, task_id=TASK, thread_id=THREAD, project_id=7)
    host.bind_task(owner_id=OWNER, task_id=TASK, thread_id=THREAD, relation="start")
    host.record_turn_started(owner_id=OWNER, task_id=TASK, thread_id=THREAD, turn_id=TURN)
    row = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="dead-host",
        rpc_id=4,
        method="item/commandExecution/requestApproval",
        params={"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "approvalId": "approval-one", "startedAtMs": 3},
    )
    assert store.interrupt_orphans(OWNER) == 0
    assert store.get(OWNER, str(row["id"]))["state"] == "pending"  # type: ignore[index]

    host.record_turn_completed(owner_id=OWNER, thread_id=THREAD, turn_id=TURN, status="interrupted", error="worker died")
    assert store.interrupt_orphans(OWNER) == 1
    interrupted = store.get(OWNER, str(row["id"]))
    assert interrupted is not None
    assert interrupted["state"] == "interrupted"
    assert store.active_count(OWNER) == 0


def test_owner_scoped_delete_does_not_touch_another_account(tmp_path: Path) -> None:
    _host, store = _stores(tmp_path)
    own = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-a",
        rpc_id=1,
        method="item/fileChange/requestApproval",
        params={"threadId": THREAD, "turnId": TURN, "itemId": "own", "startedAtMs": 1},
    )
    other = store.create(
        owner_id=OTHER,
        task_id=OTHER_TASK,
        host_session_id="host-b",
        rpc_id=1,
        method="item/fileChange/requestApproval",
        params={"threadId": "other-thread", "turnId": "other-turn", "itemId": "other", "startedAtMs": 1},
    )
    assert store.get(OTHER, str(own["id"])) is None
    assert store.get(OWNER, str(other["id"])) is None
    assert store.delete_owner(OWNER) == 1
    assert store.get(OWNER, str(own["id"])) is None
    assert store.get(OTHER, str(other["id"])) is not None


def test_official_response_helpers_preserve_supported_protocol_shapes() -> None:
    command, command_summary = approval_response("item/commandExecution/requestApproval", "acceptForSession")
    assert command == {"decision": "acceptForSession"}
    assert command_summary == command
    file_change, _ = approval_response("item/fileChange/requestApproval", "decline")
    assert file_change == {"decision": "decline"}
    with pytest.raises(AgentRuntimeError):
        approval_response("item/fileChange/requestApproval", "acceptWithExecpolicyAmendment")

    permission_row = {
        "method": "item/permissions/requestApproval",
        "request": {"permissions": {"network": {"enabled": True}, "fileSystem": None}},
    }
    granted, granted_summary = permissions_response(permission_row, "grant_session")
    assert granted == {"permissions": {"network": {"enabled": True}}, "scope": "session"}
    assert granted_summary == {"decision": "grant", "scope": "session"}
    denied, denied_summary = permissions_response(permission_row, "deny")
    assert denied == {"permissions": {}, "scope": "turn"}
    assert denied_summary == {"decision": "deny", "scope": "turn"}

    user_row = {"method": "item/tool/requestUserInput", "request": _user_input_params()}
    answer, summary = user_input_response(user_row, {"token": ["sensitive"]})
    assert answer["answers"]["token"]["answers"] == ["sensitive"]
    assert "sensitive" not in repr(summary)
    with pytest.raises(AgentRuntimeError, match="unknown question"):
        user_input_response(user_row, {"not-a-question": ["x"]})


def test_broker_consumes_response_submitted_by_another_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host, store = _stores(tmp_path)

    class FakeItemStore:
        def record_notification(self, **kwargs: Any) -> dict[str, Any]:
            return {"seq": 1, **kwargs}

    class FakeTaskStore:
        def cancel_requested(self, task_id: str) -> bool:
            assert task_id == TASK
            return False

    monkeypatch.setattr(interactions_module, "codex_item_store", lambda: FakeItemStore())
    monkeypatch.setattr(interactions_module, "agent_task_store", lambda: FakeTaskStore())
    task = AgentTask(id=TASK, prompt="test", owner_id=OWNER)
    broker = CodexInteractionBroker(task=task, store=store, host_session_id="host-owner")

    async def run() -> dict[str, Any]:
        pending = asyncio.create_task(
            broker.handle(
                99,
                "item/fileChange/requestApproval",
                {"threadId": THREAD, "turnId": TURN, "itemId": ITEM, "startedAtMs": 10},
            )
        )
        row: dict[str, Any] | None = None
        for _ in range(100):
            rows = await asyncio.to_thread(store.list_for_task, OWNER, TASK)
            if rows:
                row = rows[0]
                break
            await asyncio.sleep(0.01)
        assert row is not None and row["state"] == "pending"
        await asyncio.to_thread(
            store.submit_response,
            owner_id=OWNER,
            interaction_id=str(row["id"]),
            response={"decision": "accept"},
            summary={"decision": "accept"},
        )
        return await asyncio.wait_for(pending, timeout=2.0)

    result = asyncio.run(run())
    assert result == {"decision": "accept"}
    rows = store.list_for_task(OWNER, TASK)
    assert rows[0]["state"] == "responded"


def test_route_helper_requires_both_owner_and_task_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeInteractionStore:
        def get(self, owner_id: str, interaction_id: str) -> dict[str, Any] | None:
            if owner_id == OWNER and interaction_id == "interaction-one":
                return {"id": interaction_id, "owner_id": owner_id, "task_id": TASK}
            return None

    monkeypatch.setattr(event_routes, "codex_interaction_store", lambda: FakeInteractionStore())
    assert event_routes._interaction_for_task(OWNER, TASK, "interaction-one")["task_id"] == TASK
    with pytest.raises(KeyError):
        event_routes._interaction_for_task(OWNER, OTHER_TASK, "interaction-one")
    with pytest.raises(KeyError):
        event_routes._interaction_for_task(OTHER, TASK, "interaction-one")


def test_phase723_runtime_ui_and_account_erasure_wiring() -> None:
    root = Path(__file__).resolve().parents[2]
    loop = (root / "server/app/agent_loop.py").read_text(encoding="utf-8")
    entry = (root / "server/app/codex_host_entry.py").read_text(encoding="utf-8")
    installer = (root / "server/app/codex_interaction_install.py").read_text(encoding="utf-8")
    client = (root / "server/app/codex_interactive_client.py").read_text(encoding="utf-8")
    routes = (root / "server/app/user_codex_event_routes.py").read_text(encoding="utf-8")
    template = (root / "server/app/templates/user_agent.html").read_text(encoding="utf-8")
    javascript = (root / "server/app/static/codex_items.js").read_text(encoding="utf-8")
    cleanup = (root / "server/app/account_cleanup.py").read_text(encoding="utf-8")

    assert "from app.codex_host_entry import run_codex_task" in loop
    assert "guarded_run_codex_task" in entry
    assert 'payload["approvalPolicy"] = "on-request"' in installer
    assert "ContextVar" in installer
    assert "_interactive_tasks.add(task)" in client
    assert "/codex/interactions/{interaction_id}/respond" in routes
    assert 'id="codex-interaction-panel"' in template
    assert 'type="{% if question.isSecret %}password{% else %}text{% endif %}"' in template
    assert "fdex/interaction/" in javascript
    assert 'input.type = question.isSecret ? "password" : "text"' in javascript
    assert "interactionRecords" in javascript
    assert "window.location.reload()" in javascript
    assert ".innerHTML" not in javascript
    assert "codex_interaction_store().delete_owner(clean)" in cleanup
    assert "interaction_store.active_count(clean)" in cleanup
