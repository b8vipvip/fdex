from __future__ import annotations


def install_github_app_project_store() -> None:
    """Install the GitHub App installation-authority store before consumers import it.

    `agent_projects` remains the compatibility module used by Android, older tests and migrations.
    Phase 7.10 changes the meaning of an Agent project: it is now an internal workspace/cache row,
    while the GitHub App installation is the only repository/GitHub permission authority.

    Keep the compatibility factory's module name stable for Phase 7.7 structure tests and older
    integrations that identify the GitHub App-aware store by its historical module name.
    """
    from app import agent_projects as legacy
    from app.github_app_installation_authority import agent_project_store as authority_store

    def compatibility_store():
        return authority_store()

    compatibility_store.__module__ = "app.github_app_agent_projects"
    legacy.agent_project_store = compatibility_store
