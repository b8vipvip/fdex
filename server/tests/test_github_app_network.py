from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from app import github_app as github_app_module
from app import github_app_admin_routes as github_admin
from app.config import Settings
from app.github_app import GitHubAppClient, GitHubAppError


def _configure_app(monkeypatch: pytest.MonkeyPatch, *, proxy: str = "") -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://fdex.example")
    monkeypatch.setenv("FDEX_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("FDEX_GITHUB_APP_SLUG", "fdex-test")
    monkeypatch.setenv("FDEX_GITHUB_APP_CLIENT_ID", "Iv23.fdex-app-test")
    monkeypatch.setenv("FDEX_GITHUB_APP_CLIENT_SECRET", "github-app-client-secret")
    monkeypatch.setenv("FDEX_GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/fdex-does-not-read-key-in-network-tests.pem")
    monkeypatch.setenv("FDEX_GITHUB_HTTP_PROXY", proxy)
    monkeypatch.setenv("FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("FDEX_GITHUB_READ_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("FDEX_GITHUB_RETRY_ATTEMPTS", "3")


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


class _SequenceHttpClient:
    def __init__(self, actions: list[object], calls: list[str]) -> None:
        self.actions = actions
        self.calls = calls

    def __enter__(self) -> "_SequenceHttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append(f"{method} {url}")
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        assert isinstance(action, httpx.Response)
        return action


def test_github_transport_defaults_are_longer_than_old_fixed_20_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FDEX_GITHUB_HTTP_PROXY",
        "FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS",
        "FDEX_GITHUB_READ_TIMEOUT_SECONDS",
        "FDEX_GITHUB_RETRY_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.fdex_github_http_proxy == ""
    assert settings.fdex_github_connect_timeout_seconds == 10
    assert settings.fdex_github_read_timeout_seconds == 60
    assert settings.fdex_github_retry_attempts == 3


def test_retry_safe_github_read_retries_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    client = GitHubAppClient()
    calls: list[str] = []
    actions: list[object] = [
        httpx.ReadTimeout("slow read"),
        httpx.Response(200, json={"ok": True}),
    ]
    monkeypatch.setattr(client, "_client", lambda **kwargs: _SequenceHttpClient(actions, calls))
    monkeypatch.setattr(github_app_module.time, "sleep", lambda _seconds: None)

    result = client._request(
        "GET",
        "https://api.github.com/meta",
        retry_safe=True,
        operation="GitHub 测试读取",
    )

    assert result == {"ok": True}
    assert len(calls) == 2


def test_single_use_oauth_code_is_not_replayed_after_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    client = GitHubAppClient()
    calls: list[str] = []
    actions: list[object] = [
        httpx.ReadTimeout("slow oauth response"),
        httpx.Response(200, json={"access_token": "must-not-be-used"}),
    ]
    monkeypatch.setattr(client, "_client", lambda **kwargs: _SequenceHttpClient(actions, calls))

    with pytest.raises(GitHubAppError) as raised:
        client._request(
            "POST",
            "https://github.com/login/oauth/access_token",
            retry_safe=False,
            operation="GitHub OAuth 临时凭据交换",
        )

    message = str(raised.value)
    assert "GitHub OAuth 临时凭据交换失败" in message
    assert "读取超时 60 秒" in message
    assert "服务器直连" in message
    assert len(calls) == 1


def test_explicit_proxy_and_timeouts_are_passed_to_httpx_without_rendering_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "http://proxy-user:proxy-password@127.0.0.1:7890"
    _configure_app(monkeypatch, proxy=proxy)
    captured: dict[str, Any] = {}

    class DummyClient:
        pass

    def fake_client(**kwargs: object) -> DummyClient:
        captured.update(kwargs)
        return DummyClient()

    monkeypatch.setattr(github_app_module.httpx, "Client", fake_client)
    GitHubAppClient()._client(follow_redirects=True)

    assert captured["proxy"] == proxy
    assert captured["follow_redirects"] is True
    assert captured["trust_env"] is True
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10
    assert timeout.read == 60

    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/github_app_settings.html").read_text(encoding="utf-8")
    assert 'type="password" name="http_proxy"' in template
    assert 'value="{{ settings.fdex_github_http_proxy' not in template
    assert "FDEX 不使用第三方 GitHub 镜像" in template


def test_admin_network_save_never_audits_proxy_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request("/admin/github-app/network")
    secret_proxy = "http://alice:super-secret@proxy.example:8080"
    current = SimpleNamespace(
        fdex_github_http_proxy="",
        fdex_github_connect_timeout_seconds=10.0,
        fdex_github_read_timeout_seconds=60.0,
        fdex_github_retry_attempts=3,
    )
    written: dict[str, str] = {}
    audits: list[tuple[str, bool, dict[str, object]]] = []

    monkeypatch.setattr(github_admin, "is_admin", lambda _request: True)
    monkeypatch.setattr(github_admin, "verify_csrf", lambda _request, _token: None)
    monkeypatch.setattr(github_admin, "fresh_settings", lambda: current)
    monkeypatch.setattr(github_admin, "write_env", lambda updates: written.update(updates) or None)
    monkeypatch.setattr(github_admin.get_settings, "cache_clear", lambda: None)

    def capture_audit(_request: Request, action: str, success: bool = True, **details: object) -> None:
        audits.append((action, success, details))

    monkeypatch.setattr(github_admin, "write_audit", capture_audit)
    monkeypatch.setattr(github_admin, "set_flash", lambda *_args, **_kwargs: None)

    response = github_admin.github_network_settings(
        request,
        csrf_token="csrf",
        http_proxy=secret_proxy,
        clear_proxy=None,
        connect_timeout_seconds="12",
        read_timeout_seconds="90",
        retry_attempts="4",
    )

    assert response.status_code == 303
    assert written["FDEX_GITHUB_HTTP_PROXY"] == secret_proxy
    assert written["FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS"] == "12"
    assert written["FDEX_GITHUB_READ_TIMEOUT_SECONDS"] == "90"
    assert written["FDEX_GITHUB_RETRY_ATTEMPTS"] == "4"
    assert audits and audits[-1][0] == "save_github_network"
    assert secret_proxy not in repr(audits)
    assert "super-secret" not in repr(audits)


def test_github_admin_exposes_network_settings_and_public_probe() -> None:
    methods = {(route.path, tuple(sorted(route.methods or []))) for route in github_admin.router.routes}
    assert ("/admin/github-app/network", ("POST",)) in methods
    assert ("/admin/github-app/network/test", ("POST",)) in methods

    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/github_app_settings.html").read_text(encoding="utf-8")
    assert "GitHub 网络出口" in template
    assert "/admin/github-app/network/test" in template
    assert "github.com" in template
    assert "api.github.com" in template
