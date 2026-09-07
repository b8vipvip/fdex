from __future__ import annotations

from pathlib import Path


def _app_root() -> Path:
    return Path(__file__).resolve().parents[1] / "app"


def test_codex_provider_rollout_admin_context_supplies_base_template_settings() -> None:
    source = (_app_root() / "codex_provider_admin_routes.py").read_text(encoding="utf-8")
    assert "from app.config import SERVER_DIR, fresh_settings" in source
    assert '"settings": fresh_settings()' in source
    assert '"codex_provider_rollout.html"' in source


def test_coding_agent_employee_turn_keeps_complete_codex_only_execution_chain() -> None:
    root = _app_root()
    employee_chat = (root / "employee_chat_runtime.py").read_text(encoding="utf-8")
    agent_loop = (root / "agent_loop.py").read_text(encoding="utf-8")
    rollout = (root / "codex_provider_rollout.py").read_text(encoding="utf-8")
    host = (root / "codex_host_runtime.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")

    # Coding-Agent-enabled employees enter the Agent runtime directly, not generic client_ai.
    assert "if bool(employee.get(\"coding_agent\"))" in employee_chat
    assert "return await _run_coding_agent" in employee_chat
    assert "await FdexAgentLoop(runtime).run(task.id)" in employee_chat

    # Every task preflights the rollout-backed Codex runtime status before launching the Host.
    assert "from app.codex_engine import codex_runtime_status" in agent_loop
    assert "from app.codex_host_entry import run_codex_task" in agent_loop
    assert 'if not bool(status.get("ready"))' in agent_loop
    assert "await run_codex_task(self.runtime, task_id)" in agent_loop

    # Provider selection remains fresh-full gated and is installed before requests are served.
    assert 'required_level="full"' in rollout
    assert "select_verified_codex_provider" in rollout
    assert "engine.select_codex_provider = select_verified_codex_provider" in rollout
    assert "install_codex_provider_rollout_runtime()" in main

    # The official Host still owns the native app-server Thread/Turn execution path.
    assert "thread/start" in host
    assert "turn/start" in host
    assert "select_codex_provider" in host
