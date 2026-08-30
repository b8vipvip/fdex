from __future__ import annotations

import asyncio
import hashlib
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import Request
from fastapi.responses import JSONResponse

import app.remote_mcp_gateway as gateway_module
from app.remote_mcp_credentials import RemoteMcpCredentialStore
from app.remote_mcp_gateway import RemoteMcpLeaseStore, remote_mcp_gateway
from app.remote_mcp_registry import RemoteMcpRegistry

OWNER = "usr_phase727_owner"
OTHER = "usr_phase727_other"
TASK = "task_phase727_aaaaaaaaaaaaaaaa"
TOKEN_ONE = "phase727.token-one_ABC+/=="
TOKEN_TWO = "phase727.token-two_XYZ+/=="


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


def _credentials(tmp_path: Path, registry: RemoteMcpRegistry) -> RemoteMcpCredentialStore:
    return RemoteMcpCredentialStore(
        registry,
        key_path=tmp_path / "remote-mcp-secrets" / "credential-vault.key",
    )


def _request(headers: dict[str, str], body: bytes) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    raw_headers.append((b"content-length", str(len(body)).encode()))
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/internal/codex-mcp/lease_test",
            "raw_path": b"/internal/codex-mcp/lease_test",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 45678),
            "server": ("127.0.0.1", 18080),
        },
        receive,
    )


