from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app import github_egress_admin_routes as admin
from app import github_egress_probe as probe
from app import github_vless_pool as pool

UUID1 = "11111111-2222-3333-4444-555555555555"
UUID2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _uri(uuid_value: str, host: str, note: str = "") -> str:
    suffix = f"#{note}" if note else ""
    return (
        f"vless://{uuid_value}@{host}:443?encryption=none&security=tls&sni={host}"
        f"&type=ws&path=%2Ffdex{suffix}"
    )


def test_proxy_pool_supports_multiple_nodes_and_single_active_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pool, "_POOL_FILE", tmp_path / "vless-nodes.json")
    first = pool.add_vless_node("US West", _uri(UUID1, "one.example"))
    second = pool.add_vless_node("US East", _uri(UUID2, "two.example"))

    listed = pool.list_vless_nodes()
    assert len(listed) == 2
    assert all("uri" not in item for item in listed)
    assert not any(item["enabled"] for item in listed)

    pool.mark_active_vless_node(first["id"])
    assert pool.active_vless_node()["id"] == first["id"]
    pool.mark_active_vless_node(second["id"])
    listed = pool.list_vless_nodes()
    assert [item["id"] for item in listed if item["enabled"]] == [second["id"]]

    edited = pool.edit_vless_node(second["id"], "US East 02", _uri(UUID2, "three.example"))
    assert edited["name"] == "US East 02"
    assert "three.example:443" in edited["summary"]
    assert "uri" not in edited

    pool.disable_vless_node(second["id"])
    assert pool.active_vless_node() is None
    removed = pool.delete_vless_node(first["id"])
    assert removed["id"] == first["id"]
    assert len(pool.list_vless_nodes()) == 1
    assert oct((tmp_path / "vless-nodes.json").stat().st_mode & 0o777) == "0o600"


def test_duplicate_vless_link_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pool, "_POOL_FILE", tmp_path / "vless-nodes.json")
    link = _uri(UUID1, "one.example")
    pool.add_vless_node("one", link)
    with pytest.raises(ValueError, match="已经存在"):
        pool.add_vless_node("duplicate", link)


def test_phase716_single_node_is_migrated_without_rendering_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pool, "_POOL_FILE", tmp_path / "vless-nodes.json")
    link = _uri(UUID1, "legacy.example", "legacy")
    pool.ensure_legacy_vless_node(
        {
            "FDEX_GITHUB_EGRESS_MODE": "managed_vless",
            "FDEX_GITHUB_VLESS_URI": link,
        }
    )
    nodes = pool.list_vless_nodes()
    assert len(nodes) == 1
    assert nodes[0]["enabled"] is True
    assert "legacy.example:443" in nodes[0]["summary"]
    assert link not in repr(nodes)
    assert UUID1 not in repr(nodes)


def test_strict_probe_rejects_api_rate_limit_and_uses_server_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_headers: list[dict[str, str]] = []
    responses = [
        httpx.Response(200, text="ok", request=httpx.Request("GET", "https://github.com/")),
        httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1234567890"},
            request=httpx.Request("GET", "https://api.github.com/meta"),
        ),
    ]

    class DummyClient:
        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
            seen_headers.append(dict(headers))
            return responses.pop(0)

    class DummyGitHub:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                fdex_github_http_proxy="",
                fdex_github_connect_timeout_seconds=10.0,
                fdex_github_read_timeout_seconds=60.0,
                github_token="maintenance-token",
            )

        def _client(self, **_kwargs: Any) -> DummyClient:
            return DummyClient()

    monkeypatch.setattr(probe, "GitHubAppClient", DummyGitHub)
    result = probe.probe_github_egress_network()
    assert result["targets"][0]["ok"] is True
    assert result["targets"][1]["ok"] is False
    assert result["targets"][1]["reachable"] is True
    assert "rate limit exhausted" in result["targets"][1]["error"]
    assert seen_headers[1]["Authorization"] == "Bearer maintenance-token"


def test_proxy_list_ui_exposes_crud_without_echoing_vless_secret() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/github_egress.html").read_text(encoding="utf-8")
    base = (root / "server/app/templates/base.html").read_text(encoding="utf-8")
    assert "VLESS 代理列表" in template
    assert "/admin/github-egress/nodes/add" in template
    assert "/enable" in template
    assert "/disable" in template
    assert "/edit" in template
    assert "/delete" in template
    assert 'type="password" name="vless_uri"' in template
    assert 'value="{{ node.uri' not in template
    assert "FDEX_GITHUB_XRAY_PROXY_PASSWORD" not in template
    assert "托管 VLESS 当前无法启用" in template
    assert "无法启用：缺少 Xray" in template
    assert "position:sticky;top:12px;z-index:1000" in base


def test_admin_router_exposes_proxy_pool_crud() -> None:
    paths = {route.path for route in admin.router.routes}
    assert "/github-egress/nodes/add" in paths
    assert "/github-egress/nodes/{node_id}/edit" in paths
    assert "/github-egress/nodes/{node_id}/enable" in paths
    assert "/github-egress/nodes/{node_id}/disable" in paths
    assert "/github-egress/nodes/{node_id}/delete" in paths
