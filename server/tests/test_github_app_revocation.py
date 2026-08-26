from __future__ import annotations

from pathlib import Path

import pytest

from app import account_cleanup
from app.github_app import GitHubAppError
from app.github_app_agent_projects import GitHubAppAgentProjectStore

OWNER = "usr_1234567890abcdef12345678"


def _installation() -> dict[str, object]:
    return {
        "id": 9001,
        "app_id": 12345,
        "app_slug": "fdex-test",
        "account": {"id": 42, "login": "octocat", "type": "User"},
        "repository_selection": "selected",
        "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
        "suspended_at": None,
    }


class DeleteClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.deleted: list[int] = []

    def delete_installation(self, installation_id: int) -> None:
        self.deleted.append(installation_id)
        if self.error is not None:
            raise self.error


def _store(tmp_path: Path) -> GitHubAppAgentProjectStore:
    store = GitHubAppAgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")
    store.init()
    return store


def test_delete_connection_revokes_remote_installation_before_local_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    connection = store.save_github_app_connection(OWNER, installer_user_id="42", installation=_installation())
    client = DeleteClient()
    monkeypatch.setattr("app.github_app_agent_projects.GitHubAppClient", lambda: client)

    store.delete_connection(OWNER, int(connection["id"]))

    assert client.deleted == [9001]
    assert store.list_connections(OWNER) == []


def test_delete_connection_does_not_revoke_while_project_still_uses_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    connection = store.save_github_app_connection(OWNER, installer_user_id="42", installation=_installation())
    store.save_project(
        OWNER,
        name="alpha",
        repo_full_name="octocat/alpha",
        connection_id=int(connection["id"]),
    )
    client = DeleteClient()
    monkeypatch.setattr("app.github_app_agent_projects.GitHubAppClient", lambda: client)

    with pytest.raises(ValueError, match="still used"):
        store.delete_connection(OWNER, int(connection["id"]))

    assert client.deleted == []
    assert len(store.list_connections(OWNER)) == 1


def test_remote_revoke_failure_keeps_local_connection_for_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    connection = store.save_github_app_connection(OWNER, installer_user_id="42", installation=_installation())
    client = DeleteClient(error=GitHubAppError("GitHub HTTP 503"))
    monkeypatch.setattr("app.github_app_agent_projects.GitHubAppClient", lambda: client)

    with pytest.raises(ValueError, match="503"):
        store.delete_connection(OWNER, int(connection["id"]))

    assert client.deleted == [9001]
    assert len(store.list_connections(OWNER)) == 1


def test_account_cleanup_revokes_installation_before_dropping_owned_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    store.save_github_app_connection(OWNER, installer_user_id="42", installation=_installation())
    client = DeleteClient()

    class FakeTasks:
        def delete_owner(self, owner_id: str) -> int:
            assert owner_id == OWNER
            return 0

    class FakeWebFlows:
        def __init__(self, project_store=None) -> None:
            assert project_store is store

        def delete_owner(self, owner_id: str) -> int:
            assert owner_id == OWNER
            return 0

    class FakeAppFlows(FakeWebFlows):
        pass

    monkeypatch.setattr(account_cleanup, "agent_project_store", lambda: store)
    monkeypatch.setattr(account_cleanup, "agent_task_store", lambda: FakeTasks())
    monkeypatch.setattr(account_cleanup, "GitHubWebOAuthStore", FakeWebFlows)
    monkeypatch.setattr(account_cleanup, "GitHubAppInstallationFlowStore", FakeAppFlows)
    monkeypatch.setattr(account_cleanup, "GitHubAppClient", lambda: client)
    monkeypatch.setenv("FDEX_AGENT_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("FDEX_AGENT_WORKTREE_ROOT", str(tmp_path / "worktrees"))

    result = account_cleanup._purge_agent_resources_only(OWNER)

    assert client.deleted == [9001]
    assert result["github_app_installations_revoked"] == 1
    assert store.list_connections(OWNER) == []
