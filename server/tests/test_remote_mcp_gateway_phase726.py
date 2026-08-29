from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

import app.codex_remote_mcp_install as install_module
import app.remote_mcp_gateway as gateway_module
from app.remote_mcp_gateway import (
    PinnedResolver,
    RemoteMcpLeaseStore,
    build_codex_remote_mcp_config,
    enforce_tool_allowlist,
    remote_mcp_gateway,
)
from app.remote_mcp_registry import RemoteMcpRegistry


OWNER = "usr_phase726_owner"
OTHER = "usr_phase726_other"
TASK = "task_phase726_aaaaaaaaaaaaaaaa"


def _registry(tmp_path: Path) -> tuple[RemoteMcpRegistry, dict[str, Any]]:
    registry = RemoteMcpRegistry(tmp_path / "remote-mcp.sqlite3")
    server = registry.save(
        OWNER,
        name="docs",
        url="https://example.com/mcp",
        enabled_tools=["search_docs", "read_page"],
        enabled=True,
        startup_timeout_sec=12,
        tool_timeout_sec=45,
        resolve_dns=False,
    )
    return registry, server


def _request(
    *,
    method: str = "POST",
    client: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    query: bytes = b"",
) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    if body and not any(key == b"content-length" for key, _ in raw_headers):
        raw_headers.append((b"content-length", str(len(body)).encode()))
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/internal/codex-mcp/lease_test",
        "raw_path": b"/internal/codex-mcp/lease_test",
        "query_string": query,
        "headers": raw_headers,
        "client": (client, 45678),
        "server": ("127.0.0.1", 18080),
    }
    return Request(scope, receive)


def test_capability_lease_stores_only_hash_and_is_subordinate_to_live_registry(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    leases = RemoteMcpLeaseStore(registry)
    lease, token = leases.issue(OWNER, TASK, str(server["id"]))
    assert len(token) >= 32
    assert token not in str(lease)
    assert leases.resolve(str(lease["id"]), "wrong-token-that-is-long-enough-xxxxxxxx") is None
    resolved = leases.resolve(str(lease["id"]), token)
    assert resolved is not None
    assert resolved[0]["owner_id"] == OWNER
    assert resolved[1]["name"] == "docs"

    with registry.db() as conn:
        row = conn.execute("SELECT token_hash FROM remote_mcp_leases WHERE id=?", (lease["id"],)).fetchone()
    assert row is not None
    assert str(row["token_hash"]) != token
    assert token not in str(row["token_hash"])

    # The registry is authoritative on every request. Disabling does not need DNS and immediately
    # invalidates an already-issued localhost capability.
    registry.set_enabled(OWNER, str(server["id"]), False)
    assert leases.resolve(str(lease["id"]), token) is None


def test_task_revoke_and_owner_delete_are_scoped(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    leases = RemoteMcpLeaseStore(registry)
    lease_one, token_one = leases.issue(OWNER, TASK, str(server["id"]))
    lease_two, token_two = leases.issue(OWNER, "task_other", str(server["id"]))
    assert leases.revoke_task(OTHER, TASK) == 0
    assert leases.revoke_task(OWNER, TASK) == 1
    assert leases.resolve(str(lease_one["id"]), token_one) is None
    assert leases.resolve(str(lease_two["id"]), token_two) is not None
    assert leases.delete_owner(OTHER) == 0
    assert leases.delete_owner(OWNER) == 2


def test_codex_config_contains_only_local_gateway_url_and_header_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _server = _registry(tmp_path)
    leases = RemoteMcpLeaseStore(registry)
    monkeypatch.setattr(gateway_module, "remote_mcp_registry", lambda: registry)
    monkeypatch.setattr(gateway_module, "remote_mcp_lease_store", lambda: leases)
    monkeypatch.setattr(gateway_module, "fresh_settings", lambda: SimpleNamespace(fdex_port=18080))
    config = build_codex_remote_mcp_config(OWNER, TASK)
    assert len(config) == 1
    item = next(iter(config.values()))
    assert item["url"].startswith("http://127.0.0.1:18080/internal/codex-mcp/lease_")
    assert "example.com" not in item["url"]
    assert "?" not in item["url"]
    headers = item["http_headers"]
    token = headers["X-FDEX-MCP-Capability"]
    assert len(token) >= 32
    assert token not in item["url"]
    assert item["enabled_tools"] == ["search_docs", "read_page"]
    assert item["default_tools_approval_mode"] == "writes"

    with registry.db() as conn:
        row = conn.execute("SELECT token_hash FROM remote_mcp_leases WHERE owner_id=? AND task_id=?", (OWNER, TASK)).fetchone()
    assert row is not None
    assert token not in str(row["token_hash"])


def test_pinned_resolver_returns_only_prevalidated_addresses() -> None:
    resolver = PinnedResolver("example.com", ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"))
    rows = asyncio.run(resolver.resolve("example.com", 443, socket.AF_UNSPEC))
    assert {row["host"] for row in rows} == {"93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"}
    assert all(row["hostname"] == "example.com" for row in rows)
    with pytest.raises(OSError, match="host mismatch"):
        asyncio.run(resolver.resolve("attacker.example", 443, socket.AF_UNSPEC))


def test_gateway_enforces_tool_allowlist_for_single_and_batch_requests() -> None:
    allowed = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_docs", "arguments": {}}}
    ).encode()
    enforce_tool_allowlist(allowed, "application/json", ["search_docs"])
    denied = json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "delete_everything"}}
    ).encode()
    with pytest.raises(PermissionError, match="outside"):
        enforce_tool_allowlist(denied, "application/json", ["search_docs"])
    batch = json.dumps(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_page"}},
        ]
    ).encode()
    enforce_tool_allowlist(batch, "application/json; charset=utf-8", ["read_page"])
    with pytest.raises(ValueError, match="inspectable"):
        enforce_tool_allowlist(b"opaque", "application/octet-stream", ["read_page"])


