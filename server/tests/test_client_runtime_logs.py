from __future__ import annotations

from pathlib import Path

from app.client_runtime_logs import ClientRuntimeLogStore, redact_text


def test_client_log_store_redacts_secrets_and_filters(tmp_path: Path) -> None:
    store = ClientRuntimeLogStore(tmp_path / "client-logs.sqlite3")
    accepted = store.append_batch(
        owner_id="usr_test",
        session_id="ses_test",
        device_name="Android Pixel",
        platform="android",
        app_version="1.2.3",
        git_sha="abcdef1",
        os_version="Android 15",
        entries=[
            {
                "time": "2026-09-02T12:00:00Z",
                "level": "error",
                "component": "client_ai",
                "event": "http_error",
                "message": "authorization: Bearer super-secret-token-value",
                "details": {"http_code": 500, "access_token": "must-not-survive"},
            },
            {
                "time": "2026-09-02T12:00:01Z",
                "level": "info",
                "component": "app",
                "event": "foreground",
                "message": "ready",
                "details": {},
            },
        ],
    )

    assert accepted == 2
    rows = store.list(owner_id="usr_test", platform="android", level="error", component="client_ai", limit=10)
    assert len(rows) == 1
    assert "super-secret-token-value" not in rows[0]["message"]
    assert "[REDACTED]" in rows[0]["message"]
    assert rows[0]["details"]["access_token"] == "[REDACTED]"
    assert rows[0]["details"]["http_code"] == 500


def test_client_log_store_separates_android_and_web(tmp_path: Path) -> None:
    store = ClientRuntimeLogStore(tmp_path / "client-logs.sqlite3")
    for platform, device in (("android", "Android Pixel"), ("web", "Web Win32")):
        store.append_batch(
            owner_id="usr_test",
            session_id=f"ses_{platform}",
            device_name=device,
            platform=platform,
            app_version="1.2.3",
            git_sha="",
            os_version=platform,
            entries=[{
                "time": "2026-09-03T00:00:00Z",
                "level": "info",
                "component": f"{platform}_runtime",
                "event": "page_or_app_load",
                "message": "ready",
                "details": {},
            }],
        )

    web = store.list(owner_id="usr_test", platform="web", limit=10)
    android = store.list(owner_id="usr_test", platform="android", limit=10)
    assert len(web) == 1 and web[0]["device_name"] == "Web Win32"
    assert len(android) == 1 and android[0]["device_name"] == "Android Pixel"
    assert set(store.platforms()) == {"android", "web"}


def test_redact_text_covers_common_credentials() -> None:
    safe = redact_text("password=hunter2 api_key=sk-secret refresh_token=refresh-secret")
    assert "hunter2" not in safe
    assert "sk-secret" not in safe
    assert "refresh-secret" not in safe
    assert safe.count("[REDACTED]") == 3


def test_client_log_routes_and_admin_navigation_are_wired() -> None:
    root = Path(__file__).resolve().parents[2]
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    base = (root / "server/app/templates/base.html").read_text(encoding="utf-8")
    user_base = (root / "server/app/templates/user_base.html").read_text(encoding="utf-8")
    page = (root / "server/app/templates/client_logs.html").read_text(encoding="utf-8")
    routes = (root / "server/app/client_runtime_log_routes.py").read_text(encoding="utf-8")
    web_runtime = (root / "server/app/static/user_runtime_log.js").read_text(encoding="utf-8")
    web_chat = (root / "server/app/static/user_chat.js").read_text(encoding="utf-8")

    assert "client_runtime_log_router" in main
    assert "client_log_admin_router" in main
    assert "/admin/client-logs" in base
    assert "客户端日志" in base
    assert "导出 LOG" in page
    assert "导出 JSON" in page
    assert 'name="platform"' in page
    assert "Android 与 Web" in page

    assert '/static/user_runtime_log.js' in user_base
    assert 'data-fdex-log-scope' in user_base
    assert '@router.post("/web-batch"' in routes
    assert 'force_platform="web"' in routes
    assert "X-FDEX-CSRF-Token" in web_runtime
    assert "localStorage" in web_runtime
    assert "unhandledrejection" in web_runtime
    assert "resource_load_error" in web_runtime
    assert "fetch_complete" in web_runtime
    assert "message_chars" in web_chat
    assert "response_chars" in web_chat
    assert "assistant_message?.content" not in web_runtime
