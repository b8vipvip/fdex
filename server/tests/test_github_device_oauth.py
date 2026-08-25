from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.agent_projects import AgentProjectStore, GITHUB_ACCESS_TOKEN_URL, GITHUB_DEVICE_CODE_URL


def _store(tmp_path: Path) -> AgentProjectStore:
    return AgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDEX_GITHUB_OAUTH_CLIENT_ID", "Ov23liDeviceFlowTest")
    monkeypatch.setenv("FDEX_GITHUB_OAUTH_SCOPE", "repo read:user offline_access")


def _challenge() -> dict[str, object]:
    return {
        "device_code": "device-secret-that-must-never-leak",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }


def test_device_flow_is_owner_scoped_and_device_code_is_encrypted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    store = _store(tmp_path)
    monkeypatch.setattr(store, "_oauth_post", lambda url, form: _challenge())

    flow = store.start_device_flow("account-a")

    assert flow["status"] == "pending"
    assert flow["user_code"] == "ABCD-EFGH"
    assert flow["verification_uri"] == "https://github.com/login/device"
    assert "device_code" not in flow
    with store.db() as conn:
        row = conn.execute(
            "SELECT device_code_cipher FROM github_device_flows WHERE id=?",
            (flow["id"],),
        ).fetchone()
    assert row is not None
    assert "device-secret-that-must-never-leak" not in str(row[0])
    with pytest.raises(KeyError):
        store.get_device_flow("account-b", str(flow["id"]))


def test_device_poll_obeys_server_cadence_without_calling_github_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    store = _store(tmp_path)
    calls: list[str] = []

    def oauth(url: str, form: dict[str, str]) -> dict[str, object]:
        calls.append(url)
        return _challenge()

    monkeypatch.setattr(store, "_oauth_post", oauth)
    flow = store.start_device_flow("account-a")

    pending = store.poll_device_flow("account-a", str(flow["id"]))

    assert pending["status"] == "pending"
    assert pending["retry_after_seconds"] > 0
    assert calls == [GITHUB_DEVICE_CODE_URL]


def test_repeated_start_reuses_unexpired_challenge_and_does_not_hammer_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    store = _store(tmp_path)
    calls = 0

    def oauth(url: str, form: dict[str, str]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _challenge()

    monkeypatch.setattr(store, "_oauth_post", oauth)
    first = store.start_device_flow("account-a")
    second = store.start_device_flow("account-a")

    assert second["id"] == first["id"]
    assert calls == 1


def test_device_authorization_stores_rotating_secrets_and_returns_safe_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    store = _store(tmp_path)

    def oauth(url: str, form: dict[str, str]) -> dict[str, object]:
        if url == GITHUB_DEVICE_CODE_URL:
            return _challenge()
        assert url == GITHUB_ACCESS_TOKEN_URL
        assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
        return {
            "access_token": "github-oauth-access-secret",
            "refresh_token": "github-oauth-refresh-secret",
            "expires_in": 28800,
            "refresh_token_expires_in": 15811200,
            "scope": "repo read:user offline_access",
            "token_type": "bearer",
        }

    monkeypatch.setattr(store, "_oauth_post", oauth)
    monkeypatch.setattr(store, "_github_json", lambda token, url, **kwargs: {"id": 42, "login": "octocat"})
    flow = store.start_device_flow("account-a")
    with store.db() as conn:
        conn.execute("UPDATE github_device_flows SET next_poll_at='' WHERE id=?", (flow["id"],))

    authorized = store.poll_device_flow("account-a", str(flow["id"]))

    assert authorized["status"] == "authorized"
    connection = authorized["connection"]
    assert connection["login"] == "octocat"
    assert connection["auth_type"] == "oauth"
    assert connection["refresh_configured"] is True
    assert "token" not in connection
    serialized = str(authorized)
    assert "github-oauth-access-secret" not in serialized
    assert "github-oauth-refresh-secret" not in serialized
    secret = store.get_connection("account-a", connection["id"], secret=True)
    assert secret["token"] == "github-oauth-access-secret"
    assert secret["refresh_token"] == "github-oauth-refresh-secret"
    assert store.poll_device_flow("account-a", str(flow["id"]))["status"] == "authorized"
    with store.db() as conn:
        raw = conn.execute(
            "SELECT token_cipher,refresh_token_cipher FROM github_connections WHERE id=?",
            (connection["id"],),
        ).fetchone()
    assert raw is not None
    assert "github-oauth" not in f"{raw[0]}{raw[1]}"


def test_refresh_rotates_both_tokens_and_revalidates_github_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    store = _store(tmp_path)
    connection = store._save_oauth_connection(
        "account-a",
        {"id": 42, "login": "octocat"},
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 1,
            "refresh_token_expires_in": 3600,
            "scope": "repo offline_access",
        },
    )
    with store.db() as conn:
        conn.execute(
            "UPDATE github_connections SET token_expires_at='2020-01-01T00:00:00+00:00' WHERE id=?",
            (connection["id"],),
        )

    monkeypatch.setattr(
        store,
        "_oauth_post",
        lambda url, form: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 28800,
            "refresh_token_expires_in": 15811200,
            "scope": "repo offline_access",
        },
    )
    monkeypatch.setattr(store, "_github_json", lambda token, url, **kwargs: {"id": 42, "login": "octocat"})

    assert store.connection_token("account-a", connection["id"]) == "new-access"
    rotated = store.get_connection("account-a", connection["id"], secret=True)
    assert rotated["token"] == "new-access"
    assert rotated["refresh_token"] == "new-refresh"
    assert rotated["needs_reconnect"] is False


