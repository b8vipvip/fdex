from __future__ import annotations

from pathlib import Path

from app.user_chat_api_routes import router as chat_router


def test_async_employee_chat_route_exists() -> None:
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in chat_router.routes}
    assert ("/account/chat/employee/{employee_id}/send-json", ("POST",)) in routes


def test_main_installs_protocol_runtime_before_web_app_import() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "server/app/main.py").read_text(encoding="utf-8")
    install_at = source.index("install_provider_protocol_runtime()")
    user_app_import_at = source.index("from app.user_app_routes")
    assert install_at < user_app_import_at
    assert "app.include_router(user_chat_api_router)" in source


def test_user_portal_loads_async_chat_script() -> None:
    root = Path(__file__).resolve().parents[2]
    base = (root / "server/app/templates/user_base.html").read_text(encoding="utf-8")
    script = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")
    assert '/static/user_chat.js' in base
    assert "/send-json" in script
    assert "event.preventDefault()" in script
    assert "正在连接 FDEX AI 线路" in script
