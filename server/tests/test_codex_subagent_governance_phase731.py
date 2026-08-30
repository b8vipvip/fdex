from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.codex_env_wrapper import _inject_governance_args
from app.codex_subagent_governance import (
    CodexSubAgentSettings,
    codex_subagent_cli_overrides,
    codex_subagent_policy,
)


def _settings(**overrides: object) -> CodexSubAgentSettings:
    values: dict[str, object] = {
        "fdex_agent_subagents_enabled": True,
        "fdex_agent_subagent_max_concurrent": 4,
        "fdex_agent_subagent_rollout_budget_tokens": 80_000,
        "fdex_agent_subagent_wait_min_ms": 1_000,
        "fdex_agent_subagent_wait_default_ms": 15_000,
        "fdex_agent_subagent_wait_max_ms": 60_000,
        "fdex_agent_subagent_sampling_token_weight": 1.0,
        "fdex_agent_subagent_prefill_token_weight": 0.25,
    }
    values.update(overrides)
    return CodexSubAgentSettings(_env_file=None, **values)


def test_multi_agent_v2_policy_uses_official_limits_and_shared_budget() -> None:
    policy = codex_subagent_policy(_settings())
    assert policy["enabled"] is True
    assert policy["protocol"] == "official-codex-multi-agent-v2"
    assert policy["tool_namespace"] == "collaboration"
    assert policy["max_concurrent_threads_per_session"] == 4
    assert policy["max_parallel_subagents"] == 3
    assert policy["rollout_budget_tokens"] == 80_000
    assert policy["rollout_budget_reminders"] == [20_000, 8_000, 4_000]
    assert policy["spawn_model_overrides"] is False
    assert policy["hard_v2_depth_limit_available"] is False


def test_cli_overrides_enable_official_collab_v2_and_rollout_budget() -> None:
    overrides = codex_subagent_cli_overrides(_settings())
    assert overrides[0] == "features.collab=true"
    assert "features.multi_agent_v2={" in overrides[1]
    assert "max_concurrent_threads_per_session = 4" in overrides[1]
    assert 'tool_namespace = "collaboration"' in overrides[1]
    assert "expose_spawn_agent_model_overrides = false" in overrides[1]
    assert "features.rollout_budget={" in overrides[2]
    assert "limit_tokens = 80000" in overrides[2]
    assert "reminder_at_remaining_tokens = [20000, 8000, 4000]" in overrides[2]
    assert "sampling_token_weight = 1" in overrides[2]
    assert "prefill_token_weight = 0.25" in overrides[2]
    joined = "\n".join(overrides).lower()
    assert "api_key" not in joined
    assert "model =" not in joined
    assert "base_url" not in joined


def test_disabled_policy_explicitly_disables_all_three_official_features() -> None:
    assert codex_subagent_cli_overrides(
        _settings(fdex_agent_subagents_enabled=False)
    ) == (
        "features.collab=false",
        "features.multi_agent_v2=false",
        "features.rollout_budget=false",
    )


def test_wait_bounds_fail_closed() -> None:
    with pytest.raises(ValidationError, match="min <= default <= max"):
        _settings(
            fdex_agent_subagent_wait_min_ms=30_000,
            fdex_agent_subagent_wait_default_ms=10_000,
            fdex_agent_subagent_wait_max_ms=60_000,
        )


def test_wrapper_injects_governance_before_app_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.codex_subagent_governance as governance

    monkeypatch.setattr(
        governance,
        "codex_subagent_cli_overrides",
        lambda: ("features.collab=true", "features.multi_agent_v2={ enabled = true }"),
    )
    args = ["--config", "model_providers.fdex={ name=\"FDEX\" }", "app-server", "--listen", "stdio://"]
    result = _inject_governance_args(args)
    app_server_index = result.index("app-server")
    assert result[:2] == args[:2]
    assert result[2:6] == [
        "--config",
        "features.collab=true",
        "--config",
        "features.multi_agent_v2={ enabled = true }",
    ]
    assert app_server_index == 6
    assert result[app_server_index:] == ["app-server", "--listen", "stdio://"]


def test_wrapper_does_not_rewrite_non_app_server_codex_commands() -> None:
    args = ["--version"]
    assert _inject_governance_args(args) == args


def test_wrapper_loads_policy_from_arbitrary_tenant_cwd(tmp_path: Path) -> None:
    """Production starts the trusted wrapper with cwd set to the user's task worktree.

    Execute the wrapper as a standalone script from an unrelated cwd so importing the FDEX policy
    cannot accidentally depend on pytest/PYTHONPATH. A tiny fake Codex executable echoes the final
    argv after the wrapper has scrubbed the environment.
    """
    wrapper = Path(__file__).parents[1] / "app" / "codex_env_wrapper.py"
    fake = tmp_path / "fake-codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(wrapper), str(fake), "app-server", "--help"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    final_args = json.loads(result.stdout.strip())
    assert "app-server" in final_args
    assert "features.collab=true" in final_args
    assert any(str(value).startswith("features.multi_agent_v2={") for value in final_args)
    assert any(str(value).startswith("features.rollout_budget={") for value in final_args)


def test_bundled_codex_0147_parses_phase731_overrides() -> None:
    """Do not claim fallback compatibility from string inspection alone.

    CI installs openai-codex-cli-bin==0.147.0. Ask that exact official runtime to parse the
    generated config while rendering app-server help; this is offline and starts no task.
    """
    from codex_cli_bin import bundled_codex_path

    command = [str(bundled_codex_path())]
    for override in codex_subagent_cli_overrides(_settings()):
        command.extend(("--config", override))
    command.extend(("app-server", "--help"))
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert "app-server" in combined or "stdio" in combined


def test_phase731_admin_route_is_registered_and_policy_is_not_tenant_owned() -> None:
    root = Path(__file__).parents[1] / "app"
    parent = (root / "agent_admin_routes.py").read_text(encoding="utf-8")
    admin = (root / "codex_subagent_admin_routes.py").read_text(encoding="utf-8")
    wrapper = (root / "codex_env_wrapper.py").read_text(encoding="utf-8")
    template = (root / "templates" / "agent_subagent_settings.html").read_text(encoding="utf-8")
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")

    assert "router.include_router(codex_subagent_admin_router)" in parent
    assert 'prefix="/subagents"' in admin
    assert "write_env(" in admin
    assert "FDEX_AGENT_SUBAGENT_MAX_CONCURRENT" in admin
    assert "FDEX_AGENT_SUBAGENT_ROLLOUT_BUDGET_TOKENS" in admin
    assert "_inject_governance_args" in wrapper
    assert "os.execve" in wrapper
    assert "官方 Codex Multi-Agent V2" in template
    assert "模型覆盖被强制关闭" in template
    assert 'href="/admin/agent/subagents"' in base


def test_phase722_schema_light_event_store_can_represent_collaboration_items() -> None:
    # Phase 7.31 deliberately reuses the schema-light Phase 7.22 item/event projection. The
    # persistence code keys on item id/type rather than a whitelist, so official collaboration
    # item variants remain durable without creating a parallel FDEX sub-agent event table.
    source = (Path(__file__).parents[1] / "app" / "codex_item_store.py").read_text(encoding="utf-8")
    assert 'item_type = _text(item_dict.get("type"), 120) or "unknown"' in source
    assert 'method in {"item/started", "item/completed"}' in source
    assert "collabAgentToolCall" not in source  # no brittle per-version allowlist
