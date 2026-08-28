from __future__ import annotations

from functools import lru_cache
from typing import Any

from app import github_app_agent_projects_core as _core
from app.agent_projects import (
    DB_PATH,
    KEY_PATH,
    apply_github_proxy_to_git_env,
)
from app.github_app import GitHubAppClient
from app.github_app_agent_projects_core import *  # noqa: F401,F403


class GitHubAppAgentProjectStore(_core.GitHubAppAgentProjectStore):
    """Phase 7.15 GitHub-App Agent store with dedicated GitHub Git egress.

    The production implementation remains in the compatibility core. Existing tests and operator
    integrations historically patch ``app.github_app_agent_projects.GitHubAppClient``; synchronize
    that public injection point before core methods create a client so the refactor does not break
    the established test seam or downstream overrides.
    """

    @staticmethod
    def _sync_client_factory() -> None:
        _core.GitHubAppClient = GitHubAppClient

    def connection_token(
        self,
        owner_id: str,
        connection_id: int,
        *,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> str:
        self._sync_client_factory()
        return super().connection_token(
            owner_id,
            connection_id,
            repository=repository,
            permissions=permissions,
        )

    def list_repositories(
        self,
        owner_id: str,
        connection_id: int,
        *,
        page: int = 1,
        per_page: int = 100,
        query: str = "",
    ) -> list[dict[str, Any]]:
        self._sync_client_factory()
        return super().list_repositories(
            owner_id,
            connection_id,
            page=page,
            per_page=per_page,
            query=query,
        )

    def delete_connection(self, owner_id: str, connection_id: int) -> None:
        self._sync_client_factory()
        return super().delete_connection(owner_id, connection_id)

    def _git_env(
        self,
        owner_id: str,
        connection_id: Any,
        *,
        required: bool = False,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> dict[str, str]:
        self._sync_client_factory()
        env = super()._git_env(
            owner_id,
            connection_id,
            required=required,
            repository=repository,
            permissions=permissions,
        )
        return apply_github_proxy_to_git_env(env)


@lru_cache(maxsize=1)
def agent_project_store() -> GitHubAppAgentProjectStore:
    store = GitHubAppAgentProjectStore(DB_PATH, KEY_PATH)
    store.init()
    return store
