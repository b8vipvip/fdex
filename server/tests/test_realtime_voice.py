from app.realtime_voice import normalize_realtime_event, realtime_ws_url


def test_realtime_ws_url_uses_provider_v1_root() -> None:
    assert realtime_ws_url("https://relay.example/v1", "gpt-realtime") == (
        "wss://relay.example/v1/realtime?model=gpt-realtime"
    )
    assert realtime_ws_url("http://127.0.0.1:9000/v1", "voice model") == (
        "ws://127.0.0.1:9000/v1/realtime?model=voice%20model"
    )


def test_normalize_audio_and_transcript_events() -> None:
    assert normalize_realtime_event({"type": "response.audio.delta", "delta": "YWJj"}) == {
        "type": "audio",
        "delta": "YWJj",
    }
    assert normalize_realtime_event(
        {"type": "response.audio_transcript.delta", "delta": "你好"}
    ) == {"type": "assistant_transcript", "delta": "你好"}
    assert normalize_realtime_event(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "测试"}
    ) == {"type": "user_transcript", "text": "测试"}


def test_normalize_status_done_and_error() -> None:
    assert normalize_realtime_event({"type": "input_audio_buffer.speech_started"}) == {
        "type": "status",
        "status": "正在听…",
    }
    assert normalize_realtime_event({"type": "response.done"}) == {"type": "done"}
    assert normalize_realtime_event(
        {"type": "error", "error": {"message": "bad request"}}
    ) == {"type": "error", "message": "bad request"}
