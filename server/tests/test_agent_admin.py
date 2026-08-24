from app.config import Settings


def test_agent_runtime_defaults_to_enabled_without_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.fdex_agent_enabled is True
    assert settings.fdex_agent_default_owner == "local"
    assert "agent-sandboxes" in settings.fdex_agent_sandbox_root
    assert settings.fdex_agent_sandbox_memory_mb == 2048
    assert settings.fdex_agent_sandbox_max_concurrent == 1


def test_agent_admin_template_exposes_runtime_projects_and_shared_ai() -> None:
    template = (Settings.model_config.get("env_file").parent / "app" / "templates" / "agent_settings.html").read_text(encoding="utf-8")
    assert 'name="fdex_agent_enabled"' in template
    assert "统一供应商模型池" in template
    assert "GitHub Connector" in template
    assert "Agent 项目" in template
    assert "执行沙箱资源" in template
    assert "account → project → task" in template
    assert 'name="repo_full_name"' in template
    assert 'name="allow_push"' in template
    assert 'name="allow_pr"' in template
