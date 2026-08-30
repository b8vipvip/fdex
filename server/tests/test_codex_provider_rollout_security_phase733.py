from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from app import codex_provider_compatibility as compatibility
from app import codex_provider_smoke as smoke
from app import codex_provider_smoke_mcp as smoke_mcp


def _request(*, token: str, headers: list[tuple[bytes, bytes]], body: bytes = b"") -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/internal/codex-provider-smoke-mcp/{token}",
        "raw_path": f"/internal/codex-provider-smoke-mcp/{token}".encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 42123),
        "server": ("127.0.0.1", 8000),
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_smoke_mcp_rejects_reverse_proxied_request_even_when_peer_is_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    monkeypatch.setattr(smoke_mcp, "codex_provider_compatibility_store", lambda: store)
    token = store.issue_smoke_capability("PHASE733-PROXY")
    request = _request(
        token=token,
        headers=[
            (b"content-type", b"application/json"),
            (b"x-forwarded-for", b"203.0.113.9"),
        ],
        body=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
    )

    response = await smoke_mcp.codex_provider_smoke_mcp(token, request)

    assert response.status_code == 404
    assert store.smoke_capability(token) is not None


@pytest.mark.asyncio
async def test_smoke_mcp_accepts_direct_loopback_capability_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    monkeypatch.setattr(smoke_mcp, "codex_provider_compatibility_store", lambda: store)
    token = store.issue_smoke_capability("PHASE733-DIRECT")
    request = _request(
        token=token,
        headers=[(b"content-type", b"application/json")],
        body=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
    )

    response = await smoke_mcp.codex_provider_smoke_mcp(token, request)

    assert response.status_code == 200
    assert b"fdex-codex-provider-smoke" in response.body


class _FakeCodexClient:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, Any]]] = [
            (
                "item/started",
                {
                    "turnId": "turn-733",
                    "item": {
                        "type": "collabAgentToolCall",
                        "id": "spawn-1",
                        "tool": "spawnAgent",
                        "status": "inProgress",
                    },
                },
            ),
            (
                "item/completed",
                {
                    "turnId": "turn-733",
                    "item": {
                        "type": "collabAgentToolCall",
                        "id": "spawn-1",
                        "tool": "spawnAgent",
                        "status": "completed",
                    },
                },
            ),
            (
                "item/started",
                {
                    "turnId": "turn-733",
                    "item": {
                        "type": "subAgentActivity",
                        "id": "spawn-1",
                        "kind": "started",
                        "agentThreadId": "child-733",
                        "agentPath": "/root/smoke-child",
                    },
                },
            ),
            (
                "item/completed",
                {
                    "turnId": "turn-733",
                    "item": {
                        "type": "subAgentActivity",
                        "id": "spawn-1",
                        "kind": "started",
                        "agentThreadId": "child-733",
                        "agentPath": "/root/smoke-child",
                    },
                },
            ),
            (
                "item/completed",
                {
                    "turnId": "turn-733",
                    "item": {
                        "type": "collabAgentToolCall",
                        "id": "wait-1",
                        "tool": "wait",
                        "status": "completed",
                    },
                },
            ),
            (
                "item/completed",
                {
                    "turnId": "turn-733",
                    "item": {"type": "agentMessage", "id": "msg-1", "text": "PHASE733-DONE"},
                },
            ),
            (
                "turn/completed",
                {"turn": {"id": "turn-733", "status": "completed"}},
            ),
        ]

    async def request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        assert method == "turn/start"
        assert params["threadId"] == "thread-733"
        assert timeout > 0
        return {"turn": {"id": "turn-733"}}

    async def next_notification(self, *, timeout: float) -> tuple[str, dict[str, Any]]:
        assert timeout > 0
        return self.notifications.pop(0)


@pytest.mark.asyncio
async def test_turn_evidence_distinguishes_completed_collaboration_and_subagent_lifecycle() -> None:
    result = await smoke._run_turn(
        _FakeCodexClient(),  # type: ignore[arg-type]
        "thread-733",
        "run sub-agent smoke",
        timeout=30.0,
    )

    assert result["text"] == "PHASE733-DONE"
    assert result["completed_collab_tools"] == ["spawnAgent", "wait"]
    assert "subAgentActivity" in result["item_types"]
    assert "started" in result["subagent_activities"]


def test_full_smoke_source_requires_completed_spawn_wait_and_subagent_activity() -> None:
    source = Path(smoke.__file__).read_text(encoding="utf-8")

    assert 'if "spawnAgent" not in completed_collab:' in source
    assert 'if "wait" not in completed_collab:' in source
    assert 'if "subAgentActivity" not in sub_types:' in source
    assert 'evidence["subagent"] = True' in source
