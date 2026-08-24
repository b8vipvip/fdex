from app.config import Settings


def test_agent_runtime_defaults_to_enabled_without_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.fdex_agent_enabled is True


def test_agent_admin_template_exposes_runtime_toggle() -> None:
    template = (Settings.model_config.get("env_file").parent / "app" / "templates" / "agent_settings.html").read_text(encoding="utf-8")
    assert 'name="fdex_agent_enabled"' in template
    assert "保存并自动重启服务" in template
    assert "FDEX_AGENT_ENABLED" in template
