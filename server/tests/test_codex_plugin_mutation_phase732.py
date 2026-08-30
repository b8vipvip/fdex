from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import codex_capability_control as control


MARKET = "/srv/fdex/plugins/local-marketplace.json"
OTHER_MARKET = "/srv/fdex/plugins/other-marketplace.json"
PLUGIN_ID = "plugin.review"
PLUGIN_NAME = "review-plugin"


class PluginClient:
    def __init__(
        self,
        *,
        availability: str = "AVAILABLE",
        install_policy: str = "AVAILABLE",
        confirm_wrong_market: bool = False,
        initially_installed: bool = False,
    ) -> None:
        self.availability = availability
        self.install_policy = install_policy
        self.confirm_wrong_market = confirm_wrong_market
        self.installed = initially_installed
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def _plugin(self, *, installed: bool | None = None) -> dict[str, object]:
        return {
            "id": PLUGIN_ID,
            "name": PLUGIN_NAME,
            "description": "Review plugin",
            "version": "1.2.3",
            "localVersion": "1.2.3" if (self.installed if installed is None else installed) else None,
            "installed": self.installed if installed is None else installed,
            "enabled": True,
            "availability": self.availability,
            "installPolicy": self.install_policy,
        }

    async def request(self, method: str, params: dict[str, object], *, timeout: float = 30.0):
        self.calls.append((method, params, timeout))
        if method == "plugin/list":
            return {
                "marketplaces": [{"name": "local", "path": MARKET, "plugins": [self._plugin()]}],
                "marketplaceLoadErrors": [],
            }
        if method == "plugin/read":
            return {
                "plugin": {
                    "id": PLUGIN_ID,
                    "name": PLUGIN_NAME,
                    "description": "Review plugin detail",
                    "path": "/srv/fdex/plugins/review-plugin",
                }
            }
        if method == "plugin/install":
            self.installed = True
            return {}
        if method == "plugin/uninstall":
            self.installed = False
            return {}
        if method == "plugin/installed":
            if not self.installed:
                return {"marketplaces": [], "marketplaceLoadErrors": []}
            path = OTHER_MARKET if self.confirm_wrong_market else MARKET
            return {
                "marketplaces": [{"name": "installed", "path": path, "plugins": [self._plugin(installed=True)]}],
                "marketplaceLoadErrors": [],
            }
        raise AssertionError(f"unexpected method {method}")


def _enable_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        control,
        "codex_process_isolation_status",
        lambda: {"ready": True, "enforced": True, "required": True, "reason": ""},
    )


def _bind_client(monkeypatch: pytest.MonkeyPatch, client: PluginClient, cwd: Path) -> None:
    async def fake_with_client(owner_id, project_id, operation):
        assert owner_id == "usr_phase732"
        return await operation(client, cwd)

    monkeypatch.setattr(control, "_with_client", fake_with_client)


def test_plugin_mutation_gate_fails_closed_before_host_when_cgroup_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        control,
        "codex_process_isolation_status",
        lambda: {"ready": False, "enforced": False, "required": True, "reason": "systemd unavailable"},
    )

    async def forbidden_with_client(*args, **kwargs):
        called.append(True)
        raise AssertionError("mutation must fail before creating a Codex Host")

    monkeypatch.setattr(control, "_with_client", forbidden_with_client)
    with pytest.raises(control.CodexCapabilityError, match="systemd unavailable"):
        asyncio.run(
            control.install_local_plugin(
                "usr_phase732",
                marketplace_path=MARKET,
                plugin_name=PLUGIN_NAME,
            )
        )
    assert called == []


def test_local_install_revalidates_inventory_and_confirms_same_marketplace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_isolation(monkeypatch)
    client = PluginClient()
    _bind_client(monkeypatch, client, tmp_path)

    installed = asyncio.run(
        control.install_local_plugin(
            "usr_phase732",
            marketplace_path=MARKET,
            plugin_name=PLUGIN_NAME,
        )
    )
    assert installed["id"] == PLUGIN_ID
    assert installed["installed"] is True
    assert [method for method, _params, _timeout in client.calls] == [
        "plugin/list",
        "plugin/read",
        "plugin/install",
        "plugin/installed",
    ]
    assert client.calls[0][1] == {
        "cwds": [str(tmp_path)],
        "marketplaceKinds": ["local"],
        "forceRefetch": False,
    }
    assert client.calls[1][1] == {
        "marketplacePath": MARKET,
        "remoteMarketplaceName": None,
        "pluginName": PLUGIN_NAME,
    }
    assert client.calls[2][1] == client.calls[1][1]
    assert client.calls[2][2] == 60.0


