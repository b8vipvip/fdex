from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import codex_capability_control as control


MARKET = "/srv/fdex/plugins/local-marketplace.json"
PLUGIN_ID = "plugin.review"
PLUGIN_NAME = "review-plugin"


def _isolation_status(enforced: bool) -> dict[str, object]:
    return {
        "ready": enforced,
        "enforced": enforced,
        "required": True,
        "reason": "" if enforced else "systemd unavailable",
    }


def test_plugin_mutation_policy_remains_closed_even_when_cgroup_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control, "codex_process_isolation_status", lambda: _isolation_status(True))
    allowed, reason = control._plugin_mutation_policy()
    assert allowed is False
    assert "文件系统" in reason
    assert "cgroup" in reason


def test_install_fails_before_creating_host_even_with_cgroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control, "codex_process_isolation_status", lambda: _isolation_status(True))
    called: list[bool] = []

    async def forbidden_with_client(*args, **kwargs):
        called.append(True)
        raise AssertionError("Plugin mutation must fail before creating a Codex Host")

    monkeypatch.setattr(control, "_with_client", forbidden_with_client)
    with pytest.raises(control.CodexCapabilityError, match="plugin/install"):
        asyncio.run(
            control.install_local_plugin(
                "usr_phase732",
                marketplace_path=MARKET,
                plugin_name=PLUGIN_NAME,
            )
        )
    assert called == []


def test_uninstall_also_fails_before_host_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control, "codex_process_isolation_status", lambda: _isolation_status(True))
    called: list[bool] = []

    async def forbidden_with_client(*args, **kwargs):
        called.append(True)
        raise AssertionError("Plugin uninstall must fail before creating a Codex Host")

    monkeypatch.setattr(control, "_with_client", forbidden_with_client)
    with pytest.raises(control.CodexCapabilityError, match="plugin/uninstall"):
        asyncio.run(control.uninstall_plugin("usr_phase732", plugin_id=PLUGIN_ID))
    assert called == []


def test_missing_cgroup_is_reported_but_not_the_only_plugin_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control, "codex_process_isolation_status", lambda: _isolation_status(False))
    allowed, reason = control._plugin_mutation_policy()
    assert allowed is False
    assert "systemd unavailable" in reason
    assert "文件系统" in reason


def test_every_plugin_write_action_uses_same_fail_closed_policy() -> None:
    for action in (
        "plugin/install",
        "plugin/uninstall",
        "marketplace/add",
        "marketplace/remove",
        "marketplace/upgrade",
        "remote/plugin/install",
        "plugin/share/save",
    ):
        with pytest.raises(control.CodexCapabilityError, match="安全策略阻止"):
            control.assert_plugin_mutation_blocked(action)


def test_phase732_routes_do_not_call_plugin_install_or_uninstall_runtime() -> None:
    root = Path(__file__).parents[1] / "app"
    routes = (root / "codex_capability_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent_capabilities.html").read_text(encoding="utf-8")
    source = (root / "codex_capability_control.py").read_text(encoding="utf-8")

    # Compatibility POST routes remain so stale browser pages get an explicit policy response,
    # but the production route module must not import or call the mutation prototypes.
    assert '@router.post("/plugins/install"' in routes
    assert '@router.post("/plugins/uninstall"' in routes
    assert "install_local_plugin" not in routes
    assert "uninstall_plugin" not in routes
    assert 'action="/account/agent/capabilities/plugins/install"' not in template
    assert 'action="/account/agent/capabilities/plugins/uninstall"' not in template
    assert "写操作已锁定" in template
    assert "plugin/install、plugin/uninstall" in template
    assert "LocalStdioServerLauncher" not in source


def test_block_reason_explains_cgroup_is_not_filesystem_sandbox() -> None:
    reason = control.PLUGIN_MUTATION_BLOCK_REASON
    assert "CPU" in reason
    assert "内存" in reason
    assert "PID" in reason
    assert "文件系统执行沙箱" in reason
    assert "bundled Codex 0.147" in reason
