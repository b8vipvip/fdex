from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import codex_provider_admin_routes as routes
from app import codex_provider_smoke_report as reports
from app.codex_provider_smoke import _run_turn


def _report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, api_key: str = "") -> reports.CodexSmokeReport:
    monkeypatch.setattr(reports, "REPORT_ROOT", tmp_path / "reports")
    return reports.CodexSmokeReport(7, uuid.uuid4().hex, api_key=api_key)


def test_smoke_report_is_persistent_bounded_and_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    report = _report(monkeypatch, tmp_path, api_key="provider-secret-123")
    report.write(
        "smoke_error",
        stage="wire",
        error="HTTP 401 provider-secret-123 Authorization: Bearer abc123 sk-1234567890",
        prompt="Never store prompt text",
        api_key="Never store key",
        event_types=["reasoning", "agentMessage"],
    )
    raw = reports.CodexSmokeReport(7, report.run_id).read()
    assert "provider-secret-123" not in raw
    assert "abc123" not in raw
    assert "sk-1234567890" not in raw
    assert "Never store" not in raw
    parsed = json.loads(raw)
    assert parsed["event"] == "smoke_error"
    assert parsed["event_types"] == ["reasoning", "agentMessage"]
    assert parsed["stage"] == "wire"
    assert report.path.parent.name == "7"


def test_smoke_report_rejects_path_traversal_and_wrong_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    report = _report(monkeypatch, tmp_path)
    report.write("smoke_queued")
    for value in ("../../etc/passwd", report.run_id + "/foo", "not-a-uuid"):
        with pytest.raises(ValueError):
            reports.CodexSmokeReport(7, value)
    with pytest.raises(ValueError):
        reports.CodexSmokeReport(-1, report.run_id)
    assert not reports.CodexSmokeReport(8, report.run_id).exists()


def test_download_route_requires_admin_and_returns_only_selected_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    report = _report(monkeypatch, tmp_path)
    report.write("smoke_queued", model="gpt-6")
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, follow_redirects=False)
    url = f"/admin/agent/codex-providers/7/smoke/{report.run_id}/report"

    monkeypatch.setattr(routes, "is_admin", lambda request: False)
    unauthenticated = client.get(url)
    assert unauthenticated.status_code == 303
    assert unauthenticated.headers["location"] == "/admin/login"

    monkeypatch.setattr(routes, "is_admin", lambda request: True)
    downloaded = client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-disposition"].startswith("attachment; filename=")
    assert downloaded.headers["cache-control"] == "no-store"
    assert json.loads(downloaded.text)["event"] == "smoke_queued"
    assert client.get(url.replace("/7/", "/8/")).status_code == 404
    assert client.get(url.replace(report.run_id, uuid.uuid4().hex)).status_code == 404
    assert client.get(url.replace(report.run_id, "invalid-id")).status_code == 404


def test_real_turn_event_metadata_is_logged_without_prompt_or_response_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    report = _report(monkeypatch, tmp_path)

    class FakeAppServer:
        def __init__(self) -> None:
            self.events = iter([
                ("item/started", {"turnId": "turn-1", "item": {"type": "reasoning", "status": "inProgress"}}),
                ("item/agentMessage/delta", {"turnId": "turn-1", "delta": "private output"}),
                ("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}),
            ])

        async def request(self, method: str, payload: object, timeout: float) -> dict:
            assert method == "turn/start"
            return {"turn": {"id": "turn-1"}}

        async def next_notification(self, *, timeout: float) -> tuple[str, dict]:
            return next(self.events)

    result = asyncio.run(
        _run_turn(FakeAppServer(), "thread-1", "private prompt", timeout=20.0, stage="wire", report=report)
    )
    assert result["text"] == "private output"
    lines = [json.loads(line) for line in report.read().splitlines()]
    assert [line["event"] for line in lines] == [
        "stage_begin", "turn_started", "item_event", "first_output_delta", "turn_completed"
    ]
    raw = report.read()
    assert "private prompt" not in raw
    assert "private output" not in raw
    assert lines[-1]["delta_chars"] == len("private output")


def test_rollout_template_exposes_report_link_only_when_file_exists() -> None:
    template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "codex_provider_rollout.html").read_text(encoding="utf-8")
    assert "row.smoke_report_available" in template
    assert "run.run_id" in template
    assert "下载诊断报告" in template
    assert "旧版测试未保存诊断报告" in template
