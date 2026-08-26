from __future__ import annotations

from app import agent_projects as legacy_agent_projects
from app.github_app_bootstrap import install_github_app_project_store


def test_bootstrap_keeps_legacy_module_identity_and_uses_installation_authority() -> None:
    original = legacy_agent_projects.agent_project_store
    try:
        install_github_app_project_store()
        assert legacy_agent_projects.agent_project_store.__module__ == "app.github_app_agent_projects"
        store = legacy_agent_projects.agent_project_store()
        assert store.__class__.__name__ == "InstallationAuthorityProjectStore"
        assert hasattr(store, "sync_owner_installations")
        assert hasattr(store, "account_policy")
    finally:
        legacy_agent_projects.agent_project_store = original
