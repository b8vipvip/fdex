from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import codex_capability_control as control


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def request(self, method: str, params: dict[str, object], *, timeout: float = 30.0):
        self.calls.append((method, params))
        if method == "skills/list":
            return {
                "data": [{
                    "cwd": "/repo",
                    "skills": [{
                        "name": "review",
                        "description": "Review code",
                        "shortDescription": "Review",
                        "path": "/home/skills/review/SKILL.md",
                        "scope": "user",
                        "enabled": True,
                        "pluginId": None,
                    }],
                    "errors": [],
                }]
            }
        if method == "skills/config/write":
            return {}
        if method == "hooks/list":
            return {
                "data": [{
                    "cwd": "/repo",
                    "hooks": [{
                        "event": "afterTurn",
                        "source": "user",
                        "trustStatus": "trusted",
                        "handlerType": "command",
                    }],
                    "warnings": [],
                    "errors": [],
                }]
            }
        if method == "plugin/list":
            return {
                "marketplaces": [{
                    "name": "local-test",
                    "path": "/home/plugins/marketplace.json",
                    "plugins": [{
                        "id": "plugin.review",
                        "name": "review-plugin",
                        "description": "Review plugin",
                        "availability": "AVAILABLE",
                        "installPolicy": "AVAILABLE",
                    }],
                }],
                "marketplaceLoadErrors": [],
            }
        if method == "plugin/installed":
            return {"marketplaces": [], "marketplaceLoadErrors": []}
        if method == "plugin/read":
            return {
                "plugin": {
                    "name": "review-plugin",
                    "description": "Review plugin detail",
                    "path": "/home/plugins/review-plugin",
                }
            }
        raise AssertionError(f"unexpected method {method}")


def test_official_capability_shapes_are_flattened() -> None:
    client = FakeClient()
    skills, errors = control._flatten_skills(
        asyncio.run(client.request("skills/list", {"cwds": ["/repo"]}))
    )
    hooks, hook_errors = control._flatten_hooks(
        asyncio.run(client.request("hooks/list", {"cwds": ["/repo"]}))
    )
    markets, plugin_errors = control._flatten_marketplaces(
        asyncio.run(client.request("plugin/list", {"cwds": ["/repo"]}))
    )
    assert skills[0]["name"] == "review"
    assert skills[0]["path"] == "/home/skills/review/SKILL.md"
    assert hooks[0]["event"] == "afterTurn"
    assert markets[0]["plugins"][0]["name"] == "review-plugin"
    assert markets[0]["plugins"][0]["availability"] == "AVAILABLE"
    assert markets[0]["plugins"][0]["install_policy"] == "AVAILABLE"
    assert errors == [] and hook_errors == [] and plugin_errors == []


def test_skill_write_revalidates_exact_official_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = FakeClient()

    async def fake_with_client(owner_id, project_id, operation):
        assert owner_id == "usr_phase730"
        return await operation(client, tmp_path)

    monkeypatch.setattr(control, "_with_client", fake_with_client)
    result = asyncio.run(
        control.set_skill_enabled(
            "usr_phase730",
            path="/home/skills/review/SKILL.md",
            enabled=False,
        )
    )
    assert result["enabled"] is False
    assert [method for method, _params in client.calls] == ["skills/list", "skills/config/write"]
    assert client.calls[0][1]["forceReload"] is True
    assert client.calls[1][1] == {"path": "/home/skills/review/SKILL.md", "enabled": False}

    with pytest.raises(control.CodexCapabilityError, match="不在当前账号/项目"):
        asyncio.run(
            control.set_skill_enabled(
                "usr_phase730",
                path="/outside/evil/SKILL.md",
                enabled=True,
            )
        )
    assert [method for method, _params in client.calls].count("skills/config/write") == 1


def test_inventory_is_local_only_and_never_refetches_remote_plugins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeClient()

    async def fake_with_client(owner_id, project_id, operation):
        return await operation(client, tmp_path)

    monkeypatch.setattr(control, "_with_client", fake_with_client)
    monkeypatch.setattr(control, "_project_cwd", lambda owner_id, project_id: (tmp_path, None))
    inventory = asyncio.run(control.capability_inventory("usr_phase730", force_reload=True))
    assert inventory["plugin_mutation_allowed"] is False
    plugin_list = next(params for method, params in client.calls if method == "plugin/list")
    assert plugin_list["marketplaceKinds"] == ["local"]
    assert plugin_list["forceRefetch"] is False
    called = {method for method, _params in client.calls}
    assert "marketplace/add" not in called
    assert "marketplace/remove" not in called
    assert "plugin/install" not in called
    assert "plugin/uninstall" not in called


