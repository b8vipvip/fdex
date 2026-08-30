from __future__ import annotations

import pytest

from app import agent_admin_routes
from app import codex_capability_control
from app import codex_engine
from app import codex_host_runtime
from app import codex_provider_rollout as rollout
from app import codex_runtime_admin_routes


def test_rollout_installer_rebinds_every_preimported_codex_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gated_status() -> dict[str, object]:
        return {"ready": False, "reason": "fresh full compatibility required"}

    def verified_provider():
        return None

    old_status = lambda: {"ready": True}  # noqa: E731
    old_provider = lambda: object()  # noqa: E731

    monkeypatch.setattr(rollout, "_installed", False)
    monkeypatch.setattr(rollout, "codex_rollout_runtime_status", gated_status)
    monkeypatch.setattr(rollout, "select_verified_codex_provider", verified_provider)

    monkeypatch.setattr(codex_engine, "codex_runtime_status", old_status)
    monkeypatch.setattr(codex_engine, "select_codex_provider", old_provider)
    monkeypatch.setattr(agent_admin_routes, "codex_runtime_status", old_status)
    monkeypatch.setattr(codex_runtime_admin_routes, "codex_runtime_status", old_status)
    monkeypatch.setattr(codex_capability_control, "select_codex_provider", old_provider)
    monkeypatch.setattr(codex_host_runtime, "select_codex_provider", old_provider)

    rollout.install_codex_provider_rollout_runtime()

    assert codex_engine.codex_runtime_status is gated_status
    assert agent_admin_routes.codex_runtime_status is gated_status
    assert codex_runtime_admin_routes.codex_runtime_status is gated_status
    assert codex_engine.select_codex_provider is verified_provider
    assert codex_capability_control.select_codex_provider is verified_provider
    assert codex_host_runtime.select_codex_provider is verified_provider


def test_admin_engine_switch_source_uses_rebound_status_symbol() -> None:
    source = agent_admin_routes.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()

    assert "codex_status=codex_runtime_status()" in text
    assert "status = codex_runtime_status()" in text
    assert 'if requested_engine == "codex":' in text
