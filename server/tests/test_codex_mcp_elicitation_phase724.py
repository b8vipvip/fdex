from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import app.codex_interactions as interactions_module
from app.agent_runtime import AgentRuntimeError, AgentTask
from app.codex_host_store import CodexHostStore
from app.codex_interaction_store import CodexInteractionStore
from app.codex_interactions import CodexInteractionBroker
from app.codex_mcp_elicitation import (
    MCP_ELICITATION_METHOD,
    decorate_mcp_interaction,
    install_mcp_elicitation_compat,
    mcp_elicitation_response,
    safe_https_url,
)


OWNER = "usr_phase724_owner"
OTHER = "usr_phase724_other"
TASK = "c" * 32
THREAD = "019-phase724-thread"
TURN = "019-phase724-turn"


def _stores(tmp_path: Path) -> tuple[CodexHostStore, CodexInteractionStore]:
    path = tmp_path / "codex-host.db"
    host = CodexHostStore(path)
    host.init()
    store = CodexInteractionStore(path, tmp_path / "codex-interactions.key")
    store.init()
    return host, store


def _form_params() -> dict[str, Any]:
    return {
        "threadId": THREAD,
        "turnId": TURN,
        "serverName": "example_mcp",
        "mode": "form",
        "_meta": None,
        "message": "Configure the operation",
        "requestedSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "title": "Email",
                    "format": "email",
                    "minLength": 5,
                    "maxLength": 120,
                },
                "retries": {"type": "integer", "minimum": 1, "maximum": 5},
                "enabled": {"type": "boolean", "default": True},
                "mode": {"type": "string", "enum": ["safe", "fast"], "default": "safe"},
                "regions": {
                    "type": "array",
                    "items": {"enum": ["us", "eu", "ap"]},
                    "minItems": 1,
                    "maxItems": 2,
                },
                "note": {"type": "string", "default": "default-note"},
            },
            "required": ["email", "retries", "regions"],
        },
    }


def _token_for(projected: dict[str, Any], title: str) -> str:
    questions = projected["request"]["questions"]
    for question in questions:
        if str(question.get("header") or "") == title:
            return str(question["id"])
    raise AssertionError(f"question {title!r} not found")


def _action_token(projected: dict[str, Any]) -> str:
    questions = projected["request"]["questions"]
    for question in questions:
        if str(question.get("header") or "") == "MCP action":
            return str(question["id"])
    raise AssertionError("MCP action question not found")


def test_mcp_method_registers_with_existing_durable_store_without_new_secret_table(tmp_path: Path) -> None:
    install_mcp_elicitation_compat()
    _host, store = _stores(tmp_path)
    row = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-one",
        rpc_id=41,
        method=MCP_ELICITATION_METHOD,
        params=_form_params(),
    )
    assert row["method"] == MCP_ELICITATION_METHOD
    assert row["thread_id"] == THREAD
    assert row["turn_id"] == TURN
    assert store.get(OTHER, str(row["id"])) is None


def test_browser_projection_reuses_request_user_input_without_changing_persisted_protocol_row() -> None:
    row = {
        "id": "interaction-one",
        "method": MCP_ELICITATION_METHOD,
        "thread_id": THREAD,
        "turn_id": TURN,
        "state": "pending",
        "request": _form_params(),
    }
    projected = decorate_mcp_interaction(row)
    assert row["method"] == MCP_ELICITATION_METHOD
    assert projected["protocol_method"] == MCP_ELICITATION_METHOD
    assert projected["method"] == "item/tool/requestUserInput"
    assert projected["mcp_mode"] == "form"
    assert projected["mcp_server_name"] == "example_mcp"
    assert projected["mcp_accept_supported"] is True
    questions = projected["request"]["questions"]
    assert questions[0]["header"] == "MCP action"
    assert {option["label"] for option in questions[0]["options"]} == {"accept", "decline", "cancel"}
    assert any(question["header"] == "Email" for question in questions)


def test_standard_form_returns_exact_official_shape_and_redacted_summary() -> None:
    row = {"method": MCP_ELICITATION_METHOD, "request": _form_params()}
    projected = decorate_mcp_interaction(row)
    values = {
        _action_token(projected): ["accept"],
        _token_for(projected, "Email"): ["person@example.com"],
        _token_for(projected, "retries"): ["3"],
        _token_for(projected, "regions"): ["us", "eu"],
    }
    response, summary = mcp_elicitation_response(row, values)
    assert response == {
        "action": "accept",
        "content": {
            "email": "person@example.com",
            "retries": 3,
            "enabled": True,
            "mode": "safe",
            "regions": ["us", "eu"],
            "note": "default-note",
        },
        "_meta": None,
    }
    assert summary["action"] == "accept"
    assert summary["mode"] == "form"
    assert summary["serverName"] == "example_mcp"
    assert summary["fieldCount"] == 6
    assert set(summary["fieldNames"]) == {"email", "retries", "enabled", "mode", "regions", "note"}
    assert "person@example.com" not in repr(summary)
    assert "default-note" not in repr(summary)


