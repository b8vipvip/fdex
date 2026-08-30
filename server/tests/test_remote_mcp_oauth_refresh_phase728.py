from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import app.remote_mcp_oauth as oauth_module
from app.remote_mcp_credentials import RemoteMcpCredentialStore
from app.remote_mcp_oauth import RemoteMcpOAuthStore
from app.remote_mcp_registry import RemoteMcpRegistry

OWNER = "usr_phase728_refresh_owner"
SERVER = ""


def _stack(tmp_path: Path):
    registry = RemoteMcpRegistry(tmp_path / "remote-mcp.sqlite3")
    server = registry.save(
        OWNER,
        name="docs",
        url="https://example.com/mcp",
        enabled_tools=["search_docs"],
        enabled=True,
        startup_timeout_sec=10,
        tool_timeout_sec=20,
        resolve_dns=False,
    )
    credentials = RemoteMcpCredentialStore(registry, key_path=tmp_path / "vault.key")
    oauth = RemoteMcpOAuthStore(registry, credentials)
    original = oauth_module.resolve_public_addresses
    oauth_module.resolve_public_addresses = lambda _host: ("93.184.216.34",)
    try:
        oauth.save_config(
            OWNER,
            str(server["id"]),
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            revocation_url="https://auth.example.com/revoke",
            client_id="fdex-client",
            client_auth_method="none",
            scopes="docs.read offline_access",
        )
    finally:
        oauth_module.resolve_public_addresses = original
    return registry, credentials, oauth, server


def test_expired_access_token_refreshes_once_and_keeps_grant_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    metadata = credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="expired_access_token",
        refresh_token="refresh-token-one",
        expires_at="2020-01-01T00:00:00+00:00",
        scope="docs.read offline_access",
    )
    revision = str(metadata["grant_revision"])
    calls: list[dict[str, Any]] = []

    async def fake_post(url: str, data: dict[str, str], *, basic_auth=None):
        calls.append({"url": url, "data": dict(data), "basic_auth": basic_auth})
        return {
            "access_token": "fresh_access_token",
            "refresh_token": "refresh-token-two",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "docs.read offline_access",
        }

    monkeypatch.setattr(oauth_module, "remote_mcp_oauth_store", lambda: oauth)
    monkeypatch.setattr(oauth_module, "remote_mcp_credential_store", lambda: credentials)
    monkeypatch.setattr(oauth_module, "_oauth_post", fake_post)

    token = asyncio.run(oauth_module.oauth_access_token(OWNER, str(server["id"]), revision))
    assert token == "fresh_access_token"
    assert len(calls) == 1
    assert calls[0]["data"]["grant_type"] == "refresh_token"
    assert calls[0]["data"]["refresh_token"] == "refresh-token-one"
    assert credentials.revision(OWNER, str(server["id"])) == revision
    grant = credentials.get_oauth_grant(OWNER, str(server["id"]), expected_revision=revision)
    assert grant is not None
    assert grant["refresh_token"] == "refresh-token-two"


def test_expired_oauth_without_refresh_token_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    metadata = credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="expired_access_token",
        refresh_token="",
        expires_at="2020-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(oauth_module, "remote_mcp_oauth_store", lambda: oauth)
    monkeypatch.setattr(oauth_module, "remote_mcp_credential_store", lambda: credentials)
    with pytest.raises(RuntimeError, match="没有 refresh_token"):
        asyncio.run(
            oauth_module.oauth_access_token(
                OWNER,
                str(server["id"]),
                str(metadata["grant_revision"]),
            )
        )


def test_revocation_transport_failure_preserves_local_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    metadata = credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token",
        refresh_token="refresh-token",
    )
    revision = str(metadata["grant_revision"])

    async def fail_post(*_args, **_kwargs):
        raise RuntimeError("revocation transport failed")

    monkeypatch.setattr(oauth_module, "remote_mcp_oauth_store", lambda: oauth)
    monkeypatch.setattr(oauth_module, "remote_mcp_credential_store", lambda: credentials)
    monkeypatch.setattr(oauth_module, "_oauth_post", fail_post)
    with pytest.raises(RuntimeError, match="revocation transport failed"):
        asyncio.run(oauth_module.revoke_oauth_grant(OWNER, str(server["id"])))
    assert credentials.get_oauth_grant(OWNER, str(server["id"]), expected_revision=revision) is not None


def test_successful_revocation_deletes_local_grant_and_revokes_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.remote_mcp_gateway import RemoteMcpLeaseStore

    registry, credentials, oauth, server = _stack(tmp_path)
    metadata = credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token",
        refresh_token="refresh-token",
    )
    leases = RemoteMcpLeaseStore(registry, credentials)
    lease, capability = leases.issue(OWNER, "task_phase728_revoke", str(server["id"]))
    assert leases.resolve(str(lease["id"]), capability) is not None

    async def ok_post(url: str, data: dict[str, str], *, basic_auth=None):
        assert data["token"] == "refresh-token"
        return {}

    monkeypatch.setattr(oauth_module, "remote_mcp_oauth_store", lambda: oauth)
    monkeypatch.setattr(oauth_module, "remote_mcp_credential_store", lambda: credentials)
    monkeypatch.setattr(oauth_module, "_oauth_post", ok_post)
    assert asyncio.run(oauth_module.revoke_oauth_grant(OWNER, str(server["id"]))) is True
    assert credentials.metadata(OWNER, str(server["id"])) is None
    assert leases.resolve(str(lease["id"]), capability) is None


def test_owner_delete_removes_oauth_config_flow_and_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token",
        refresh_token="refresh-token",
    )
    monkeypatch.setattr(oauth_module, "fresh_settings", lambda: type("S", (), {"public_base_url": "https://fdex.example.com"})())
    oauth.begin_flow(OWNER, str(server["id"]))
    assert oauth.config(OWNER, str(server["id"])) is not None
    assert credentials.metadata(OWNER, str(server["id"])) is not None
    assert credentials.delete_owner(OWNER) == 1
    assert credentials.metadata(OWNER, str(server["id"])) is None
    with registry.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM remote_mcp_oauth_configs WHERE owner_id=?", (OWNER,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM remote_mcp_oauth_flows WHERE owner_id=?", (OWNER,)).fetchone()[0] == 0
