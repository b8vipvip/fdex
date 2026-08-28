from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import agent_projects as agent_projects_module
from app.agent_projects import AgentProjectStore
from app.github_app import GitHubAppClient
from app.github_app_agent_projects import GitHubAppAgentProjectStore


def _store(tmp_path: Path) -> AgentProjectStore:
    return AgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")


def _configs(env: dict[str, str]) -> dict[str, str]:
    count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    return {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(count)
    }


def test_legacy_agent_git_uses_dedicated_github_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FDEX_GITHUB_HTTP_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("FDEX_GITHUB_READ_TIMEOUT_SECONDS", "90")
    store = _store(tmp_path)
    monkeypatch.setattr(store, "connection_token", lambda owner_id, connection_id: "installation-or-oauth-token")

    env = store._git_env("account-a", 7)
    configs = _configs(env)

    assert configs["http.https://github.com.proxy"] == "http://127.0.0.1:10808"
    assert configs["http.https://github.com.lowSpeedLimit"] == "1"
    assert configs["http.https://github.com.lowSpeedTime"] == "90"
    assert configs["http.extraHeader"].startswith("AUTHORIZATION: basic ")


def test_github_app_agent_git_uses_same_dedicated_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FDEX_GITHUB_HTTP_PROXY", "http://127.0.0.1:10808")
    store = GitHubAppAgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")
    monkeypatch.setattr(
        store,
        "connection_token",
        lambda owner_id, connection_id, repository="", permissions=None: "short-lived-installation-token",
    )

    env = store._git_env(
        "account-a",
        9,
        repository="octo/repo",
        permissions={"contents": "read"},
    )
    configs = _configs(env)

    assert configs["http.https://github.com.proxy"] == "http://127.0.0.1:10808"
    assert configs["http.extraHeader"].startswith("AUTHORIZATION: basic ")


def test_invalid_non_http_github_proxy_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FDEX_GITHUB_HTTP_PROXY", "socks5://127.0.0.1:10808")
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="http://"):
        store._git_env("account-a", None)


def test_legacy_api_and_oauth_delegate_to_shared_github_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(self: GitHubAppClient, method: str, url: str, **kwargs: Any) -> Any:
        calls.append((method, url, kwargs))
        if "oauth" in url:
            return {"device_code": "x"}
        return {"ok": True}

    monkeypatch.setattr(GitHubAppClient, "_request", fake_request)

    assert AgentProjectStore._github_api("secret", "https://api.github.com/meta") == {"ok": True}
    assert AgentProjectStore._oauth_post(
        "https://github.com/login/oauth/access_token",
        {"client_id": "client"},
    ) == {"device_code": "x"}

    api_call = calls[0]
    assert api_call[2]["token"] == "secret"
    assert api_call[2]["retry_safe"] is True
    oauth_call = calls[1]
    assert oauth_call[2]["auth"] == "none"
    assert oauth_call[2]["retry_safe"] is False


def test_git_fetch_retries_transient_network_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FDEX_GITHUB_RETRY_ATTEMPTS", "3")
    calls = 0

    def fake_run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="fatal: Failed to connect to github.com")
        return SimpleNamespace(returncode=0, stdout="updated", stderr="")

    monkeypatch.setattr(agent_projects_module._core.subprocess, "run", fake_run)
    monkeypatch.setattr(agent_projects_module.time, "sleep", lambda _seconds: None)

    output = AgentProjectStore._git(
        ("git", "fetch", "origin", "--prune"),
        cwd=tmp_path,
        env={},
        timeout=30,
    )

    assert output == "updated"
    assert calls == 2


def test_failed_clone_cleans_partial_directory_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FDEX_GITHUB_RETRY_ATTEMPTS", "2")
    destination = tmp_path / "repository"
    calls = 0

    def fake_run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            destination.mkdir(parents=True)
            (destination / "partial.pack").write_text("partial", encoding="utf-8")
            return SimpleNamespace(returncode=1, stdout="", stderr="RPC failed; early EOF")
        assert not destination.exists()
        return SimpleNamespace(returncode=0, stdout="cloned", stderr="")

    monkeypatch.setattr(agent_projects_module._core.subprocess, "run", fake_run)
    monkeypatch.setattr(agent_projects_module.time, "sleep", lambda _seconds: None)

    output = AgentProjectStore._git(
        ("git", "clone", "--no-tags", "https://github.com/octo/repo.git", str(destination)),
        cwd=tmp_path,
        env={},
        timeout=300,
    )

    assert output == "cloned"
    assert calls == 2
