from __future__ import annotations

from pathlib import Path

import pytest

from app.codex_dynamic_tool_policy import (
    DynamicToolPolicyError,
    assert_dynamic_tool_activation_blocked,
    dynamic_tool_policy,
)


def test_dynamic_tools_are_explicitly_locked_for_phase730() -> None:
    policy = dynamic_tool_policy()
    assert policy["allowed"] is False
    assert policy["runtime_fallback"] == "rust-v0.147.0"
    assert policy["activation_field"] == "thread/start.dynamicTools"
    assert "Phase 7.32" in str(policy["reason"])
    with pytest.raises(DynamicToolPolicyError, match="Dynamic Tool"):
        assert_dynamic_tool_activation_blocked()


def test_fdex_thread_start_does_not_inject_dynamic_tools_into_fallback() -> None:
    source = (Path(__file__).parents[1] / "app" / "codex_host_runtime.py").read_text(encoding="utf-8")
    thread_param_section = source.split("def _thread_common_params", 1)[1].split("def turn_start_params", 1)[0]
    assert '"dynamicTools"' not in thread_param_section
    assert '"dynamic_tools"' not in thread_param_section


def test_capability_ui_surfaces_dynamic_tool_lock() -> None:
    root = Path(__file__).parents[1] / "app"
    routes = (root / "codex_capability_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "user_agent_capabilities.html").read_text(encoding="utf-8")
    assert "dynamic_tool_policy()" in routes
    assert "Dynamic Tools" in template
    assert "thread/start.dynamicTools" in template
    assert "已锁定" in template
