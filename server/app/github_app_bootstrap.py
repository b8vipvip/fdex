from __future__ import annotations


def install_github_app_project_store() -> None:
    """Install the Phase 7.7 project-store implementation before consumers import it.

    `agent_projects` remains the compatibility module used by older tests and migrations. The
    production application replaces only its cached factory before route/runtime modules import
    `agent_project_store`, so existing APIs keep their stable import path while GitHub App
    installations gain ephemeral-token behavior without duplicating the legacy OAuth/PAT code.
    """
    from app import agent_projects as legacy
    from app.github_app_agent_projects import agent_project_store

    legacy.agent_project_store = agent_project_store