def test_form_validation_rejects_missing_required_multiple_actions_enum_injection_and_unknown_field() -> None:
    row = {"method": MCP_ELICITATION_METHOD, "request": _form_params()}
    projected = decorate_mcp_interaction(row)
    action = _action_token(projected)
    email = _token_for(projected, "Email")
    retries = _token_for(projected, "retries")
    regions = _token_for(projected, "regions")
    mode = _token_for(projected, "mode")

    with pytest.raises(AgentRuntimeError, match="required"):
        mcp_elicitation_response(row, {action: ["accept"], retries: ["2"], regions: ["us"]})
    with pytest.raises(AgentRuntimeError, match="exactly one"):
        mcp_elicitation_response(
            row,
            {action: ["accept", "decline"], email: ["a@example.com"], retries: ["2"], regions: ["us"]},
        )
    with pytest.raises(AgentRuntimeError, match="allowed enum"):
        mcp_elicitation_response(
            row,
            {action: ["accept"], email: ["a@example.com"], retries: ["2"], regions: ["us"], mode: ["root"]},
        )
    with pytest.raises(AgentRuntimeError, match="unknown fields"):
        mcp_elicitation_response(
            row,
            {
                action: ["accept"],
                email: ["a@example.com"],
                retries: ["2"],
                regions: ["us"],
                "__not_a_field__": ["x"],
            },
        )


def test_decline_and_cancel_never_require_form_content() -> None:
    row = {"method": MCP_ELICITATION_METHOD, "request": _form_params()}
    projected = decorate_mcp_interaction(row)
    action = _action_token(projected)
    decline, decline_summary = mcp_elicitation_response(row, {action: ["decline"]})
    cancel, cancel_summary = mcp_elicitation_response(row, {action: ["cancel"]})
    assert decline == {"action": "decline", "content": None, "_meta": None}
    assert cancel == {"action": "cancel", "content": None, "_meta": None}
    assert decline_summary["action"] == "decline"
    assert cancel_summary["action"] == "cancel"


def test_url_mode_accepts_only_generic_credential_free_https_and_redacts_summary() -> None:
    url = "https://example.com/authorize?state=very-sensitive-state"
    row = {
        "method": MCP_ELICITATION_METHOD,
        "request": {
            "threadId": THREAD,
            "turnId": TURN,
            "serverName": "generic_mcp",
            "mode": "url",
            "_meta": None,
            "message": "Complete sign-in",
            "url": url,
            "elicitationId": "flow-one",
        },
    }
    projected = decorate_mcp_interaction(row)
    action = _action_token(projected)
    response, summary = mcp_elicitation_response(row, {action: ["accept"]})
    assert response == {"action": "accept", "content": None, "_meta": None}
    assert summary == {
        "action": "accept",
        "mode": "url",
        "serverName": "generic_mcp",
        "externalHost": "example.com",
    }
    assert "very-sensitive-state" not in repr(summary)
    assert safe_https_url("http://example.com/action") is None
    assert safe_https_url("https://user:password@example.com/action") is None

    insecure = dict(row)
    insecure["request"] = dict(row["request"], url="http://example.com/action")
    insecure_projection = decorate_mcp_interaction(insecure)
    assert insecure_projection["mcp_accept_supported"] is False
    with pytest.raises(AgentRuntimeError, match="HTTPS"):
        mcp_elicitation_response(insecure, {_action_token(insecure_projection): ["accept"]})


def test_codex_apps_and_openai_specific_forms_remain_fail_closed_for_accept() -> None:
    codex_apps = {
        "method": MCP_ELICITATION_METHOD,
        "request": {
            "threadId": THREAD,
            "turnId": TURN,
            "serverName": "codex_apps",
            "mode": "url",
            "message": "Authenticate connector",
            "url": "https://chatgpt.com/apps/auth?state=secret",
            "elicitationId": "connector-one",
        },
    }
    projected = decorate_mcp_interaction(codex_apps)
    assert projected["mcp_accept_supported"] is False
    action = _action_token(projected)
    with pytest.raises(AgentRuntimeError, match="does not proxy ChatGPT"):
        mcp_elicitation_response(codex_apps, {action: ["accept"]})
    decline, _summary = mcp_elicitation_response(codex_apps, {action: ["decline"]})
    assert decline["action"] == "decline"

    openai_form = {
        "method": MCP_ELICITATION_METHOD,
        "request": {
            "threadId": THREAD,
            "turnId": TURN,
            "serverName": "server-one",
            "mode": "openai/form",
            "message": "Private form",
            "requestedSchema": {"type": "object"},
        },
    }
    openai_projection = decorate_mcp_interaction(openai_form)
    assert openai_projection["mcp_accept_supported"] is False
    openai_action = _action_token(openai_projection)
    with pytest.raises(AgentRuntimeError, match="OpenAI-specific"):
        mcp_elicitation_response(openai_form, {openai_action: ["accept"]})
    declined, _ = mcp_elicitation_response(openai_form, {openai_action: ["decline"]})
    assert declined == {"action": "decline", "content": None, "_meta": None}