def test_refresh_fails_closed_if_github_identity_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    store = _store(tmp_path)
    connection = store._save_oauth_connection(
        "account-a",
        {"id": 42, "login": "octocat"},
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 1,
            "refresh_token_expires_in": 3600,
        },
    )
    with store.db() as conn:
        conn.execute(
            "UPDATE github_connections SET token_expires_at='2020-01-01T00:00:00+00:00' WHERE id=?",
            (connection["id"],),
        )
    monkeypatch.setattr(
        store,
        "_oauth_post",
        lambda url, form: {
            "access_token": "wrong-user-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 28800,
        },
    )
    monkeypatch.setattr(store, "_github_json", lambda token, url, **kwargs: {"id": 99, "login": "intruder"})

    with pytest.raises(ValueError, match="identity changed"):
        store.connection_token("account-a", connection["id"])
    assert store.get_connection("account-a", connection["id"])["needs_reconnect"] is True


def test_repository_discovery_is_owner_scoped_and_returns_only_safe_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(store, "_github_json", lambda token, url, **kwargs: {"id": 42, "login": "octocat"})
    connection = store.save_connection("account-a", "Legacy", "pat-secret-for-tests-123")
    monkeypatch.setattr(
        store,
        "_github_api",
        lambda token, url, **kwargs: [
            {
                "id": 7,
                "name": "fdex",
                "full_name": "octocat/fdex",
                "private": True,
                "default_branch": "main",
                "permissions": {"pull": True, "push": True, "admin": False},
                "archived": False,
                "description": "FDEX app",
                "updated_at": "2026-08-25T00:00:00Z",
                "ssh_url": "must-not-be-returned",
                "owner": {"private_profile": "must-not-be-returned"},
            }
        ],
    )

    repositories = store.list_repositories("account-a", connection["id"], query="fdex")

    assert repositories == [
        {
            "id": 7,
            "name": "fdex",
            "full_name": "octocat/fdex",
            "private": True,
            "default_branch": "main",
            "can_push": True,
            "archived": False,
            "description": "FDEX app",
            "updated_at": "2026-08-25T00:00:00Z",
        }
    ]
    with pytest.raises(KeyError):
        store.list_repositories("account-b", connection["id"])


def test_existing_connection_database_migrates_without_losing_pat(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE github_connections (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL,name TEXT NOT NULL,
                   login TEXT NOT NULL DEFAULT '',token_cipher TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL,updated_at TEXT NOT NULL
               )"""
        )
    store = AgentProjectStore(path, tmp_path / "projects.key")

    store.init()

    with store.db() as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(github_connections)")}
    assert {"auth_type", "refresh_token_cipher", "token_expires_at", "needs_reconnect"} <= columns
