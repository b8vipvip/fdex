from __future__ import annotations

from pathlib import Path

from app import codex_engine
from app.config import Settings


def _provider(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 7,
        "name": "Primary",
        "base_url": "https://example.invalid/v1",
        "api_key": "super-secret-provider-key",
        "enabled": True,
        "priority": 1,
        "protocol_order": ["responses", "chat"],
        "main_text_model": "gpt-5.6-sol",
        "backup_text_models": [],
    }
    value.update(overrides)
    return value


def test_codex_provider_selection_requires_responses_key_and_model() -> None:
    chat_only = _provider(id=1, protocol_order=["chat"])
    no_key = _provider(id=2, api_key="")
    no_model = _provider(id=3, main_text_model="")
    good = _provider(id=4, name="Responses")

    selected = codex_engine.select_codex_provider_from([chat_only, no_key, no_model, good])

    assert selected is not None
    assert selected.provider_id == 4
    assert selected.name == "Responses"
    assert selected.model == "gpt-5.6-sol"
    assert selected.base_url == "https://example.invalid/v1"


def test_codex_provider_override_never_contains_api_key() -> None:
    selected = codex_engine.select_codex_provider_from([_provider()])
    assert selected is not None

    override = codex_engine._provider_override(selected)

    assert "super-secret-provider-key" not in override
    assert "FDEX_CODEX_PROVIDER_KEY" in override
    assert 'wire_api = "responses"' in override


def test_codex_shell_environment_does_not_expose_provider_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FDEX_CODEX_PROVIDER_KEY", "must-not-reach-shell")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-reach-shell-either")
    policy = codex_engine._shell_environment_policy(tmp_path)

    assert policy["inherit"] == "none"
    configured = policy["set"]
    assert isinstance(configured, dict)
    assert "FDEX_CODEX_PROVIDER_KEY" not in configured
    assert "GITHUB_TOKEN" not in configured
    assert configured["HOME"] == str(tmp_path)


def test_codex_thread_config_preserves_fdex_network_policy(tmp_path: Path) -> None:
    blocked = codex_engine._codex_thread_config(tmp_path, allow_network=False)
    allowed = codex_engine._codex_thread_config(tmp_path, allow_network=True)

    assert blocked["sandbox_workspace_write"] == {"network_access": False}
    assert allowed["sandbox_workspace_write"] == {"network_access": True}
    assert blocked["web_search"] == "disabled"
    assert allowed["web_search"] == "disabled"


def test_codex_process_env_only_adds_provider_key_to_sanitized_wrapper_input(tmp_path: Path) -> None:
    env = codex_engine._safe_process_env(tmp_path, "provider-key")
    assert env["FDEX_CODEX_PROVIDER_KEY"] == "provider-key"
    assert env["CODEX_HOME"] == str(tmp_path)
    assert "GITHUB_TOKEN" not in env
    assert "FDEX_GITHUB_APP_CLIENT_SECRET" not in env


def test_protected_paths_are_rejected_before_fdex_commit() -> None:
    assert codex_engine._path_is_protected(".env")
    assert codex_engine._path_is_protected(".env.production")
    assert codex_engine._path_is_protected("server/data/private.json")
    assert codex_engine._path_is_protected(".git/config")
    assert not codex_engine._path_is_protected(".env.example")
    assert not codex_engine._path_is_protected("server/app/main.py")


def test_agent_settings_no_longer_define_or_normalize_engine_modes(monkeypatch) -> None:
    monkeypatch.setenv("FDEX_AGENT_ENGINE", "legacy")
    monkeypatch.setenv("FDEX_AGENT_MAX_STEPS", "29")
    monkeypatch.setenv("FDEX_AGENT_MODEL_MAX_TOKENS", "3999")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "fdex_agent_engine")
    assert not hasattr(settings, "fdex_agent_max_steps")
    assert not hasattr(settings, "fdex_agent_model_max_tokens")
    assert not hasattr(codex_engine, "normalize_engine_mode")


def test_admin_template_exposes_codex_as_the_only_agent_core() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/agent_settings.html").read_text(encoding="utf-8")
    assert "唯一执行核心：OpenAI Codex Core" in template
    assert 'name="fdex_agent_engine"' not in template
    assert "codex_status.ready" in template
    assert "FDEX_CODEX_PROVIDER_KEY" not in template