def test_vault_encrypts_secret_uses_keyed_fingerprint_and_owner_scope(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    store = _credentials(tmp_path, registry)
    metadata = store.set_bearer(OWNER, str(server["id"]), TOKEN_ONE)

    assert metadata["auth_type"] == "bearer"
    assert TOKEN_ONE not in str(metadata)
    assert metadata["fingerprint"] != hashlib.sha256(TOKEN_ONE.encode("ascii")).hexdigest()[:16]
    assert store.get_bearer(
        OWNER,
        str(server["id"]),
        expected_revision=str(metadata["updated_at"]),
    ) == TOKEN_ONE
    assert store.metadata(OTHER, str(server["id"])) is None
    with pytest.raises(KeyError):
        store.set_bearer(OTHER, str(server["id"]), TOKEN_TWO)

    with registry.db() as conn:
        row = conn.execute(
            "SELECT secret_cipher,fingerprint FROM remote_mcp_credentials WHERE owner_id=? AND server_id=?",
            (OWNER, server["id"]),
        ).fetchone()
    assert row is not None
    assert TOKEN_ONE not in str(row["secret_cipher"])
    assert str(row["fingerprint"]) == metadata["fingerprint"]

    key_path = tmp_path / "remote-mcp-secrets" / "credential-vault.key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700
    assert "secret_cipher" not in store.list_metadata(OWNER)[str(server["id"])]


def test_vault_rejects_invalid_tokens_and_missing_or_wrong_key(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    store = _credentials(tmp_path, registry)
    for invalid in ("", "abc\ndef", "含中文", "x" * 8193, "Bearer token with spaces"):
        with pytest.raises(ValueError):
            store.set_bearer(OWNER, str(server["id"]), invalid)

    store.set_bearer(OWNER, str(server["id"]), TOKEN_ONE)
    key_path = tmp_path / "remote-mcp-secrets" / "credential-vault.key"
    key_path.unlink()
    with pytest.raises(RuntimeError, match="key is missing"):
        RemoteMcpCredentialStore(RemoteMcpRegistry(registry.path), key_path=key_path).init()

    key_path.write_bytes(Fernet.generate_key() + b"\n")
    with pytest.raises(RuntimeError, match="does not match"):
        RemoteMcpCredentialStore(RemoteMcpRegistry(registry.path), key_path=key_path).init()


def test_add_rotate_delete_credentials_revoke_existing_leases(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    credentials = _credentials(tmp_path, registry)
    leases = RemoteMcpLeaseStore(registry, credentials)

    anonymous, anonymous_token = leases.issue(OWNER, TASK, str(server["id"]))
    assert anonymous["credential_updated_at"] == ""
    assert leases.resolve(str(anonymous["id"]), anonymous_token) is not None

    first = credentials.set_bearer(OWNER, str(server["id"]), TOKEN_ONE)
    assert leases.resolve(str(anonymous["id"]), anonymous_token) is None
    first_lease, first_token = leases.issue(OWNER, TASK, str(server["id"]))
    assert first_lease["credential_updated_at"] == first["updated_at"]

    second = credentials.set_bearer(OWNER, str(server["id"]), TOKEN_TWO)
    assert second["updated_at"] != first["updated_at"]
    assert leases.resolve(str(first_lease["id"]), first_token) is None
    second_lease, second_token = leases.issue(OWNER, TASK, str(server["id"]))

    assert credentials.delete(OWNER, str(server["id"])) is True
    assert leases.resolve(str(second_lease["id"]), second_token) is None
    fresh, fresh_token = leases.issue(OWNER, TASK, str(server["id"]))
    assert fresh["credential_updated_at"] == ""
    assert leases.resolve(str(fresh["id"]), fresh_token) is not None


def test_credential_toctou_changes_fail_closed(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    store = _credentials(tmp_path, registry)
    metadata = store.set_bearer(OWNER, str(server["id"]), TOKEN_ONE)
    revision = str(metadata["updated_at"])

    assert store.delete(OWNER, str(server["id"])) is True
    with pytest.raises(RuntimeError, match="disappeared"):
        store.get_bearer(OWNER, str(server["id"]), expected_revision=revision)

    store.set_bearer(OWNER, str(server["id"]), TOKEN_TWO)
    with pytest.raises(RuntimeError, match="revision changed"):
        store.get_bearer(OWNER, str(server["id"]), expected_revision="")


def test_gateway_ignores_codex_authorization_and_injects_only_vault_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeLeaseStore:
        def resolve(self, lease_id: str, token: str):
            assert lease_id == "lease_test"
            assert token == "correct-capability-token-xxxxxxxxxxxxxxxx"
            return (
                {
                    "owner_id": OWNER,
                    "task_id": TASK,
                    "server_id": "mcp_one",
                    "credential_updated_at": "credential-rev-1",
                },
                {"url": "https://example.com/mcp", "enabled_tools": ["search_docs"], "tool_timeout_sec": 60},
            )

    class FakeCredentialStore:
        def get_bearer(self, owner_id: str, server_id: str, *, expected_revision: str | None = None):
            assert (owner_id, server_id, expected_revision) == (OWNER, "mcp_one", "credential-rev-1")
            return TOKEN_ONE

    async def fake_relay(request: Request, *, server: dict[str, Any], body: bytes, bearer_token: str | None = None):
        captured["headers"] = gateway_module._forward_request_headers(request, bearer_token=bearer_token)
        return JSONResponse({"ok": True})

    monkeypatch.setattr(gateway_module, "remote_mcp_lease_store", lambda: FakeLeaseStore())
    monkeypatch.setattr(gateway_module, "remote_mcp_credential_store", lambda: FakeCredentialStore())
    monkeypatch.setattr(gateway_module, "_relay", fake_relay)

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_docs"}}
    ).encode()
    response = asyncio.run(
        remote_mcp_gateway(
            "lease_test",
            _request(
                {
                    "content-type": "application/json",
                    "X-FDEX-MCP-Capability": "correct-capability-token-xxxxxxxxxxxxxxxx",
                    "Authorization": "Bearer attacker-controlled-value",
                    "Cookie": "attacker-cookie=1",
                },
                body,
            ),
        )
    )
    assert response.status_code == 200
    forwarded = captured["headers"]
    assert forwarded["Authorization"] == f"Bearer {TOKEN_ONE}"
    assert "attacker-controlled-value" not in str(forwarded)
    assert "Cookie" not in forwarded
    assert "X-FDEX-MCP-Capability" not in forwarded


def test_atomic_server_delete_removes_secret_and_revokes_lease(tmp_path: Path) -> None:
    registry, server = _registry(tmp_path)
    credentials = _credentials(tmp_path, registry)
    credentials.set_bearer(OWNER, str(server["id"]), TOKEN_ONE)
    leases = RemoteMcpLeaseStore(registry, credentials)
    lease, token = leases.issue(OWNER, TASK, str(server["id"]))

    assert credentials.delete_server(OTHER, str(server["id"])) is False
    assert registry.get(OWNER, str(server["id"])) is not None
    assert credentials.delete_server(OWNER, str(server["id"])) is True
    assert registry.get(OWNER, str(server["id"])) is None
    assert leases.resolve(str(lease["id"]), token) is None

    with registry.db() as conn:
        secret_count = int(conn.execute("SELECT COUNT(*) FROM remote_mcp_credentials").fetchone()[0])
        state = conn.execute("SELECT state FROM remote_mcp_leases WHERE id=?", (lease["id"],)).fetchone()
    assert secret_count == 0
    assert state is not None and str(state["state"]) == "revoked"


def test_multi_worker_schema_upgrade_serializes_credential_column(tmp_path: Path) -> None:
    db_path = tmp_path / "remote-mcp.sqlite3"
    key_path = tmp_path / "remote-mcp-secrets" / "credential-vault.key"
    bootstrap = RemoteMcpRegistry(db_path)
    bootstrap.init()
    with bootstrap.db() as conn:
        conn.execute(
            """
            CREATE TABLE remote_mcp_leases (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, task_id TEXT NOT NULL,
                server_id TEXT NOT NULL, server_updated_at TEXT NOT NULL DEFAULT '',
                token_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT '', revoked_at TEXT NOT NULL DEFAULT ''
            )
            """
        )

    def worker() -> None:
        registry = RemoteMcpRegistry(db_path)
        credentials = RemoteMcpCredentialStore(registry, key_path=key_path)
        RemoteMcpLeaseStore(registry, credentials).init()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker) for _ in range(4)]
        for future in futures:
            future.result(timeout=15)

    with bootstrap.db() as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(remote_mcp_leases)").fetchall()}
    assert "credential_updated_at" in columns


def test_lifecycle_export_ui_and_codex_config_do_not_expose_remote_secret() -> None:
    root = Path(__file__).parents[1] / "app"
    cleanup = (root / "account_cleanup.py").read_text(encoding="utf-8")
    export = (root / "account_data_export.py").read_text(encoding="utf-8")
    routes = (root / "agent_policy_portal_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent_settings.html").read_text(encoding="utf-8")
    gateway = (root / "remote_mcp_gateway.py").read_text(encoding="utf-8")

    assert cleanup.index("remote_mcp_lease_store().delete_owner(clean)") < cleanup.index(
        "remote_mcp_credential_store().delete_owner(clean)"
    ) < cleanup.index("remote_mcp_registry().delete_owner(clean)")
    assert '"remote_mcp_bearer_tokens"' in export
    assert '"remote_mcp_servers": remote_mcp_registry().export_owner(user_id)' in export
    assert "secret_cipher" not in export
    assert 'type="password" name="bearer_token"' in template
    assert "credential.fingerprint" in template
    assert "secret_cipher" not in template
    assert "http_headers" not in routes
    assert "delete_server(owner_id, server_id)" in routes

    config_section = gateway.split("def build_codex_remote_mcp_config", 1)[1].split("class PinnedResolver", 1)[0]
    assert "Authorization" not in config_section
    assert "bearer_token" not in config_section
    assert "remote_mcp_credential_store().get_bearer" in gateway
