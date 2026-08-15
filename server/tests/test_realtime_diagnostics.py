import json

from app import realtime_diagnostics
from app.realtime_voice import _safe_client_diagnostic_details


def test_client_diagnostic_details_only_keep_safe_fields() -> None:
    safe = _safe_client_diagnostic_details(
        {
            "frames": 10,
            "bytes": 4096,
            "sample_rate": 24000,
            "route": "2:Speaker",
            "message": "x" * 500,
            "api_key": "secret",
            "pcm_base64": "AAAA",
            "text": "private transcript",
        }
    )
    assert safe["frames"] == 10
    assert safe["bytes"] == 4096
    assert safe["sample_rate"] == 24000
    assert safe["route"] == "2:Speaker"
    assert len(safe["message"]) == 180
    assert "api_key" not in safe
    assert "pcm_base64" not in safe
    assert "text" not in safe


def test_realtime_diagnostic_is_jsonl_and_owner_only(tmp_path, monkeypatch) -> None:
    target = tmp_path / "realtime-voice.log"
    monkeypatch.setattr(realtime_diagnostics, "DIAGNOSTIC_FILE", target)
    realtime_diagnostics.write_realtime_diagnostic(
        "fdexrt_test",
        "session_summary",
        client_audio_frames=12,
        upstream_audio_frames=8,
    )
    record = json.loads(target.read_text(encoding="utf-8").strip())
    assert record["session_id"] == "fdexrt_test"
    assert record["event"] == "session_summary"
    assert record["details"]["client_audio_frames"] == 12
    assert target.stat().st_mode & 0o777 == 0o600
