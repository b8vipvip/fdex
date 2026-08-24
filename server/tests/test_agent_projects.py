from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_projects import AgentProjectStore


def _store(tmp_path: Path) -> AgentProjectStore:
    return AgentProjectStore(tmp_path / "projects.db", tmp_path / "projects.key")


def test_projects_are_scoped_by_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setenv("FDEX_AGENT_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    project_a = store.save_project("account-a", name="A", repo_full_name="octo/a")
    project_b = store.save_project("account-b", name="B", repo_full_name="octo/b")

    assert [item["id"] for item in store.list_projects("account-a")] == [project_a["id"]]
    assert [item["id"] for item in store.list_projects("account-b")] == [project_b["id"]]
    with pytest.raises(KeyError):
        store.get_project("account-a", project_b["id"])


def test_project_paths_keep_account_project_task_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setenv("FDEX_AGENT_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    project = store.save_project("local", name="FDEX", repo_full_name="b8vipvip/fdex")
    repo, worktrees = store.project_paths("local", project["id"])
    assert "owners/local/projects" in repo.as_posix()
    assert repo.name == "repository"
    assert worktrees.name == "worktrees"
    assert repo.parent == worktrees.parent


def test_github_token_is_encrypted_and_not_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(store, "_github_json", lambda token, url, **kwargs: {"login": "octocat"})
    connection = store.save_connection("local", "Primary", "ghp_super_secret_token")
    assert connection["login"] == "octocat"
    assert connection["token_configured"] is True
    assert "token" not in connection

    with store.db() as conn:
        row = conn.execute("SELECT token_cipher FROM github_connections WHERE id=?", (connection["id"],)).fetchone()
    assert row is not None
    assert "ghp_super_secret_token" not in str(row[0])
    secret = store.get_connection("local", connection["id"], secret=True)
    assert secret["token"] == "ghp_super_secret_token"


def test_project_remote_permissions_and_sandbox_defaults(tmp_path: Path) -> None:
    store = _store(tmp_path)
    read_only = store.save_project("local", name="Read only", repo_full_name="octo/read-only")
    assert read_only["allow_push"] is False
    assert read_only["allow_pr"] is False
    assert read_only["allow_network"] is False
    assert read_only["sandbox_memory_mb"] == 2048
    assert read_only["sandbox_cpu_percent"] == 150

    pr_project = store.save_project(
        "local",
        name="PR",
        repo_full_name="octo/pr",
        allow_pr=True,
        allow_network=True,
        sandbox_memory_mb=3072,
        sandbox_cpu_percent=200,
    )
    assert pr_project["allow_pr"] is True
    assert pr_project["allow_push"] is True
    assert pr_project["allow_network"] is True
    assert pr_project["sandbox_memory_mb"] == 3072
    assert pr_project["sandbox_cpu_percent"] == 200


def test_owner_scope_rejects_path_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for owner_id in ("../escape", "..", "."):
        with pytest.raises(ValueError):
            store.save_project(owner_id, name="Bad", repo_full_name="octo/repo")
