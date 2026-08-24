from app.config import Settings
from app.user_admin_routes import _format_bytes


def test_user_admin_template_exposes_account_controls_without_tokens() -> None:
    template = (Settings.model_config.get("env_file").parent / "app" / "templates" / "users.html").read_text(encoding="utf-8")
    base = (Settings.model_config.get("env_file").parent / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert "FDEX 用户管理" in template
    assert "注销全部设备" in template
    assert "禁用" in template
    assert "恢复" in template
    assert "GitHub 连接" in template
    assert "Agent 项目" in template
    assert "沙箱磁盘" in template
    assert "Access/Refresh Token 的哈希" in template
    assert "access_hash" not in template
    assert "refresh_hash" not in template
    assert 'href="/admin/users"' in base


def test_user_admin_formats_sandbox_usage() -> None:
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(1024) == "1.0 KB"
    assert _format_bytes(12 * 1024 * 1024) == "12 MB"
