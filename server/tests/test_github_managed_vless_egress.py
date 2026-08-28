from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app import github_egress_admin_routes as egress_admin
from app.github_egress import (
    build_xray_config,
    managed_proxy_url,
    parse_vless_uri,
)

UUID = "11111111-2222-3333-4444-555555555555"


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("fdex.test", 443),
            "session": {"admin_user": "testadmin", "csrf_token": "x" * 40},
        }
    )


def test_parse_vless_ws_tls_builds_xray_outbound() -> None:
    outbound = parse_vless_uri(
        f"vless://{UUID}@node.example:443?encryption=none&security=tls&sni=cdn.example"
        "&fp=chrome&type=ws&host=edge.example&path=%2Fgithub#fdex"
    )
    assert outbound["protocol"] == "vless"
    assert outbound["settings"]["vnext"][0]["address"] == "node.example"
    assert outbound["settings"]["vnext"][0]["users"][0]["id"] == UUID
    stream = outbound["streamSettings"]
    assert stream["network"] == "ws"
    assert stream["security"] == "tls"
    assert stream["tlsSettings"]["serverName"] == "cdn.example"
    assert stream["wsSettings"]["path"] == "/github"
    assert stream["wsSettings"]["headers"]["Host"] == "edge.example"


def test_parse_vless_reality_and_rejects_non_vless() -> None:
    outbound = parse_vless_uri(
        f"vless://{UUID}@203.0.113.10:443?encryption=none&security=reality&sni=www.example.com"
        "&fp=chrome&pbk=public-key-test&sid=abcd&type=tcp&flow=xtls-rprx-vision"
    )
    reality = outbound["streamSettings"]["realitySettings"]
    assert reality["publicKey"] == "public-key-test"
    assert reality["shortId"] == "abcd"
    assert outbound["settings"]["vnext"][0]["users"][0]["flow"] == "xtls-rprx-vision"
    with pytest.raises(ValueError, match="vless"):
        parse_vless_uri("https://example.com")


def test_managed_xray_is_loopback_authenticated_and_github_only() -> None:
    config = build_xray_config(
        f"vless://{UUID}@node.example:443?encryption=none&security=tls&type=grpc&serviceName=fdex",
        18188,
        "fdex-user",
        "secret-password",
    )
    inbound = config["inbounds"][0]
    assert inbound["listen"] == "127.0.0.1"
    assert inbound["protocol"] == "http"
    assert inbound["settings"]["accounts"] == [{"user": "fdex-user", "pass": "secret-password"}]
    assert [item["protocol"] for item in config["outbounds"]] == ["vless", "blackhole"]
    allow_rule, fallback_rule = config["routing"]["rules"]
    assert "domain:github.com" in allow_rule["domain"]
    assert "domain:githubusercontent.com" in allow_rule["domain"]
    assert fallback_rule["outboundTag"] == "blocked"
    assert all(item["protocol"] != "freedom" for item in config["outbounds"])


def test_managed_proxy_url_is_loopback_and_credentialed() -> None:
    proxy = managed_proxy_url(18188, "fdex user", "p@ss:/?#")
    assert proxy.startswith("http://fdex%20user:")
    assert proxy.endswith("@127.0.0.1:18188")
    assert "p@ss" not in proxy


def test_admin_page_never_renders_vless_or_generated_proxy_secret() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/github_egress.html").read_text(encoding="utf-8")
    base = (root / "server/app/templates/base.html").read_text(encoding="utf-8")
    assert 'type="password" name="vless_uri"' in template
    assert 'value="{{ settings.fdex_github_vless' not in template
    assert "FDEX_GITHUB_XRAY_PROXY_PASSWORD" not in template
    assert "FDEX_GITHUB_HTTP_PROXY" not in template
    assert "/admin/github-egress" in base
    assert "GitHub/VLESS 出站" in base


def test_admin_routes_expose_managed_vless_control_plane() -> None:
    methods = {(route.path, tuple(sorted(route.methods or []))) for route in egress_admin.router.routes}
    assert ("/github-egress", ("GET",)) in methods
    assert ("/github-egress/save", ("POST",)) in methods
    assert ("/github-egress/test", ("POST",)) in methods
    assert ("/github-egress/restart", ("POST",)) in methods


