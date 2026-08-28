from __future__ import annotations

from functools import lru_cache
from typing import Any

from app import github_app_agent_projects_core as _core
from app.agent_projects import (
    DB_PATH,
    KEY_PATH,
    apply_github_proxy_to_git_env,
)
from app.github_app_agent_projects_core import *  # noqa: F401,F403


class GitHubAppAgentProjectStore(_core.GitHubAppAgentProjectStore):
    """Phase 7.15 GitHub-App Agent store with dedicated GitHub Git egress."""

    def _git_env(
        self,
        owner_id: str,
        connection_id: Any,
        *,
        required: bool = False,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> dict[str, str]:
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
