from __future__ import annotations

_EGRESS_ROUTER_INSTALLED = False


def install_github_app_project_store() -> None:
    """Install GitHub App authority plus the admin-only dedicated GitHub egress control plane.

    `agent_projects` remains the compatibility module used by Android, older tests and migrations.
    Phase 7.10 changes the meaning of an Agent project: it is now an internal workspace/cache row,
    while the GitHub App installation is the only repository/GitHub permission authority.

    Phase 7.16 also attaches the GitHub/VLESS egress admin router to the existing `/admin` router
    before FastAPI includes it, and restores the managed Xray transient unit on service startup.
    The managed proxy is application-scoped: no system HTTP_PROXY/HTTPS_PROXY, routing table,
    iptables, DNS or global Git proxy is modified.
    """
    from app import agent_projects as legacy
    from app.github_app_installation_authority import agent_project_store as authority_store

    def compatibility_store():
        return authority_store()

    compatibility_store.__module__ = "app.github_app_agent_projects"
    legacy.agent_project_store = compatibility_store

    global _EGRESS_ROUTER_INSTALLED
    if not _EGRESS_ROUTER_INSTALLED:
        from app.admin_routes import router as admin_router
        from app.github_egress_admin_routes import router as github_egress_router

        admin_router.include_router(github_egress_router)
        _EGRESS_ROUTER_INSTALLED = True

    from app.github_egress import ensure_managed_egress_on_startup

    ensure_managed_egress_on_startup()
