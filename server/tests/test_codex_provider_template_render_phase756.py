"""Regression coverage for admin Jinja compilation and the Codex rollout GET route.

The CI suite previously checked that timeout copy existed in this template,
but never compiled or rendered it, allowing a malformed {% endif %> to ship.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from jinja2 import TemplateSyntaxError
from starlette.requests import Request

from app import codex_provider_admin_routes as routes
from app.config import SERVER_DIR


TEMPLATE_ROOT = SERVER_DIR / "app" / "templates"


def test_every_admin_template_parses_as_jinja() -> None:
    """Fail CI on malformed Jinja tags even in rarely visited admin pages."""
    paths = sorted(TEMPLATE_ROOT.rglob("*.html"))
    assert paths, "No HTML templates found"
    for path in paths:
        try:
            routes.templates.env.parse(path.read_text(encoding="utf-8"))
        except TemplateSyntaxError as exc:
            pytest.fail(f"Invalid Jinja template {path.relative_to(TEMPLATE_ROOT)}:{exc.lineno}: {exc.message}")


def _context(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": SimpleNamespace(
            environment="test",
            app_version="test",
            fdex_codex_smoke_turn_idle_timeout_seconds=1800,
            fdex_codex_smoke_turn_max_seconds=3600,
        ),
        "csrf_token": "test-csrf",
        "flash": None,
        "current_path": request.url.path,
        **extra,
    }


def _rollout(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "runtime": {"version": "1.0", "source": "test", "path": "/tmp/codex"},
        "runtime_error": None,
        "max_age_hours": 24,
        "required_level": "full",
        "rows": rows,
        "smoke_active": False,
    }


def _provider_row(*, status: str = "idle", report: bool = False) -> dict[str, object]:
    run: dict[str, object] = {}
    if status == "running":
        run = {
            "active": True,
            "started_at": "2026-10-18T04:00:00+00:00",
            "elapsed_seconds": 151,
            "detail": "processing",
            "runtime_version": "1.0",
            "run_id": "smoke-run",
        }
    elif status == "failed":
        run = {
            "active": False,
            "status": "failed",
            "finished_at": "2026-10-18T04:05:00+00:00",
            "started_at": "2026-10-18T04:00:00+00:00",
            "runtime_version": "1.0",
            "error": "test failure",
            "run_id": "smoke-run",
        }
    return {
        "provider": {
            "id": 1,
            "priority": 1,
            "enabled": True,
            "name": "test-provider",
            "main_text_model": "test-model",
            "base_url": "https://example.invalid/v1",
            "protocol_order": ["responses"],
            "api_key_masked": "***",
        },
        "compatibility": {"valid": False, "level": "none", "reason": "unverified", "record": {}},
        "smoke_run": run,
        "smoke_report_available": report,
        "spec": True,
    }


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "暂无供应商"),
        ([_provider_row()], "执行 full smoke"),
        ([_provider_row(status="running", report=True)], "下载诊断报告"),
        ([_provider_row(status="failed")], "本次失败"),
    ],
)
def test_codex_provider_rollout_get_renders_200(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
    expected: str,
) -> None:
    """Exercise the real FastAPI handler and the inherited Jinja template."""
    request = Request({
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "server": ("example.invalid", 443),
        "path": "/admin/agent/codex-providers",
        "raw_path": b"/admin/agent/codex-providers",
        "query_string": b"",
        "headers": [],
    })
    monkeypatch.setattr(routes, "is_admin", lambda _request: True)
    monkeypatch.setattr(routes, "_ctx", _context)
    monkeypatch.setattr(routes, "_rollout_with_smoke_runs", lambda: _rollout(rows))
    monkeypatch.setattr(
        routes, "codex_process_isolation_status",
        lambda: {"enforced": True, "memory_mb": 512, "cpu_percent": 50, "pids_max": 100},
    )
    response = routes.codex_provider_rollout_page(request)
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Codex Provider Rollout" in html
    assert expected in html
    if rows:
        assert "1800 秒无进展" in html
        assert "3600 秒" in html