def test_gateway_is_loopback_only_requires_header_capability_and_never_forwards_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLeaseStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def resolve(self, lease_id: str, token: str):
            self.calls.append((lease_id, token))
            if token != "correct-capability-token-xxxxxxxxxxxxxxxx":
                return None
            return (
                {"id": lease_id, "owner_id": OWNER, "task_id": TASK, "server_id": "mcp_one"},
                {"url": "https://example.com/mcp", "enabled_tools": ["search_docs"], "tool_timeout_sec": 60},
            )

    fake = FakeLeaseStore()
    monkeypatch.setattr(gateway_module, "remote_mcp_lease_store", lambda: fake)

    async def fake_relay(request: Request, *, server: dict[str, Any], body: bytes):
        forwarded = gateway_module._forward_request_headers(request)
        assert "X-FDEX-MCP-Capability" not in forwarded
        assert "Authorization" not in forwarded
        assert "Cookie" not in forwarded
        return JSONResponse({"ok": True})

    monkeypatch.setattr(gateway_module, "_relay", fake_relay)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_docs"}}
    ).encode()
    external = _request(
        client="203.0.113.25",
        headers={"content-type": "application/json", "X-FDEX-MCP-Capability": "correct-capability-token-xxxxxxxxxxxxxxxx"},
        body=body,
    )
    assert asyncio.run(remote_mcp_gateway("lease_test", external)).status_code == 404
    assert fake.calls == []

    missing = _request(client="127.0.0.1", headers={"content-type": "application/json"}, body=body)
    assert asyncio.run(remote_mcp_gateway("lease_test", missing)).status_code == 404

    valid = _request(
        client="127.0.0.1",
        headers={
            "content-type": "application/json",
            "X-FDEX-MCP-Capability": "correct-capability-token-xxxxxxxxxxxxxxxx",
            "Authorization": "Bearer must-not-leave-loopback",
            "Cookie": "must-not-leave-loopback=1",
        },
        body=body,
    )
    assert asyncio.run(remote_mcp_gateway("lease_test", valid)).status_code == 200


def test_gateway_blocks_tool_outside_allowlist_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLeaseStore:
        def resolve(self, _lease_id: str, _token: str):
            return (
                {"owner_id": OWNER, "task_id": TASK, "server_id": "mcp_one"},
                {"url": "https://example.com/mcp", "enabled_tools": ["read_page"], "tool_timeout_sec": 60},
            )

    monkeypatch.setattr(gateway_module, "remote_mcp_lease_store", lambda: FakeLeaseStore())
    called = False

    async def should_not_relay(*_args: Any, **_kwargs: Any):
        nonlocal called
        called = True
        return JSONResponse({"unexpected": True})

    monkeypatch.setattr(gateway_module, "_relay", should_not_relay)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "write_admin"}}
    ).encode()
    request = _request(
        headers={
            "content-type": "application/json",
            "X-FDEX-MCP-Capability": "correct-capability-token-xxxxxxxxxxxxxxxx",
        },
        body=body,
    )
    response = asyncio.run(remote_mcp_gateway("lease_test", request))
    assert response.status_code == 403
    assert called is False


def test_task_context_injects_then_revokes_mcp_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_config = {
        "fdex_docs": {
            "url": "http://127.0.0.1:18080/internal/codex-mcp/lease_test",
            "http_headers": {"X-FDEX-MCP-Capability": "secret"},
            "enabled_tools": ["read_page"],
        }
    }
    revoked: list[tuple[str, str]] = []

    class FakeLeaseStore:
        def revoke_task(self, owner_id: str, task_id: str) -> int:
            revoked.append((owner_id, task_id))
            return 1

    monkeypatch.setattr(install_module, "build_codex_remote_mcp_config", lambda owner_id, task_id: fake_config)
    monkeypatch.setattr(install_module, "remote_mcp_lease_store", lambda: FakeLeaseStore())
    import app.codex_host_runtime as host

    with install_module.codex_remote_mcp_scope(OWNER, TASK):
        config = host._codex_thread_config(tmp_path / "codex-home", allow_network=False)
        assert config["mcp_servers"] == fake_config
    config_after = host._codex_thread_config(tmp_path / "codex-home", allow_network=False)
    assert "mcp_servers" not in config_after
    assert revoked == [(OWNER, TASK)]


def test_runtime_wiring_keeps_remote_endpoint_and_capability_out_of_shell() -> None:
    root = Path(__file__).parents[1] / "app"
    entry = (root / "codex_host_entry.py").read_text(encoding="utf-8")
    engine = (root / "codex_engine.py").read_text(encoding="utf-8")
    gateway = (root / "remote_mcp_gateway.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    assert "codex_remote_mcp_scope(task.owner_id, task.id)" in entry
    assert "X-FDEX-MCP-Capability" not in engine
    assert "remote_mcp" not in engine.lower()
    assert 'allow_redirects=False' in gateway
    assert 'if 300 <= upstream.status < 400' in gateway
    assert 'if upstream.status in {401, 403, 407}' in gateway
    assert 'router.api_route("/{lease_id}"' in gateway
    assert 'app.include_router(remote_mcp_gateway_router)' in main
