from __future__ import annotations

import inspect

from app import agent_projects as legacy_agent_projects
from app.github_app_bootstrap import install_github_app_project_store


def test_bootstrap_keeps_legacy_module_identity_and_targets_installation_authority() -> None:
    original = legacy_agent_projects.agent_project_store
    try:
        install_github_app_project_store()
        assert legacy_agent_projects.agent_project_store.__module__ == "app.github_app_agent_projects"
        source = inspect.getsource(install_github_app_project_store)
        assert "github_app_installation_authority" in source
        assert "authority_store" in source
    finally:
        legacy_agent_projects.agent_project_store = original