def test_install_rejects_unknown_policy_before_plugin_read_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_isolation(monkeypatch)
    client = PluginClient(install_policy="FUTURE_UNKNOWN_POLICY")
    _bind_client(monkeypatch, client, tmp_path)

    with pytest.raises(control.CodexCapabilityError, match="fail-closed"):
        asyncio.run(
            control.install_local_plugin(
                "usr_phase732",
                marketplace_path=MARKET,
                plugin_name=PLUGIN_NAME,
            )
        )
    assert [method for method, _params, _timeout in client.calls] == ["plugin/list"]


def test_install_rejects_unavailable_plugin_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_isolation(monkeypatch)
    client = PluginClient(availability="DISABLED_BY_ADMIN")
    _bind_client(monkeypatch, client, tmp_path)

    with pytest.raises(control.CodexCapabilityError, match="availability"):
        asyncio.run(
            control.install_local_plugin(
                "usr_phase732",
                marketplace_path=MARKET,
                plugin_name=PLUGIN_NAME,
            )
        )
    assert [method for method, _params, _timeout in client.calls] == ["plugin/list"]


def test_install_does_not_accept_same_name_confirmation_from_other_marketplace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_isolation(monkeypatch)
    client = PluginClient(confirm_wrong_market=True)
    _bind_client(monkeypatch, client, tmp_path)

    with pytest.raises(control.CodexCapabilityError, match="相同 marketplace"):
        asyncio.run(
            control.install_local_plugin(
                "usr_phase732",
                marketplace_path=MARKET,
                plugin_name=PLUGIN_NAME,
            )
        )
    assert "plugin/install" in [method for method, _params, _timeout in client.calls]


def test_uninstall_requires_exact_installed_id_and_confirms_disappearance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_isolation(monkeypatch)
    client = PluginClient(initially_installed=True)
    _bind_client(monkeypatch, client, tmp_path)

    removed = asyncio.run(
        control.uninstall_plugin(
            "usr_phase732",
            plugin_id=PLUGIN_ID,
        )
    )
    assert removed["id"] == PLUGIN_ID
    assert [method for method, _params, _timeout in client.calls] == [
        "plugin/installed",
        "plugin/uninstall",
        "plugin/installed",
    ]
    assert client.calls[1][1] == {"pluginId": PLUGIN_ID}
    assert client.calls[1][2] == 60.0


def test_uninstall_rejects_non_inventory_id_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_isolation(monkeypatch)
    client = PluginClient(initially_installed=True)
    _bind_client(monkeypatch, client, tmp_path)

    with pytest.raises(control.CodexCapabilityError, match="唯一清单"):
        asyncio.run(
            control.uninstall_plugin(
                "usr_phase732",
                plugin_id="plugin.not-present",
            )
        )
    assert [method for method, _params, _timeout in client.calls] == ["plugin/installed"]


def test_phase732_plugin_routes_and_ui_keep_broader_mutations_fail_closed() -> None:
    root = Path(__file__).parents[1] / "app"
    routes = (root / "codex_capability_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent_capabilities.html").read_text(encoding="utf-8")
    source = (root / "codex_capability_control.py").read_text(encoding="utf-8")

    assert '@router.post("/plugins/install"' in routes
    assert '@router.post("/plugins/uninstall"' in routes
    assert '@router.post("/plugins/mutate"' in routes
    assert 'action="/account/agent/capabilities/plugins/install"' in template
    assert 'action="/account/agent/capabilities/plugins/uninstall"' in template
    assert "marketplace/add/remove/upgrade" in template
    assert "plugin/share/*" in template
    assert "remoteMarketplaceName\": None" in source
