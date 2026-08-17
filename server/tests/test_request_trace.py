from fastapi.testclient import TestClient

import app.client_ai as client_ai_module
from app.main import app
from app.request_trace import normalize_request_id


def test_normalize_request_id_filters_unsafe_characters() -> None:
    assert normalize_request_id(" upload / case # 1 ") == "upload-case-1"
    assert normalize_request_id("abc:def_123") == "abc:def_123"
    assert normalize_request_id("")


def test_stream_preserves_request_id_and_emits_server_received_status(monkeypatch) -> None:
    fake_provider = {
        "name": "diagnostic-fake",
        "api_key": "",
        "base_url": "https://example.invalid",
        "main_text_model": "",
        "backup_text_models": [],
        "main_vision_model": "",
        "backup_vision_models": [],
        "main_image_model": "",
        "backup_image_models": [],
        "main_audio_model": "",
        "backup_audio_models": [],
        "timeout_seconds": 1,
    }
    monkeypatch.setattr(client_ai_module, "_providers", lambda: [fake_provider])

    client = TestClient(app, base_url="http://testserver")
    response = client.post(
        "/api/client/ai/stream",
        headers={"X-FDEX-Request-ID": "attachment-case-123", "X-FDEX-Request-Mode": "stream"},
        json={"prompt": "测试附件链路"},
    )

    assert response.status_code == 200
    assert response.headers["x-fdex-request-id"] == "attachment-case-123"
    assert "FDEX 服务端已接收请求" in response.text
    assert "所有 AI 供应商均调用失败" in response.text
