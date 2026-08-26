from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-12345")
os.environ.setdefault("ADMIN_SESSION_SECRET", "test-session-secret-that-is-longer-than-32-characters")
os.environ.setdefault("ADMIN_COOKIE_SECURE", "false")
os.environ.setdefault("APP_DIR", "/tmp/fdex-test-phase79")
os.environ.setdefault("SERVICE_NAME", "fdex-test")
os.environ.setdefault("RELEASE_CACHE_DIR", "/tmp/fdex-test-phase79/releases")

from app.github_app_admin_routes import _manifest, router as github_app_admin_router  # noqa: E402
from app.main import app  # noqa: E402
from app.user_agent_task_routes import router as user_agent_router  # noqa: E402
from app.user_app_routes import router as user_app_router  # noqa: E402
from app.web_workspace import WebWorkspaceStore  # noqa: E402


def _route_methods(router) -> set[tuple[str, tuple[str, ...]]]:
    return {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}


def test_web_workspace_is_hard_scoped_by_center_user_id(tmp_path: Path) -> None:
    store = WebWorkspaceStore(tmp_path / "web-workspace.db")
    owner_a = "usr_aaaaaaaaaaaaaaaaaaaaaaaa"
    owner_b = "usr_bbbbbbbbbbbbbbbbbbbbbbbb"

    a = store.create(owner_a, "employee", {"name": "A", "active": True}, sort_key="A")
    b = store.create(owner_b, "employee", {"name": "B", "active": True}, sort_key="B")

    assert a["id"] == 1
    assert b["id"] == 1
    assert [item["name"] for item in store.list(owner_a, "employee")] == ["A"]
    assert [item["name"] for item in store.list(owner_b, "employee")] == ["B"]

    store.create(owner_a, "message", {"employee_id": 1, "role": "user", "content": "only-a"}, parent_id=1)
    assert [item["content"] for item in store.list(owner_a, "message", parent_id=1)] == ["only-a"]
    assert store.list(owner_b, "message", parent_id=1) == []

    assert store.clear_owner(owner_a) == 2
    assert store.list(owner_a, "employee") == []
    assert [item["name"] for item in store.list(owner_b, "employee")] == ["B"]


def test_web_user_routes_cover_android_top_level_capabilities() -> None:
    methods = _route_methods(user_app_router)
    required_gets = {
        "/account/messages",
        "/account/employees",
        "/account/chat/employee/{employee_id}",
        "/account/groups",
        "/account/chat/group/{group_id}",
        "/account/knowledge",
        "/account/work",
        "/account/work/{project_id}",
        "/account/discover",
        "/account/me",
        "/account/settings",
        "/account/security",
        "/account/deleted",
        "/account/info/{slug}",
    }
    for path in required_gets:
        assert (path, ("GET",)) in methods

    required_posts = {
        "/account/employees",
        "/account/chat/employee/{employee_id}/send",
        "/account/groups",
        "/account/chat/group/{group_id}/send",
        "/account/knowledge",
        "/account/work",
        "/account/security/password",
        "/account/security/memory/clear",
        "/account/security/account/delete",
        "/account/deleted/restore",
    }
    for path in required_posts:
        assert (path, ("POST",)) in methods


def test_web_coding_agent_routes_support_create_run_cancel_retry() -> None:
    methods = _route_methods(user_agent_router)
    assert ("/account/agent", ("GET",)) in methods
    assert ("/account/agent/tasks", ("POST",)) in methods
    assert ("/account/agent/tasks/{task_id}", ("GET",)) in methods
    assert ("/account/agent/tasks/{task_id}/run", ("POST",)) in methods
    assert ("/account/agent/tasks/{task_id}/cancel", ("POST",)) in methods
    assert ("/account/agent/tasks/{task_id}/retry", ("POST",)) in methods
    assert ("/account/agent/sandbox/cleanup", ("POST",)) in methods


def test_main_app_registers_new_web_and_admin_routes() -> None:
    paths = {route.path for route in app.routes}
    assert "/account/messages" in paths
    assert "/account/agent" in paths
    assert "/account/security" in paths
    assert "/admin/github-app" in paths
    assert "/admin/github-app/manifest/start" in paths
    assert "/admin/github-app/manifest/callback" in paths


def test_github_app_manifest_is_multi_user_and_keeps_setup_callback() -> None:
    cfg = SimpleNamespace(public_base_url="https://fdex.k2n.cn")
    manifest = _manifest(cfg)
    assert manifest["public"] is True
    assert manifest["request_oauth_on_install"] is False
    assert manifest["setup_on_update"] is True
    assert manifest["url"] == "https://fdex.k2n.cn/account/github"
    assert manifest["setup_url"] == "https://fdex.k2n.cn/account/github/app/setup"
    assert manifest["callback_urls"] == ["https://fdex.k2n.cn/account/github/app/oauth/callback"]
    assert manifest["default_permissions"] == {
        "contents": "write",
        "pull_requests": "write",
        "metadata": "read",
    }
    assert str(manifest["name"]).startswith("FDEX-fdex-k2n-cn-")


def test_github_app_admin_and_user_ui_explain_real_install_state() -> None:
    root = Path(__file__).resolve().parents[2]
    user_template = (root / "server/app/templates/user_github.html").read_text(encoding="utf-8")
    admin_template = (root / "server/app/templates/github_app_settings.html").read_text(encoding="utf-8")
    base_template = (root / "server/app/templates/user_base.html").read_text(encoding="utf-8")

    assert "当前 GitHub App 安装功能尚不可用" in user_template
    assert "平台管理员需要先在 FDEX 服务端管理后台" in user_template
    assert "安装 / 连接 FDEX GitHub App" in user_template
    assert "https://github.com/settings/installations/{{ connection.github_app_installation_id }}" in user_template
    assert "在 GitHub 创建并初始化 FDEX GitHub App" in admin_template
    assert "/admin/github-app/manifest/start" in admin_template
    for label in ("消息", "知识库", "工作", "工作群", "Coding Agent", "GitHub", "发现", "我的"):
        assert label in base_template


def test_all_new_templates_compile() -> None:
    root = Path(__file__).resolve().parents[2]
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(root / "server/app/templates"), autoescape=True)
    for name in (
        "user_web_app.html",
        "user_agent.html",
        "github_app_settings.html",
        "github_app_manifest_post.html",
        "user_github.html",
        "user_base.html",
    ):
        assert env.get_template(name) is not None


def test_github_app_admin_routes_are_admin_only_namespace() -> None:
    methods = _route_methods(github_app_admin_router)
    assert ("/admin/github-app", ("GET",)) in methods
    assert ("/admin/github-app/manifest/start", ("POST",)) in methods
    assert ("/admin/github-app/manifest/callback", ("GET",)) in methods
    assert all(route.path.startswith("/admin/github-app") for route in github_app_admin_router.routes)
