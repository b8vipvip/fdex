from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app.client_log_admin_routes as client_log_admin_routes
import app.provider_request_trace as provider_request_trace
from app.main import app
from app.request_records import RequestRecordStore


def test_provider_request_store_records_one_outbound_call_and_redacts_secrets(tmp_path: Path) -> None:
    store = RequestRecordStore(tmp_path / "provider-request-records.sqlite3")
    record_id = store.begin(
        request_id="request-record-test-1",
        provider_id=7,
        provider="fake-provider",
        model="gpt-test",
        protocol="chat",
        target="https://provider.example/v1/chat/completions",
        method="POST",
        mode="non_stream",
        source="provider_runtime",
    )
    store.add_event(
        record_id,
        "diagnostic",
        authorization="Bearer should-never-be-persisted",
        api_key="should-never-be-persisted",
    )
    store.finish(record_id, status_code=200, elapsed_ms=123, outcome="success", content_type="application/json")

    rows = store.list(provider="fake-provider", status="success", query="gpt-test", limit=10)
    assert len(rows) == 1
    assert rows[0]["record_id"] == record_id
    assert rows[0]["request_id"] == "request-record-test-1"
    assert rows[0]["provider"] == "fake-provider"
    assert rows[0]["model"] == "gpt-test"
    assert rows[0]["protocol"] == "chat"
    assert rows[0]["status_code"] == 200
    assert rows[0]["elapsed_ms"] == 123
    assert rows[0]["outcome"] == "success"

    record = store.get(record_id)
    assert record is not None
    assert len(record["events"]) == 3
    assert record["events"][1]["payload"]["authorization"] == "[REDACTED]"
    assert record["events"][1]["payload"]["api_key"] == "[REDACTED]"


def test_provider_transport_tracer_records_actual_ai_provider_post_only(monkeypatch) -> None:
    begins: list[dict[str, object]] = []
    finishes: list[dict[str, object]] = []

    class CaptureStore:
        def begin(self, **kwargs):
            begins.append(dict(kwargs))
            return "provider-record-1"

        def finish(self, record_id: str, **kwargs):
            finishes.append({"record_id": record_id, **kwargs})

    monkeypatch.setattr(provider_request_trace, "request_record_store", lambda: CaptureStore())

    def match_provider(request: httpx.Request):
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            return {
                "provider_id": 9,
                "provider": "Supplier A",
                "protocol": "chat",
                "target": "https://supplier.example/v1/chat/completions",
            }
        return None

    monkeypatch.setattr(provider_request_trace, "_match_provider", match_provider)
    provider_request_trace.bind_request_id("parent-request-9")

    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await client.post(
                "https://supplier.example/v1/chat/completions",
                headers={"Authorization": "Bearer hidden"},
                json={"model": "gpt-5.6", "stream": False, "messages": [{"role": "user", "content": "secret prompt"}]},
            )
            assert response.status_code == 200
            health = await client.get("https://supplier.example/v1/models")
            assert health.status_code == 200

    asyncio.run(exercise())

    assert len(begins) == 1
    assert begins[0]["request_id"] == "parent-request-9"
    assert begins[0]["provider"] == "Supplier A"
    assert begins[0]["model"] == "gpt-5.6"
    assert begins[0]["protocol"] == "chat"
    assert begins[0]["mode"] == "non_stream"
    assert "secret prompt" not in str(begins[0])
    assert len(finishes) == 1
    assert finishes[0]["record_id"] == "provider-record-1"
    assert finishes[0]["status_code"] == 200
    assert finishes[0]["outcome"] == "success"


def test_public_api_keeps_request_id_but_does_not_become_provider_history() -> None:
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/api/health", headers={"X-FDEX-Request-ID": "health-trace-1"})
    assert response.status_code == 200
    assert response.headers["x-fdex-request-id"] == "health-trace-1"


def test_admin_provider_request_page_and_single_export(monkeypatch) -> None:
    record = {
        "record_id": "provider-record-export-1",
        "request_id": "request-export-1",
        "started_at": "2026-09-14T09:00:00.000+00:00",
        "ended_at": "2026-09-14T09:00:00.100+00:00",
        "provider_id": 3,
        "provider": "Supplier A",
        "model": "gpt-5.6",
        "protocol": "responses",
        "target": "https://supplier.example/v1/responses",
        "method": "POST",
        "mode": "stream",
        "source": "provider_runtime",
        "status_code": 200,
        "elapsed_ms": 100,
        "outcome": "success",
        "event_count": 2,
        "last_event": "provider_request_success",
        "error_type": "",
        "error": "",
        "events": [
            {
                "occurred_at": "2026-09-14T09:00:00.000+00:00",
                "level": "info",
                "event": "provider_request_begin",
                "payload": {"provider": "Supplier A", "model": "gpt-5.6"},
            },
            {
                "occurred_at": "2026-09-14T09:00:00.100+00:00",
                "level": "info",
                "event": "provider_request_success",
                "payload": {"status_code": 200, "elapsed_ms": 100},
            },
        ],
    }

    class FakeStore:
        def list(self, **kwargs):
            return [{key: value for key, value in record.items() if key != "events"}]

        def providers(self):
            return ["Supplier A"]

        def get(self, record_id: str):
            return record if record_id == "provider-record-export-1" else None

    monkeypatch.setattr(client_log_admin_routes, "is_admin", lambda request: True)
    monkeypatch.setattr(client_log_admin_routes, "request_record_store", lambda: FakeStore())

    client = TestClient(app, base_url="http://testserver")
    page = client.get("/admin/requests")
    assert page.status_code == 200
    assert "供应商 AI 请求记录" in page.text
    assert "Supplier A" in page.text
    assert "gpt-5.6" in page.text
    assert "provider-record-export-1" in page.text
    assert "https://supplier.example/v1/responses" in page.text
    assert "导出日志" in page.text

    exported = client.get("/admin/requests/provider-record-export-1/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/plain")
    assert 'filename="fdex-provider-request-provider-record-export-1.log"' in exported.headers["content-disposition"]
    assert "FDEX Provider AI Request Record" in exported.text
    assert "Provider: Supplier A" in exported.text
    assert "Model: gpt-5.6" in exported.text
    assert "provider_request_success" in exported.text
