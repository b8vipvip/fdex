from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

import app.remote_mcp_registry as registry_module
from app.codex_engine import _codex_thread_config
from app.remote_mcp_registry import RemoteMcpRegistry, normalize_remote_mcp_url, normalize_tools


OWNER = "usr_phase725_owner"
OTHER = "usr_phase725_other"


def _public_dns(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
    ]


def _private_dns(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


def _mixed_dns(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443)),
    ]


def test_remote_mcp_url_requires_credential_free_https_443_and_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _public_dns)
    url, addresses = normalize_remote_mcp_url("https://Example.COM/mcp")
    assert url == "https://example.com/mcp"
    assert set(addresses) == {"93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"}

    for invalid in (
        "http://example.com/mcp",
        "https://user:pass@example.com/mcp",
        "https://example.com:8443/mcp",
        "https://example.com/mcp?token=secret",
        "https://example.com/mcp#fragment",
        "https://127.0.0.1/mcp",
        "https://10.2.3.4/mcp",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/mcp",
    ):
        with pytest.raises(ValueError):
            normalize_remote_mcp_url(invalid)

    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _private_dns)
    with pytest.raises(ValueError, match="非公网"):
        normalize_remote_mcp_url("https://internal.example/mcp")

    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _mixed_dns)
    with pytest.raises(ValueError, match="同时返回"):
        normalize_remote_mcp_url("https://rebind.example/mcp")


def test_public_ipv6_literal_is_canonicalized_with_brackets() -> None:
    url, addresses = normalize_remote_mcp_url(
        "https://[2606:4700:4700::1111]/mcp",
        resolve_dns=True,
    )
    assert url == "https://[2606:4700:4700::1111]/mcp"
    assert addresses == ("2606:4700:4700::1111",)


def test_tool_allowlist_is_explicit_bounded_and_deduplicated() -> None:
    assert normalize_tools("search_docs\nread_page,search_docs") == ("search_docs", "read_page")
    with pytest.raises(ValueError, match="工具名无效"):
        normalize_tools("safe\n../../shell")
    with pytest.raises(ValueError, match="最多"):
        normalize_tools([f"tool_{index}" for index in range(65)])


def test_registry_is_owner_scoped_and_contains_no_credential_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _public_dns)
    store = RemoteMcpRegistry(tmp_path / "remote-mcp.sqlite3")
    server = store.save(
        OWNER,
        name="docs",
        url="https://example.com/mcp",
        enabled_tools="search_docs\nread_page",
        enabled=True,
        startup_timeout_sec=20,
        tool_timeout_sec=90,
    )
    assert server["owner_id"] == OWNER
    assert server["enabled"] is True
    assert server["enabled_tools"] == ["search_docs", "read_page"]
    assert store.get(OTHER, server["id"]) is None
    assert store.list(OTHER) == []
    assert store.delete(OTHER, server["id"]) is False

    with store.db() as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(remote_mcp_servers)").fetchall()}
    forbidden = {
        "bearer_token",
        "bearer_token_cipher",
        "oauth_token",
        "oauth_credentials",
        "http_headers",
        "command",
        "args",
        "env",
        "cwd",
    }
    assert columns.isdisjoint(forbidden)

    exported = store.export_owner(OWNER)
    assert exported == [
        {
            "id": server["id"],
            "name": "docs",
            "url": "https://example.com/mcp",
            "enabled": True,
            "enabled_tools": ["search_docs", "read_page"],
            "startup_timeout_sec": 20,
            "tool_timeout_sec": 90,
            "created_at": server["created_at"],
            "updated_at": server["updated_at"],
        }
    ]
    assert "resolved_addresses" not in exported[0]
    assert store.delete_owner(OTHER) == 0
    assert store.delete_owner(OWNER) == 1
    assert store.list(OWNER) == []


def test_registry_requires_allowlist_before_enabled_and_revalidates_dns_on_enable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _public_dns)
    store = RemoteMcpRegistry(tmp_path / "remote-mcp.sqlite3")
    with pytest.raises(ValueError, match="allowlist"):
        store.save(
            OWNER,
            name="empty",
            url="https://example.com/mcp",
            enabled_tools="",
            enabled=True,
        )
    disabled = store.save(
        OWNER,
        name="empty",
        url="https://example.com/mcp",
        enabled_tools="",
        enabled=False,
    )
    with pytest.raises(ValueError, match="allowlist"):
        store.set_enabled(OWNER, disabled["id"], True)

    updated = store.save(
        OWNER,
        server_id=disabled["id"],
        name="empty",
        url="https://example.com/mcp",
        enabled_tools="read_page",
        enabled=True,
    )
    assert updated["enabled"] is True

    # A poisoned DNS answer must never prevent the owner from disabling the registry entry.
    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _private_dns)
    stopped = store.set_enabled(OWNER, disabled["id"], False)
    assert stopped["enabled"] is False
    with pytest.raises(ValueError, match="非公网"):
        store.set_enabled(OWNER, disabled["id"], True)


def test_cross_owner_cannot_overwrite_existing_registry_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module.socket, "getaddrinfo", _public_dns)
    store = RemoteMcpRegistry(tmp_path / "remote-mcp.sqlite3")
    first = store.save(
        OWNER,
        name="docs",
        url="https://example.com/mcp",
        enabled_tools="read_page",
    )
    with pytest.raises(ValueError, match="不存在或不属于"):
        store.save(
            OTHER,
            server_id=first["id"],
            name="stolen",
            url="https://example.net/mcp",
            enabled_tools="read_page",
        )
    assert store.get(OWNER, first["id"])["name"] == "docs"  # type: ignore[index]


def test_phase725_registry_base_config_and_lifecycle_invariants_survive_runtime_activation(tmp_path: Path) -> None:
    # Phase 7.26 activates MCP only through the task-scoped Host seam. The underlying Codex engine
    # config helper must remain MCP-free so no user URL/capability can leak into unrelated callers.
    config = _codex_thread_config(tmp_path / "codex-home", allow_network=True)
    assert "mcp_servers" not in config
    assert "mcpServers" not in config
    assert config["sandbox_workspace_write"] == {"network_access": True}

    route_source = Path(__file__).parents[1] / "app" / "agent_policy_portal_routes.py"
    template_source = Path(__file__).parents[1] / "app" / "templates" / "user_agent_settings.html"
    cleanup_source = Path(__file__).parents[1] / "app" / "account_cleanup.py"
    export_source = Path(__file__).parents[1] / "app" / "account_data_export.py"
    route = route_source.read_text(encoding="utf-8")
    assert 'remote_mcp_registry().save(' in route
    assert 'remote_mcp_registry().set_enabled(str(user["id"]), server_id, False)' in route
    template = template_source.read_text(encoding="utf-8")
    # UI wording can advance in later phases; the durable 7.25 invariants are the owner registry,
    # explicit emergency disable and credential-free lifecycle/export contract.
    assert "Remote MCP" in template
    assert "立即停用（不依赖 DNS）" in template
    assert "remote_mcp_registry().delete_owner(clean)" in cleanup_source.read_text(encoding="utf-8")
    assert '"remote_mcp_servers": remote_mcp_registry().export_owner(user_id)' in export_source.read_text(encoding="utf-8")
