from __future__ import annotations

import json
from pathlib import Path

import app.update_monitor as monitor
from app.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(app_dir=str(tmp_path))


def test_update_status_reads_persistent_progress(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    path = tmp_path / "server" / "data" / "admin-update-status.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "dependencies",
                "percent": 58,
                "message": "正在安装和更新后端依赖",
                "started_at": "2026-08-24T01:00:00+00:00",
                "updated_at": "2026-08-24T01:01:00+00:00",
                "completed_at": "",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor, "_latest_update_unit", lambda: "fdex-admin-update-123.service")
    monkeypatch.setattr(
        monitor,
        "_unit_properties",
        lambda unit: {"ActiveState": "active", "Result": "success", "ExecMainStatus": "0"},
    )
    monkeypatch.setattr(monitor, "_unit_logs", lambda unit: ["pip install...", "still running"])

    result = monitor.update_task_status(settings)

    assert result["status"] == "running"
    assert result["stage"] == "dependencies"
    assert result["percent"] == 58
    assert result["unit"] == "fdex-admin-update-123.service"
    assert result["logs"][-1] == "still running"


def test_systemd_failure_overrides_stale_running_state(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    path = tmp_path / "server" / "data" / "admin-update-status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "running", "stage": "health", "percent": 96}), encoding="utf-8")
    monkeypatch.setattr(monitor, "_latest_update_unit", lambda: "fdex-admin-update-456.service")
    monkeypatch.setattr(
        monitor,
        "_unit_properties",
        lambda unit: {"ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "1"},
    )
    monkeypatch.setattr(monitor, "_unit_logs", lambda unit: ["health check failed"])

    result = monitor.update_task_status(settings)

    assert result["status"] == "failed"
    assert result["exit_code"] == "1"
    assert "health check failed" in result["logs"]


def test_update_script_uses_structured_progress_and_server_requirements() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "update_server.sh"
    text = script.read_text(encoding="utf-8")
    assert "FDEX_UPDATE_STAGE|" in text
    assert 'update_progress "running" "dependencies" "58"' in text
    assert '"${APP_DIR}/server/requirements.txt"' in text
    assert 'update_progress "succeeded" "completed" "100"' in text
