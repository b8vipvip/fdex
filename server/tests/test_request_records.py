from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.client_log_admin_routes as client_log_admin_routes
import app.request_trace as request_trace_module
from app.main import app
from app.request_records import RequestRecordStore


def test_request_record_store_groups_events_and_redacts_secrets(tmp_path: Path) -> None:
    store = RequestRecordStore(tmp_path / "request-records.sqlite3")
    request_id = "request-record-test-1"
    store.record_event(
        {
            "component": "http",
            "event": "http_request_begin",
            "request_id": request_id,
            "method": "POST",
            "path": "/api/client/ai",
            "client": "127.0.0.1",
            "mode": "sync",
            "content_type": "application/json",
            "content_length": "42",
            "access_token": "should-never-be-persisted",
        }
    )
    store.record_event(
        {
            "component": "client_ai",
            "event": "provider_attempt",
            "request_id": request_id,
            "provider": "fake-provider",
        }
    )
    store.record_event(
        {
            "component": "http",
            "event": "http_request_end",
            "request_id": request_id,
            "status_code": 200,
            "elapsed_ms": 123,
        }
    )

    rows = store.list(status="success", query="request-record-test", limit=10)
    assert len(rows) == 1
    assert rows[0]["request_id"] == request_id
    assert rows[0]["status_code"] == 200
    assert rows[0]["elapsed_ms"] == 123
    assert rows[0]["event_count"] == 3

    record = store.get(request_id)
    assert record is not None
    assert len(record["events"]) == 3
    assert record["events"][0]["payload"]["access_token"] == "[REDACTED]"


def test_public_api_request_gets_request_id_and_trace_events(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    class CaptureStore:
        def record_event(self, payload, *, level="info"):
            events.append({"payload": payload, "level": level})

    monkeypatch.setattr(request_trace_module, "request_record_store", lambda: CaptureStore())
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/api/health", headers={"X-FDEX-Request-ID": "health-trace-1"})

    assert response.status_code == 200
    assert response.headers["x-fdex-request-id"] == "health-trace-1"
    assert [item["payload"]["event"] for item in events] == ["http_request_begin", "http_request_end"]
    assert events[0]["payload"]["path"] == "/api/health"
    assert events[1]["payload"]["status_code"] == 200


def test_admin_request_records_page_and_single_export(monkeypatch) -> None:
    record = {
        "request_id": "request-export-1",
        "started_at": "2026-09-14T08:00:00.000+00:00",
        "ended_at": "2026-09-14T08:00:00.100+00:00",
        "method": "POST",
        "path": "/api/client/ai",
        "status_code": 200,
        "elapsed_ms": 100,
        "client": "127.0.0.1",
        "mode": "stream",
        "content_type": "application/json",
        "content_length": "20",
        "event_count": 2,
        "last_component": "http",
        "last_event": "http_request_end",
        "error_type": "",
        "error": "",
        "events": [
            {
                "occurred_at": "2026-09-14T08:00:00.000+00:00",
                "level": "info",
                "component": "http",
                "event": "http_request_begin",
                "payload": {"request_id": "request-export-1", "event": "http_request_begin"},
            },
            {
                "occurred_at": "2026-09-14T08:00:00.100+00:00",
                "level": "info",
                "component": "http",
                "event": "http_request_end",
                "payload": {"request_id": "request-export-1", "event": "http_request_end", "status_code": 200},
            },
        ],
    }

    class FakeStore:
        def list(self, **kwargs):
            return [{key: value for key, value in record.items() if key != "events"}]

        def methods(self):
            return ["POST"]

        def get(self, request_id: str):
            return record if request_id == "request-export-1" else None

    monkeypatch.setattr(client_log_admin_routes, "is_admin", lambda request: True)
    monkeypatch.setattr(client_log_admin_routes, "request_record_store", lambda: FakeStore())

    client = TestClient(app, base_url="http://testserver")
    page = client.get("/admin/requests")
    assert page.status_code == 200
    assert "请求记录" in page.text
    assert "request-export-1" in page.text
    assert "导出日志" in page.text

    exported = client.get("/admin/requests/request-export-1/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/plain")
    assert 'filename="fdex-request-request-export-1.log"' in exported.headers["content-disposition"]
    assert "FDEX Request Record" in exported.text
    assert "http_request_begin" in exported.text
    assert "http_request_end" in exported.text
