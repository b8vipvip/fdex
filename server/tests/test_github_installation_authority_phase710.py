from __future__ import annotations

from pathlib import Path

import pytest

from app.github_app_installation_authority import InstallationAuthorityProjectStore

OWNER = "usr_1234567890abcdef12345678"


def _installation() -> dict[str, object]:
    return {
        "id": 9001,
        "app_id": 12345,
        "app_slug": "fdex-test",
        "account": {"id": 42, "login": "octocat", "type": "User"},
        "repository_selection": "all",
        "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
        "suspended_at": None,
    }


def _repo(name: str, *, can_push: bool = True, can_pr: bool = True, archived: bool = False) -> dict[str, object]:
    return {
        "id": abs(hash(name)) % 100000 + 1,
        "name": name,
        "full_name": f"octocat/{name}",
        "private": True,
        "default_branch": "main",
        "can_push": can_push,
        "can_pr": can_pr,
        "archived": archived,
        "description": "test repository",
        "updated_at": "2026-08-26T00:00:00Z",
    }


class FakeInstallationAuthorityStore(InstallationAuthorityProjectStore):
    def __init__(self, db_path: Path, key_path: Path) -> None:
        super().__init__(db_path, key_path)
        self.repositories: list[dict[str, object]] = []

    def list_repositories(
        self,
        owner_id: str,
        connection_id: int,
        *,
        page: int = 1,
        per_page: int = 100,
        query: str = "",
    ) -> list[dict[str, object]]:
        assert owner_id == OWNER
        start = (page - 1) * per_page
        items = self.repositories[start : start + per_page]
        needle = query.strip().casefold()
        if needle:
            items = [item for item in items if needle in str(item["full_name"]).casefold()]
        return [dict(item) for item in items]


def _store(tmp_path: Path) -> tuple[FakeInstallationAuthorityStore, int]:
    store = FakeInstallationAuthorityStore(tmp_path / "projects.db", tmp_path / "projects.key")
    store.init()
    connection = store.save_github_app_connection(
        OWNER,
        installer_user_id="42",
        installation=_installation(),
    )
    return store, int(connection["id"])


def test_installation_sync_automatically_creates_all_agent_workspaces(tmp_path: Path) -> None:
    store, connection_id = _store(tmp_path)
    store.repositories = [_repo("alpha"), _repo("beta", can_pr=False)]

    count = store.sync_connection(OWNER, connection_id)
    projects = store.list_projects(OWNER, enabled_only=True)

    assert count == 2
    assert [item["repo_full_name"] for item in projects] == ["octocat/alpha", "octocat/beta"]
    assert all(item["managed_by_installation"] for item in projects)
    assert all(item["connection_id"] == connection_id for item in projects)
    alpha = next(item for item in projects if item["repo_full_name"] == "octocat/alpha")
    beta = next(item for item in projects if item["repo_full_name"] == "octocat/beta")
    assert alpha["allow_push"] is True and alpha["allow_pr"] is True
    assert beta["allow_push"] is True and beta["allow_pr"] is False


def test_per_repository_save_flags_cannot_override_installation_authority(tmp_path: Path) -> None:
    store, connection_id = _store(tmp_path)
    store.repositories = [_repo("alpha", can_push=True, can_pr=True)]
    store.sync_connection(OWNER, connection_id)

    project = store.save_project(
        OWNER,
        name="try-to-restrict",
        repo_full_name="octocat/alpha",
        connection_id=connection_id,
        allow_push=False,
        allow_pr=False,
        allow_network=True,
        sandbox_memory_mb=9999,
        sandbox_cpu_percent=777,
    )

    assert project["name"] == "alpha"
    assert project["allow_push"] is True
    assert project["allow_pr"] is True
    assert project["allow_network"] is False
    assert project["sandbox_memory_mb"] != 9999


def test_account_runtime_policy_applies_once_to_every_authorized_repository(tmp_path: Path) -> None:
    store, connection_id = _store(tmp_path)
    store.repositories = [_repo("alpha"), _repo("beta")]
    store.sync_connection(OWNER, connection_id)

    policy = store.save_account_policy(
        OWNER,
        allow_network=True,
        sandbox_memory_mb=3072,
        sandbox_cpu_percent=225,
    )
    projects = store.list_projects(OWNER, enabled_only=True)

    assert policy["allow_network"] is True
    assert policy["sandbox_memory_mb"] == 3072
    assert policy["sandbox_cpu_percent"] == 225
    assert all(item["allow_network"] is True for item in projects)
    assert all(item["sandbox_memory_mb"] == 3072 for item in projects)
    assert all(item["sandbox_cpu_percent"] == 225 for item in projects)


def test_repository_removed_from_github_installation_is_disabled_but_history_id_survives(tmp_path: Path) -> None:
    store, connection_id = _store(tmp_path)
    store.repositories = [_repo("alpha"), _repo("beta")]
    store.sync_connection(OWNER, connection_id)
    before = {item["repo_full_name"]: int(item["id"]) for item in store.list_projects(OWNER)}

    store.repositories = [_repo("alpha")]
    store.sync_connection(OWNER, connection_id)
    all_projects = {item["repo_full_name"]: item for item in store.list_projects(OWNER)}

    assert all_projects["octocat/alpha"]["enabled"] is True
    assert all_projects["octocat/beta"]["enabled"] is False
    assert all_projects["octocat/beta"]["allow_push"] is False
    assert all_projects["octocat/beta"]["allow_pr"] is False
    assert int(all_projects["octocat/beta"]["id"]) == before["octocat/beta"]
    assert [item["repo_full_name"] for item in store.list_projects(OWNER, enabled_only=True)] == ["octocat/alpha"]


def test_installation_managed_project_cannot_be_manually_deleted(tmp_path: Path) -> None:
    store, connection_id = _store(tmp_path)
    store.repositories = [_repo("alpha")]
    store.sync_connection(OWNER, connection_id)
    project = store.list_projects(OWNER, enabled_only=True)[0]

    with pytest.raises(ValueError, match="GitHub App"):
        store.delete_project(OWNER, int(project["id"]))


def test_web_ui_no_longer_contains_per_repository_agent_permission_forms() -> None:
    root = Path(__file__).resolve().parents[2]
    github_template = (root / "server/app/templates/user_github.html").read_text(encoding="utf-8")
    agent_template = (root / "server/app/templates/user_agent.html").read_text(encoding="utf-8")
    settings_template = (root / "server/app/templates/user_agent_settings.html").read_text(encoding="utf-8")

    assert "添加到 Coding Agent" not in github_template
    assert 'name="allow_push"' not in github_template
    assert 'name="allow_pr"' not in github_template
    assert 'name="allow_network"' not in github_template
    assert "GitHub App Installation" in github_template
    assert "不需要逐仓库" in agent_template
    assert "账号级运行策略" in agent_template
    assert 'name="allow_network"' in settings_template
    assert "/account/agent/runtime/sync" in settings_template
