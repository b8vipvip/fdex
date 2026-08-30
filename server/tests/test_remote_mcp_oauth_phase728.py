from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.remote_mcp_gateway as gateway_module
import app.remote_mcp_oauth as oauth_module
from app.remote_mcp_credentials import RemoteMcpCredentialStore
from app.remote_mcp_gateway import RemoteMcpLeaseStore
from app.remote_mcp_oauth import RemoteMcpOAuthStore
from app.remote_mcp_registry import RemoteMcpRegistry

OWNER = "usr_phase728_owner"
OTHER = "usr_phase728_other"
TASK = "task_phase728_aaaaaaaaaaaaaaaa"


def _stack(tmp_path: Path) -> tuple[RemoteMcpRegistry, RemoteMcpCredentialStore, RemoteMcpOAuthStore, dict[str, Any]]:
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
    return registry, credentials, oauth, server


def _save_config(oauth: RemoteMcpOAuthStore, server_id: str) -> dict[str, Any]:
    original = oauth_module.resolve_public_addresses
    oauth_module.resolve_public_addresses = lambda _host: ("93.184.216.34",)
    try:
        return oauth.save_config(
            OWNER,
            server_id,
            authorization_url="https://auth.example.com/authorize?audience=docs",
            token_url="https://auth.example.com/token",
            revocation_url="https://auth.example.com/revoke",
            client_id="fdex-client",
            client_auth_method="client_secret_post",
            client_secret="client-secret-value",
            scopes="docs.read offline_access",
        )
    finally:
        oauth_module.resolve_public_addresses = original


def test_oauth_config_never_returns_client_secret_and_flow_is_owner_scoped_hash_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    _save_config(oauth, str(server["id"]))
    public = oauth.config(OWNER, str(server["id"]))
    assert public is not None
    assert public["has_client_secret"] is True
    assert "client_secret" not in public
    private = oauth.config(OWNER, str(server["id"]), include_secret=True)
    assert private is not None and private["client_secret"] == "client-secret-value"

    monkeypatch.setattr(oauth_module, "fresh_settings", lambda: SimpleNamespace(public_base_url="https://fdex.example.com"))
    flow = oauth.begin_flow(OWNER, str(server["id"]))
    url = str(flow["authorization_url"])
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=https%3A%2F%2Ffdex.example.com%2Faccount%2Fagent%2Fruntime%2Fmcp%2Foauth%2Fcallback" in url
    assert "client-secret-value" not in url

    with registry.db() as conn:
        row = conn.execute("SELECT state_hash,verifier_cipher FROM remote_mcp_oauth_flows").fetchone()
    assert row is not None
    assert "state=" not in str(row["state_hash"])
    assert "client-secret-value" not in str(row["verifier_cipher"])
    # Extract state from generated URL only to simulate the browser callback.
    from urllib.parse import parse_qs, urlsplit

    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(ValueError, match="不属于"):
        oauth.claim_flow(OTHER, state)
    claimed = oauth.claim_flow(OWNER, state)
    assert claimed["owner_id"] == OWNER
    assert len(str(claimed["verifier"])) >= 43
    with pytest.raises(ValueError, match="已使用"):
        oauth.claim_flow(OWNER, state)


def test_oauth_grant_refresh_keeps_lease_revision_but_reauthorization_changes_it(tmp_path: Path) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    first = credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token_one",
        refresh_token="refresh-token-one",
        expires_at="2026-08-30T00:00:00+00:00",
        scope="docs.read",
    )
    revision = credentials.revision(OWNER, str(server["id"]))
    assert revision == first["grant_revision"]
    credentials.refresh_oauth_grant(
        OWNER,
        str(server["id"]),
        expected_revision=revision,
        access_token="access_token_two",
        refresh_token="refresh-token-two",
        expires_at="2027-08-30T00:00:00+00:00",
        scope="docs.read",
    )
    assert credentials.revision(OWNER, str(server["id"])) == revision
    refreshed = credentials.get_oauth_grant(OWNER, str(server["id"]), expected_revision=revision)
    assert refreshed is not None and refreshed["access_token"] == "access_token_two"

    second = credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token_three",
        refresh_token="refresh-token-three",
    )
    assert second["grant_revision"] != revision