def test_local_plugin_read_revalidates_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = FakeClient()

    async def fake_with_client(owner_id, project_id, operation):
        return await operation(client, tmp_path)

    monkeypatch.setattr(control, "_with_client", fake_with_client)
    detail = asyncio.run(
        control.read_local_plugin(
            "usr_phase730",
            marketplace_path="/home/plugins/marketplace.json",
            plugin_name="review-plugin",
        )
    )
    assert detail["name"] == "review-plugin"
    assert [method for method, _params in client.calls] == ["plugin/list", "plugin/read"]

    with pytest.raises(control.CodexCapabilityError, match="不在当前账号/项目"):
        asyncio.run(
            control.read_local_plugin(
                "usr_phase730",
                marketplace_path="/tmp/evil.json",
                plugin_name="review-plugin",
            )
        )
    assert [method for method, _params in client.calls].count("plugin/read") == 1


def test_wider_plugin_mutations_remain_hard_blocked_after_phase732() -> None:
    for action in ("marketplace/add", "marketplace/remove", "marketplace/upgrade", "plugin/share/save"):
        with pytest.raises(control.CodexCapabilityError, match="Phase 7.32"):
            control.assert_plugin_mutation_blocked(action)


def test_project_inventory_never_prepares_or_fetches_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    scoped_root = tmp_path / "owners" / "usr_phase730"
    repo = scoped_root / "projects" / "7" / "repository"
    worktrees = scoped_root / "projects" / "7" / "worktrees"
    repo.mkdir(parents=True)

    class FakeStore:
        def get_project(self, owner_id: str, project_id: int):
            return {"id": 7, "name": "demo", "repo_full_name": "b8vipvip/fdex", "enabled": True}

        def project_paths(self, owner_id: str, project_id: int):
            return repo, worktrees

        def owner_root(self, owner_id: str):
            return scoped_root

        def prepare_repository(self, *args, **kwargs):
            raise AssertionError("capability inventory must never clone/fetch")

    fake_store = FakeStore()
    monkeypatch.setattr(control, "_codex_home", lambda owner_id: home)
    monkeypatch.setattr(control, "agent_project_store", lambda: fake_store)
    cwd, project = control._project_cwd("usr_phase730", 7)
    assert cwd == home and project and project["id"] == 7
    (repo / ".git").mkdir()
    cwd, _project = control._project_cwd("usr_phase730", 7)
    assert cwd == repo.resolve()


def test_phase730_route_ui_and_native_method_wiring() -> None:
    root = Path(__file__).parents[1] / "app"
    routes = (root / "codex_capability_routes.py").read_text(encoding="utf-8")
    source = (root / "codex_capability_control.py").read_text(encoding="utf-8")
    parent_routes = (root / "codex_input_center_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent_capabilities.html").read_text(encoding="utf-8")
    input_center = (root / "templates" / "user_agent_input_center.html").read_text(encoding="utf-8")

    assert "router.include_router(codex_capability_router)" in parent_routes
    assert 'prefix="/capabilities"' in routes
    assert "/account/agent/capabilities" in input_center
    for method in ("skills/list", "skills/config/write", "hooks/list", "plugin/list", "plugin/installed", "plugin/read"):
        assert method in source
    assert '"marketplaceKinds": ["local"]' in source
    assert '"forceRefetch": False' in source
    inventory_section = source.split("async def capability_inventory", 1)[1].split("async def set_skill_enabled", 1)[0]
    assert '"plugin/install",' not in inventory_section
    assert '"plugin/uninstall",' not in inventory_section
    assert "Phase 7.32" in source
    assert 'action="/account/agent/capabilities/plugins/install"' not in template
    assert 'action="/account/agent/capabilities/plugins/uninstall"' not in template
    assert "验证 Plugin 写安全门" in template
    assert "plugin/install、plugin/uninstall" in template