def test_mcp_form_values_are_encrypted_then_ciphertext_is_destroyed_after_host_claim(tmp_path: Path) -> None:
    install_mcp_elicitation_compat()
    _host, store = _stores(tmp_path)
    row = store.create(
        owner_id=OWNER,
        task_id=TASK,
        host_session_id="host-one",
        rpc_id="mcp-request-one",
        method=MCP_ELICITATION_METHOD,
        params=_form_params(),
    )
    projected = decorate_mcp_interaction(row)
    response, summary = mcp_elicitation_response(
        row,
        {
            _action_token(projected): ["accept"],
            _token_for(projected, "Email"): ["secret-user@example.com"],
            _token_for(projected, "retries"): ["2"],
            _token_for(projected, "regions"): ["ap"],
        },
    )
    answered = store.submit_response(
        owner_id=OWNER,
        interaction_id=str(row["id"]),
        response=response,
        summary=summary,
    )
    assert answered["state"] == "answered"
    assert "secret-user@example.com" not in repr(answered)
    with store.db() as conn:
        raw = conn.execute(
            "SELECT response_cipher,response_summary_json FROM codex_interactions WHERE id=?",
            (row["id"],),
        ).fetchone()
    assert raw is not None and raw["response_cipher"]
    assert "secret-user@example.com" not in str(raw["response_cipher"])
    assert "secret-user@example.com" not in str(raw["response_summary_json"])

    assert store.claim_response(owner_id=OTHER, interaction_id=str(row["id"]), host_session_id="host-one") is None
    claimed = store.claim_response(owner_id=OWNER, interaction_id=str(row["id"]), host_session_id="host-one")
    assert claimed == response
    with store.db() as conn:
        final = conn.execute("SELECT state,response_cipher FROM codex_interactions WHERE id=?", (row["id"],)).fetchone()
    assert final is not None
    assert final["state"] == "responded"
    assert final["response_cipher"] == ""


def test_broker_delivers_mcp_response_submitted_from_another_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_mcp_elicitation_compat()
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
    task = AgentTask(id=TASK, prompt="mcp bridge", owner_id=OWNER)
    broker = CodexInteractionBroker(task=task, store=store, host_session_id="host-owner")

    async def run() -> dict[str, Any]:
        pending = asyncio.create_task(broker.handle(88, MCP_ELICITATION_METHOD, _form_params()))
        row: dict[str, Any] | None = None
        for _ in range(100):
            rows = await asyncio.to_thread(store.list_for_task, OWNER, TASK)
            if rows:
                row = rows[0]
                break
            await asyncio.sleep(0.01)
        assert row is not None and row["state"] == "pending"
        projected = decorate_mcp_interaction(row)
        response, summary = mcp_elicitation_response(
            row,
            {
                _action_token(projected): ["accept"],
                _token_for(projected, "Email"): ["worker@example.com"],
                _token_for(projected, "retries"): ["4"],
                _token_for(projected, "regions"): ["eu"],
            },
        )
        await asyncio.to_thread(
            store.submit_response,
            owner_id=OWNER,
            interaction_id=str(row["id"]),
            response=response,
            summary=summary,
        )
        return await asyncio.wait_for(pending, timeout=2.0)

    result = asyncio.run(run())
    assert result["action"] == "accept"
    assert result["content"]["email"] == "worker@example.com"
    assert store.list_for_task(OWNER, TASK)[0]["state"] == "responded"


def test_phase724_wiring_keeps_mcp_credentials_and_arbitrary_stdio_out_of_scope() -> None:
    root = Path(__file__).resolve().parents[2]
    install = (root / "server/app/codex_interaction_install.py").read_text(encoding="utf-8")
    routes = (root / "server/app/user_codex_event_routes.py").read_text(encoding="utf-8")
    helper = (root / "server/app/codex_mcp_elicitation.py").read_text(encoding="utf-8")
    status = (root / "CURRENT_STATUS.md").read_text(encoding="utf-8")

    assert "install_mcp_elicitation_compat()" in install
    assert "MCP_ELICITATION_METHOD" in routes
    assert "decorate_mcp_interaction" in routes
    assert "mcp_elicitation_response" in routes
    assert "mcpServer/elicitation/request" in helper
    assert "serverName == \"codex_apps\"" in helper
    assert "OpenAI-specific" in helper
    assert "stdio" in status.lower()
    assert "MCP" in status