def test_lease_binds_oauth_grant_not_access_token_refresh(tmp_path: Path) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token_one",
        refresh_token="refresh-token-one",
    )
    leases = RemoteMcpLeaseStore(registry, credentials)
    lease, capability = leases.issue(OWNER, TASK, str(server["id"]))
    revision = str(lease["credential_updated_at"])
    credentials.refresh_oauth_grant(
        OWNER,
        str(server["id"]),
        expected_revision=revision,
        access_token="access_token_two",
        refresh_token="refresh-token-two",
    )
    assert leases.resolve(str(lease["id"]), capability) is not None
    credentials.set_oauth_grant(
        OWNER,
        str(server["id"]),
        access_token="access_token_new_grant",
        refresh_token="refresh-token-new-grant",
    )
    assert leases.resolve(str(lease["id"]), capability) is None


def test_refresh_lock_is_cross_worker_claim_once(tmp_path: Path) -> None:
    registry, credentials, oauth, server = _stack(tmp_path)
    _save_config(oauth, str(server["id"]))
    first = oauth.claim_refresh(OWNER, str(server["id"]))
    assert first
    assert oauth.claim_refresh(OWNER, str(server["id"])) is None
    oauth.release_refresh(OWNER, str(server["id"]), str(first))
    second = oauth.claim_refresh(OWNER, str(server["id"]))
    assert second and second != first


def test_oauth_endpoint_policy_requires_https_443_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_module, "resolve_public_addresses", lambda host: ("93.184.216.34",))
    normalized, addresses = oauth_module.normalize_oauth_endpoint("https://AUTH.example.com/token", allow_query=False)
    assert normalized == "https://auth.example.com/token"
    assert addresses == ("93.184.216.34",)
    with pytest.raises(ValueError, match="HTTPS"):
        oauth_module.normalize_oauth_endpoint("http://auth.example.com/token", allow_query=False)
    with pytest.raises(ValueError, match="443"):
        oauth_module.normalize_oauth_endpoint("https://auth.example.com:8443/token", allow_query=False)
    with pytest.raises(ValueError, match="query"):
        oauth_module.normalize_oauth_endpoint("https://auth.example.com/token?a=1", allow_query=False)


def test_gateway_oauth_authorization_is_fdex_held_and_toctou_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCredentials:
        def metadata(self, owner_id: str, server_id: str):
            return {"auth_type": "oauth", "lease_revision": "grant-1"}

    calls: list[tuple[str, str, str]] = []

    async def fake_access(owner_id: str, server_id: str, revision: str) -> str:
        calls.append((owner_id, server_id, revision))
        return "oauth-access-token"

    monkeypatch.setattr(gateway_module, "remote_mcp_credential_store", lambda: FakeCredentials())
    monkeypatch.setattr(gateway_module, "oauth_access_token", fake_access)
    lease = {"owner_id": OWNER, "server_id": "mcp_one", "credential_updated_at": "grant-1"}
    assert asyncio.run(gateway_module._authorization_bearer(lease)) == "oauth-access-token"
    assert calls == [(OWNER, "mcp_one", "grant-1")]

    lease["credential_updated_at"] = "stale-grant"
    with pytest.raises(RuntimeError, match="changed"):
        asyncio.run(gateway_module._authorization_bearer(lease))


def test_source_and_export_never_expose_oauth_secrets() -> None:
    root = Path(__file__).parents[1]
    template = (root / "app" / "templates" / "user_agent_settings.html").read_text(encoding="utf-8")
    gateway = (root / "app" / "remote_mcp_gateway.py").read_text(encoding="utf-8")
    export = (root / "app" / "account_data_export.py").read_text(encoding="utf-8")
    engine = (root / "app" / "codex_engine.py").read_text(encoding="utf-8")
    assert 'type="password"' in template
    assert "access_token" not in template
    assert "refresh_token" not in template
    assert "client_secret" not in gateway
    assert "oauth_access_token" in gateway
    assert "remote_mcp_oauth_access_tokens" in export
    assert "remote_mcp_oauth_refresh_tokens" in export
    assert "remote_mcp_oauth" not in engine.lower()
