from pathlib import Path

from app.codex_provider_admin_routes import _admin_time
from app.codex_provider_smoke_runs import CodexProviderSmokeRunStore


ROOT = Path(__file__).resolve().parents[1]


def test_smoke_run_state_is_durable_and_separate_from_compatibility(tmp_path: Path) -> None:
    store = CodexProviderSmokeRunStore(tmp_path / "compat.db")
    started = store.begin(
        7,
        runtime_version="0.149.0",
        runtime_source="managed",
        model="gpt-5.6-sol",
    )
    assert started["status"] == "queued"
    assert started["active"] is True
    assert started["run_id"]

    running = store.update(
        7,
        started["run_id"],
        status="running",
        stage="full-smoke",
        detail="wire → tools → MCP → subagent",
    )
    assert running is not None
    assert running["status"] == "running"
    assert running["active"] is True
    assert "tools" in running["detail"]

    finished = store.finish(
        7,
        started["run_id"],
        status="failed",
        stage="wire",
        detail="full smoke ended",
        error="tool turn did not create the required scratch file with exact content",
    )
    assert finished is not None
    assert finished["active"] is False
    assert finished["status"] == "failed"
    assert finished["finished_at"]
    assert "scratch file" in finished["error"]


def test_duplicate_active_smoke_is_rejected(tmp_path: Path) -> None:
    store = CodexProviderSmokeRunStore(tmp_path / "compat.db")
    store.begin(
        9,
        runtime_version="0.149.0",
        runtime_source="managed",
        model="gpt-5.6-sol",
    )
    try:
        store.begin(
            9,
            runtime_version="0.149.0",
            runtime_source="managed",
            model="gpt-5.6-sol",
        )
    except RuntimeError as exc:
        assert "已有正在执行" in str(exc)
    else:
        raise AssertionError("duplicate active smoke must be rejected")


def test_smoke_timestamp_display_converts_persisted_utc_to_beijing_time() -> None:
    assert _admin_time("2026-09-09T11:48:30+00:00") == "2026-09-09 19:48:30"
    assert _admin_time("2026-09-09T11:44:49+00:00") == "2026-09-09 19:44:49"


def test_rollout_page_surfaces_current_run_and_auto_refreshes() -> None:
    route = (ROOT / "app" / "codex_provider_admin_routes.py").read_text(encoding="utf-8")
    template = (ROOT / "app" / "templates" / "codex_provider_rollout.html").read_text(encoding="utf-8")
    assert "codex_provider_smoke_run_store().begin" in route
    assert "_smoke_heartbeat" in route
    assert 'templates.env.filters["admin_time"] = _admin_time' in route
    assert 'ZoneInfo("Asia/Shanghai")' in route
    assert '本次测试 / 最后完成（北京时间）' in template
    assert "run.started_at|admin_time" in template
    assert "run.finished_at|admin_time" in template
    assert "record.checked_at|admin_time" in template
    assert "已用时 {{ run.elapsed_seconds }} 秒" in template
    assert "window.location.reload()" in template
    assert "run.active" in template
