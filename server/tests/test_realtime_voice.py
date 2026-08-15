from app.realtime_voice import (
    CHAT2API_LIVE,
    OPENAI_REALTIME,
    build_chat2api_text_event,
    build_openai_text_events,
    build_realtime_session,
    canonical_chat2api_live_model,
    chat2api_live_ws_url,
    model_looks_chat2api_live,
    model_looks_realtime,
    normalize_chat2api_live_event,
    normalize_realtime_event,
    realtime_protocol,
    realtime_ws_url,
)


def test_realtime_ws_url_uses_provider_v1_root() -> None:
    assert realtime_ws_url("https://relay.example/v1", "gpt-realtime") == (
        "wss://relay.example/v1/realtime?model=gpt-realtime"
    )
    assert realtime_ws_url("http://127.0.0.1:9000/v1", "voice model") == (
        "ws://127.0.0.1:9000/v1/realtime?model=voice%20model"
    )


def test_chat2api_live_ws_url_uses_audio_realtime_endpoint() -> None:
    assert chat2api_live_ws_url("https://chat2api.example/v1") == (
        "wss://chat2api.example/v1/audio/realtime"
    )
    assert chat2api_live_ws_url("http://127.0.0.1:9000") == (
        "ws://127.0.0.1:9000/v1/audio/realtime"
    )


def test_current_realtime_session_uses_24k_pcm_and_server_vad() -> None:
    session = build_realtime_session(voice="alloy", instructions="be concise")
    assert session["type"] == "realtime"
    assert session["output_modalities"] == ["audio"]
    assert session["instructions"] == "be concise"
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["input"]["turn_detection"]["create_response"] is True
    assert session["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["voice"] == "alloy"


def test_live_models_are_realtime_candidates() -> None:
    assert model_looks_realtime("gpt-realtime")
    assert model_looks_realtime("gpt-live")
    assert model_looks_realtime("GPT Live")
    assert model_looks_realtime("gpt_live")
    assert model_looks_realtime("vendor-super-live")
    assert not model_looks_realtime("gpt-audio")


def test_gpt_live_uses_chat2api_protocol_even_with_spaces() -> None:
    provider = {"audio_protocol": "auto"}
    assert model_looks_chat2api_live("GPT Live")
    assert model_looks_chat2api_live("gpt_live_mini")
    assert canonical_chat2api_live_model("GPT Live") == "gpt-live"
    assert canonical_chat2api_live_model("gpt live mini") == "gpt-live-mini"
    assert realtime_protocol(provider, "GPT Live") == CHAT2API_LIVE
    assert realtime_protocol(provider, "gpt-live-mini") == CHAT2API_LIVE


def test_openai_realtime_protocol_remains_supported() -> None:
    assert realtime_protocol({"audio_protocol": "auto"}, "gpt-realtime") == OPENAI_REALTIME
    assert realtime_protocol({"audio_protocol": "realtime"}, "vendor-voice") == OPENAI_REALTIME
    assert realtime_protocol({"audio_protocol": "auto"}, "gpt-audio") == ""


def test_normalize_audio_and_transcript_events() -> None:
    assert normalize_realtime_event({"type": "response.output_audio.delta", "delta": "YWJj"}) == {
        "type": "audio",
        "delta": "YWJj",
    }
    assert normalize_realtime_event(
        {"type": "response.output_audio_transcript.delta", "delta": "你好"}
    ) == {"type": "assistant_transcript", "delta": "你好"}
    assert normalize_realtime_event(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "测试"}
    ) == {"type": "user_transcript", "text": "测试"}


def test_normalize_chat2api_live_events() -> None:
    assert normalize_chat2api_live_event({"type": "transcript.final", "text": "你好"}) == {
        "type": "user_transcript",
        "text": "你好",
    }
    assert normalize_chat2api_live_event({"type": "response.text.delta", "delta": "您好"}) == {
        "type": "assistant_transcript",
        "delta": "您好",
    }
    assert normalize_chat2api_live_event({"type": "response.done"}) == {"type": "done"}
    assert normalize_chat2api_live_event(
        {"type": "response.interrupted", "response_id": "r1"}
    ) == {"type": "interrupt", "status": "回答已打断，正在听…"}
    assert normalize_chat2api_live_event(
        {"type": "error", "code": "GPT_LIVE_UNAVAILABLE", "message": "bridge busy"}
    ) == {"type": "error", "message": "bridge busy"}


def test_speech_started_is_an_interrupt_signal_for_local_audio_flush() -> None:
    assert normalize_realtime_event({"type": "input_audio_buffer.speech_started"}) == {
        "type": "interrupt",
        "status": "已打断回答，正在听…",
    }
    assert normalize_chat2api_live_event({"type": "input_audio_buffer.speech_started"}) == {
        "type": "interrupt",
        "status": "已打断回答，正在听…",
    }
    assert normalize_realtime_event({"type": "input_audio_buffer.speech_stopped"}) == {
        "type": "status",
        "status": "正在处理语音…",
    }


def test_realtime_text_is_sent_inside_the_existing_session() -> None:
    assert build_chat2api_text_event("继续说") == {
        "type": "input.text",
        "text": "继续说",
    }
    item, response = build_openai_text_events("继续说")
    assert item == {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "继续说"}],
        },
    }
    assert response == {"type": "response.create"}


def test_normalize_done_and_error() -> None:
    assert normalize_realtime_event({"type": "response.done"}) == {"type": "done"}
    assert normalize_realtime_event(
        {"type": "error", "error": {"message": "bad request"}}
    ) == {"type": "error", "message": "bad request"}
