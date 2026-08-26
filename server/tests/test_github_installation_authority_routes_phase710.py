from __future__ import annotations

from pathlib import Path

from app.agent_policy_portal_routes import router as policy_router


def test_account_level_agent_policy_routes_exist() -> None:
    methods = {(route.path, tuple(sorted(route.methods or []))) for route in policy_router.routes}
    assert ("/account/agent/runtime", ("GET",)) in methods
    assert ("/account/agent/runtime/policy", ("POST",)) in methods
    assert ("/account/agent/runtime/sync", ("POST",)) in methods


def test_main_mounts_installation_authority_before_agent_routes() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "server/app/main.py").read_text(encoding="utf-8")
    bootstrap = source.index("install_github_app_project_store()")
    agent_import = source.index("from app.agent_routes")
    assert bootstrap < agent_import
    assert "app.include_router(agent_policy_portal_router)" in source