def test_admin_save_never_audits_vless_or_local_proxy_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request("/admin/github-egress/save")
    secret_link = (
        f"vless://{UUID}@node.example:443?encryption=none&security=tls&sni=node.example"
        "&type=ws&path=%2Fprivate"
    )
    values = {
        "FDEX_GITHUB_EGRESS_MODE": "direct",
        "FDEX_GITHUB_VLESS_URI": "",
        "FDEX_GITHUB_XRAY_PROXY_USER": "",
        "FDEX_GITHUB_XRAY_PROXY_PASSWORD": "",
    }
    cfg = SimpleNamespace(
        fdex_port=18080,
        fdex_github_http_proxy="",
        fdex_github_connect_timeout_seconds=10.0,
        fdex_github_read_timeout_seconds=60.0,
        fdex_github_retry_attempts=3,
    )
    written: dict[str, str] = {}
    audits: list[tuple[str, bool, dict[str, object]]] = []

    monkeypatch.setattr(egress_admin, "is_admin", lambda _request: True)
    monkeypatch.setattr(egress_admin, "verify_csrf", lambda _request, _token: None)
    monkeypatch.setattr(egress_admin, "read_env", lambda: dict(values))
    monkeypatch.setattr(egress_admin, "fresh_settings", lambda: cfg)
    monkeypatch.setattr(egress_admin, "resolve_xray_binary", lambda _value: "/usr/bin/xray")
    monkeypatch.setattr(
        egress_admin,
        "make_managed_credentials",
        lambda _values: ("fdex-private-user", "local-proxy-super-secret"),
    )
    monkeypatch.setattr(egress_admin, "write_env", lambda updates: written.update(updates) or None)
    monkeypatch.setattr(egress_admin.get_settings, "cache_clear", lambda: None)
    monkeypatch.setattr(egress_admin, "apply_managed_egress", lambda **_kwargs: {})
    monkeypatch.setattr(egress_admin, "set_flash", lambda *_args, **_kwargs: None)

    def capture_audit(_request: Request, action: str, success: bool = True, **details: object) -> None:
        audits.append((action, success, details))

    monkeypatch.setattr(egress_admin, "write_audit", capture_audit)

    response = egress_admin.save_github_egress(
        request,
        csrf_token="csrf",
        mode="managed_vless",
        vless_uri=secret_link,
        xray_binary="xray",
        xray_local_port="18188",
        http_proxy="",
        connect_timeout_seconds="15",
        read_timeout_seconds="90",
        retry_attempts="3",
        clear_saved_vless=None,
    )

    assert response.status_code == 303
    assert written["FDEX_GITHUB_VLESS_URI"] == secret_link
    assert written["FDEX_GITHUB_HTTP_PROXY"].startswith("http://fdex-private-user:")
    assert written["FDEX_GITHUB_HTTP_PROXY"].endswith("@127.0.0.1:18188")
    assert audits and audits[-1][0] == "save_github_egress"
    audit_repr = repr(audits)
    assert UUID not in audit_repr
    assert "local-proxy-super-secret" not in audit_repr
    assert secret_link not in audit_repr


def test_maintenance_github_status_uses_fdex_proxy_and_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import httpx
    from app import github_service

    captured: dict[str, object] = {}

    class DummyAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> "DummyAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = dict(headers)
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    monkeypatch.setattr(github_service.httpx, "AsyncClient", DummyAsyncClient)
    cfg = SimpleNamespace(
        fdex_github_http_proxy="http://fdex:secret@127.0.0.1:18188",
        fdex_github_connect_timeout_seconds=15.0,
        fdex_github_read_timeout_seconds=90.0,
        github_token="admin-maintenance-token",
    )
    result = asyncio.run(github_service._get_json("https://api.github.com/meta", cfg))
    assert result == {"ok": True}
    assert captured["proxy"] == cfg.fdex_github_http_proxy
    assert captured["trust_env"] is False
    assert captured["headers"]["Authorization"] == "Bearer admin-maintenance-token"
