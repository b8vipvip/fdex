from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bundled_codex_fallback_is_newer_than_0147() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "openai-codex-cli-bin==0.149.0" in requirements
    assert "openai-codex-cli-bin==0.147.0" not in requirements


def test_smoke_route_guards_known_gpt56_code_mode_regression() -> None:
    source = (ROOT / "app" / "codex_provider_admin_routes.py").read_text(encoding="utf-8")
    assert 'startswith("gpt-5.6")' in source
    assert 'runtime.version.strip() == "0.147.0"' in source
    assert "custom_tool_call" in source
    assert "Codex Runtime 管理页升级" in source
    assert "logger.exception" in source
