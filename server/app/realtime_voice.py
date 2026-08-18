from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect

from app.multimodal_service import infer_audio_protocol
from app.provider_manager import audio_model_candidates, provider_store
from app.realtime_diagnostics import write_realtime_diagnostic
from app.realtime_memory import RealtimeMemoryRecorder, clean_realtime_user_text, prepare_realtime_memory

router = APIRouter(prefix="/api/client/voice", tags=["android-client"])

OPENAI_REALTIME = "openai_realtime"
CHAT2API_LIVE = "chat2api_live"


def _normalized_model(model: str) -> str:
    value = (model or "").strip().lower().replace("_", "-")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def realtime_ws_url(base_url: str, model: str) -> str:
    """Build an OpenAI-compatible Realtime websocket URL from a provider BaseUrl."""
    raw = (base_url or "").strip().rstrip("/")
    if raw.endswith("/chat/completions"):
        raw = raw[: -len("/chat/completions")].rstrip("/")
    parsed = urlsplit(raw)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") or "/v1"
    realtime_path = path if path.endswith("/realtime") else f"{path}/realtime"
    return urlunsplit((scheme, parsed.netloc, realtime_path, f"model={quote(model, safe='')}", ""))


def chat2api_live_ws_url(base_url: str) -> str:
    """Build the chat2api-live-v1 websocket URL."""
    raw = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/audio/speech", "/images/generations"):
        if raw.lower().endswith(suffix):
            raw = raw[: -len(suffix)].rstrip("/")
            break
    parsed = urlsplit(raw)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/audio/realtime"):
        realtime_path = path
    elif path.endswith("/v1"):
        realtime_path = f"{path}/audio/realtime"
    elif not path:
        realtime_path = "/v1/audio/realtime"
    else:
        realtime_path = f"{path}/v1/audio/realtime"
    return urlunsplit((scheme, parsed.netloc, realtime_path, "", ""))


def build_realtime_session(*, voice: str, instructions: str = "") -> dict[str, Any]:
    """Current OpenAI-compatible Realtime session shape using 24 kHz PCM audio."""
    session: dict[str, Any] = {
        "type": "realtime",
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "noise_reduction": {"type": "near_field"},
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "voice": voice,
            },
        },
    }
    if instructions:
        session["instructions"] = instructions
    return session


def build_chat2api_text_event(text: str) -> dict[str, Any]:
    """FDEX contract for text injected into the already-open chat2api Live session."""
    return {"type": "input.text", "text": text}


def build_openai_text_events(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """OpenAI Realtime equivalent of injecting one user text turn."""
    return (
        {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        },
        {"type": "response.create"},
    )


def normalize_realtime_event(data: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(data.get("type") or "")
    if event_type in {"session.created", "session.updated"}:
        return {"type": "status", "status": "语音会话已连接"}
    if event_type == "input_audio_buffer.speech_started":
        return {"type": "interrupt", "status": "已打断回答，正在听…"}
    if event_type == "input_audio_buffer.speech_stopped":
        return {"type": "status", "status": "正在处理语音…"}
    if event_type in {"response.created", "response.output_item.added"}:
        return {"type": "status", "status": "正在回答…"}
    if event_type in {"response.audio.delta", "response.output_audio.delta"}:
        delta = data.get("delta")
        if isinstance(delta, str) and delta:
            return {"type": "audio", "delta": delta}
    if event_type in {"response.audio_transcript.delta", "response.output_audio_transcript.delta"}:
        delta = data.get("delta")
        if isinstance(delta, str) and delta:
            return {"type": "assistant_transcript", "delta": delta}
    if event_type in {"response.text.delta", "response.output_text.delta"}:
        delta = data.get("delta")
        if isinstance(delta, str) and delta:
            return {"type": "assistant_transcript", "delta": delta}
    if event_type in {
        "conversation.item.input_audio_transcription.completed",
        "input_audio_buffer.transcription.completed",
    }:
        transcript = data.get("transcript") or data.get("text")
        if isinstance(transcript, str) and transcript:
            return {"type": "user_transcript", "text": transcript}
    if event_type in {"response.done", "response.completed"}:
        return {"type": "done"}
    if event_type == "error":
        error = data.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "Realtime 上游返回错误")
        else:
            message = str(error or "Realtime 上游返回错误")
        return {"type": "error", "message": message[:300]}
    return None


def normalize_chat2api_live_event(data: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(data.get("type") or "")
    if event_type == "session.ready":
        return {"type": "status", "status": "GPT-Live 语音会话已连接"}
    if event_type == "input_audio_buffer.speech_started":
        return {"type": "interrupt", "status": "已打断回答，正在听…"}
    if event_type == "input_audio_buffer.speech_stopped":
        return {"type": "status", "status": "正在处理语音…"}
    if event_type in {"response.created", "response.audio.started"}:
        return {"type": "status", "status": "正在回答…"}
    if event_type == "transcript.final":
        text = data.get("text")
        if isinstance(text, str) and text:
            return {"type": "user_transcript", "text": text}
    if event_type == "response.text.delta":
        delta = data.get("delta")
        if isinstance(delta, str) and delta:
            return {"type": "assistant_transcript", "delta": delta}
    if event_type == "response.interrupted":
        return {"type": "interrupt", "status": "回答已打断，正在听…"}
    if event_type == "response.done":
        return {"type": "done"}
    if event_type == "session.closed":
        return {"type": "status", "status": "实时语音已结束"}
    if event_type == "error":
        message = str(data.get("message") or data.get("code") or "GPT-Live 上游返回错误")
        return {"type": "error", "message": message[:300]}
    return None


def model_looks_chat2api_live(model: str) -> bool:
    normalized = _normalized_model(model)
    return normalized in {"gpt-live", "gpt-live-mini"}


def canonical_chat2api_live_model(model: str) -> str:
    normalized = _normalized_model(model)
    if normalized == "gpt-live-mini":
        return "gpt-live-mini"
    return "gpt-live"


def model_looks_realtime(model: str) -> bool:
    normalized = _normalized_model(model)
    return "realtime" in normalized or model_looks_chat2api_live(model) or normalized.endswith("-live")


def realtime_protocol(provider: dict[str, Any], model: str) -> str:
    """Select the upstream websocket protocol without changing Android's FDEX protocol."""
    if model_looks_chat2api_live(model):
        return CHAT2API_LIVE
    configured = str(provider.get("audio_protocol") or "auto").strip().lower()
    if configured == "realtime":
        return OPENAI_REALTIME
    if infer_audio_protocol(provider, model) == "realtime" or "realtime" in _normalized_model(model):
        return OPENAI_REALTIME
    return ""


def _realtime_candidates() -> list[tuple[dict[str, Any], str, str]]:
    candidates: list[tuple[dict[str, Any], str, str]] = []
    for provider in provider_store().list(enabled_only=True, include_secret=True):
        if not provider.get("api_key"):
            continue
        for model in audio_model_candidates(provider):
            protocol = realtime_protocol(provider, model)
            if protocol:
                candidates.append((provider, model, protocol))
    return candidates


def _safe_client_diagnostic_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "frames", "bytes", "chunk_bytes", "sample_rate", "buffer_bytes", "state", "track_state",
        "play_state", "play_ok", "speaker_enabled", "modern_route_applied", "route", "result", "change",
        "http_code", "aec_available", "aec_enabled", "last_written", "mic_frames", "mic_bytes",
        "received_frames", "received_bytes", "played_frames", "played_bytes", "encoded_chars", "message",
    }
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if isinstance(item, (bool, int, float)) or item is None:
            safe[key] = item
        elif isinstance(item, str):
            safe[key] = item[:180]
    return safe


async def _send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def _await_chat2api_ready(upstream: Any, timeout_seconds: float) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("等待 chat2api session.ready 超时")
        raw = await asyncio.wait_for(upstream.recv(), timeout=remaining)
        if isinstance(raw, bytes):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        event_type = str(data.get("type") or "")
        if event_type == "session.ready":
            return data
        if event_type == "error":
            raise RuntimeError(str(data.get("message") or data.get("code") or "chat2api GPT-Live 初始化失败"))


@router.websocket("/realtime")
async def realtime_voice(websocket: WebSocket) -> None:
    session_id = "fdexrt_" + uuid.uuid4().hex[:12]
    started_at = time.monotonic()
    client_ip = websocket.client.host if websocket.client else ""
    stats: dict[str, int] = {
        "client_audio_frames": 0,
        "client_audio_bytes": 0,
        "upstream_audio_frames": 0,
        "upstream_audio_bytes": 0,
        "client_downlink_frames": 0,
        "client_downlink_bytes": 0,
        "interrupts": 0,
    }
    last_response_audio_started_at = 0.0
    response_active = False

    def diag(event: str, **details: Any) -> None:
        write_realtime_diagnostic(session_id, event, **details)

    diag("client_connected", client_ip=client_ip)
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        start = json.loads(first)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect) as exc:
        diag("start_failed", error=exc.__class__.__name__)
        await _send_json(websocket, {"type": "error", "message": "实时语音会话初始化失败"})
        await websocket.close(code=1008)
        return

    if start.get("type") != "start":
        diag("start_rejected", first_type=str(start.get("type") or ""))
        await _send_json(websocket, {"type": "error", "message": "首条消息必须是 start"})
        await websocket.close(code=1008)
        return

    candidates = _realtime_candidates()
    diag("start_received", candidate_count=len(candidates), has_system=bool(str(start.get("system") or "").strip()))
    if not candidates:
        diag("no_realtime_provider")
        await _send_json(
            websocket,
            {
                "type": "error",
                "message": "没有可用的实时语音供应商；GPT Live/gpt-live 会自动走 chat2api-live-v1，Realtime 模型会走 OpenAI-compatible Realtime。",
            },
        )
        await websocket.close(code=1013)
        return

    raw_system = str(start.get("system") or "").strip()
    raw_memory_control = str(start.get("memory_control") or "").strip()
    system, memory_control, memory_recall = await prepare_realtime_memory(raw_system, raw_memory_control)
    memory_recorder = RealtimeMemoryRecorder(memory_control, diag=diag)
    diag(
        "realtime_memory_bootstrap",
        control=memory_control is not None,
        mempalace_chars=len(memory_recall.mempalace_raw),
        letta_chars=len(memory_recall.letta_structured),
        errors=list(memory_recall.error_codes),
    )
    requested_voice = str(start.get("voice") or "").strip()
    upstream = None
    chosen_provider: dict[str, Any] | None = None
    chosen_model = ""
    chosen_protocol = ""
    errors: list[str] = []

    for provider, model, protocol in candidates:
        if protocol == CHAT2API_LIVE:
            url = chat2api_live_ws_url(str(provider.get("base_url") or ""))
            headers = {
                "Authorization": f"Bearer {provider['api_key']}",
                "User-Agent": "FDEX-Realtime/1.3",
            }
        else:
            url = realtime_ws_url(str(provider.get("base_url") or ""), model)
            headers = {
                "Authorization": f"Bearer {provider['api_key']}",
                "OpenAI-Beta": "realtime=v1",
                "User-Agent": "FDEX-Realtime/1.3",
            }
        diag(
            "upstream_connect_try",
            provider=str(provider.get("name") or ""),
            model=model,
            protocol=protocol,
            upstream_host=urlsplit(url).netloc,
        )
        try:
            upstream = await connect(
                url,
                additional_headers=headers,
                open_timeout=min(20.0, float(provider.get("timeout_seconds") or 60)),
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=16 * 1024 * 1024,
            )
            chosen_provider = provider
            chosen_model = model
            chosen_protocol = protocol
            if protocol == CHAT2API_LIVE:
                await upstream.send(
                    json.dumps(
                        {
                            "type": "session.start",
                            "model": canonical_chat2api_live_model(model),
                            "instructions": system,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                await _await_chat2api_ready(
                    upstream,
                    timeout_seconds=max(20.0, min(90.0, float(provider.get("timeout_seconds") or 60))),
                )
            diag(
                "upstream_connected",
                provider=str(provider.get("name") or ""),
                model=model,
                protocol=protocol,
                upstream_host=urlsplit(url).netloc,
            )
            break
        except Exception as exc:
            errors.append(f"{provider.get('name', '供应商')} / {model}: {str(exc)[:180]}")
            diag(
                "upstream_connect_failed",
                provider=str(provider.get("name") or ""),
                model=model,
                protocol=protocol,
                error_type=exc.__class__.__name__,
                error=str(exc)[:180],
            )
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    pass
            upstream = None
            chosen_provider = None
            chosen_model = ""
            chosen_protocol = ""

    if upstream is None or chosen_provider is None:
        diag("all_upstreams_failed", attempts=len(errors))
        await _send_json(
            websocket,
            {"type": "error", "message": "实时语音供应商连接失败：" + "；".join(errors[-4:])},
        )
        await websocket.close(code=1011)
        return

    voice = requested_voice or str(chosen_provider.get("audio_voice") or "alloy")
    if chosen_protocol == OPENAI_REALTIME:
        await upstream.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": build_realtime_session(voice=voice, instructions=system),
                },
                separators=(",", ":"),
            )
        )
        input_sample_rate = 24000
        output_sample_rate = 24000
    else:
        input_sample_rate = 16000
        output_sample_rate = 24000

    await _send_json(
        websocket,
        {
            "type": "ready",
            "provider": str(chosen_provider.get("name") or ""),
            "model": chosen_model,
            "protocol": chosen_protocol,
            "voice": voice,
            "input_sample_rate": input_sample_rate,
            "output_sample_rate": output_sample_rate,
            "sample_rate": output_sample_rate,
            "text_in_session": True,
            "barge_in": True,
        },
    )
    diag(
        "client_ready_sent",
        provider=str(chosen_provider.get("name") or ""),
        model=chosen_model,
        protocol=chosen_protocol,
        input_sample_rate=input_sample_rate,
        output_sample_rate=output_sample_rate,
    )

    async def client_to_upstream() -> None:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event_type = str(data.get("type") or "")
            if event_type == "audio":
                chunk = str(data.get("data") or "")
                if not chunk:
                    continue
                try:
                    audio_for_stats = base64.b64decode(chunk, validate=True)
                except Exception:
                    diag("client_audio_decode_failed", encoded_chars=len(chunk))
                    continue
                if not audio_for_stats:
                    continue
                stats["client_audio_frames"] += 1
                stats["client_audio_bytes"] += len(audio_for_stats)
                if stats["client_audio_frames"] == 1 or stats["client_audio_frames"] % 100 == 0:
                    diag(
                        "client_audio_progress",
                        frames=stats["client_audio_frames"],
                        bytes=stats["client_audio_bytes"],
                        chunk_bytes=len(audio_for_stats),
                    )
                if chosen_protocol == CHAT2API_LIVE:
                    await upstream.send(audio_for_stats)
                else:
                    await upstream.send(
                        json.dumps(
                            {"type": "input_audio_buffer.append", "audio": chunk},
                            separators=(",", ":"),
                        )
                    )
            elif event_type == "diagnostic":
                diag(
                    "android_" + re.sub(r"[^a-zA-Z0-9_.-]", "_", str(data.get("event") or "unknown"))[:64],
                    **_safe_client_diagnostic_details(data.get("details")),
                )
            elif event_type == "text":
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                memory_recorder.add_user(clean_realtime_user_text(text), source="text")
                diag("client_text_in_session", chars=len(text))
                if chosen_protocol == CHAT2API_LIVE:
                    await upstream.send(
                        json.dumps(
                            build_chat2api_text_event(text),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                else:
                    item_event, response_event = build_openai_text_events(text)
                    await upstream.send(json.dumps(item_event, ensure_ascii=False, separators=(",", ":")))
                    await upstream.send(json.dumps(response_event, separators=(",", ":")))
            elif event_type == "commit" and chosen_protocol == OPENAI_REALTIME:
                await upstream.send('{"type":"input_audio_buffer.commit"}')
                await upstream.send('{"type":"response.create"}')
            elif event_type == "cancel":
                diag("client_cancel")
                await upstream.send('{"type":"response.cancel"}')
            elif event_type == "clear" and chosen_protocol == OPENAI_REALTIME:
                await upstream.send('{"type":"input_audio_buffer.clear"}')
            elif event_type == "stop":
                diag("client_stop")
                if chosen_protocol == CHAT2API_LIVE:
                    await upstream.send('{"type":"session.finish"}')
                return

    async def upstream_to_client() -> None:
        nonlocal last_response_audio_started_at, response_active
        async for raw in upstream:
            if isinstance(raw, bytes):
                if chosen_protocol == CHAT2API_LIVE and raw:
                    stats["upstream_audio_frames"] += 1
                    stats["upstream_audio_bytes"] += len(raw)
                    if stats["upstream_audio_frames"] == 1 or stats["upstream_audio_frames"] % 50 == 0:
                        diag(
                            "upstream_audio_progress",
                            frames=stats["upstream_audio_frames"],
                            bytes=stats["upstream_audio_bytes"],
                            chunk_bytes=len(raw),
                        )
                    await _send_json(
                        websocket,
                        {"type": "audio", "delta": base64.b64encode(raw).decode("ascii")},
                    )
                    stats["client_downlink_frames"] += 1
                    stats["client_downlink_bytes"] += len(raw)
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                diag("upstream_non_json_text", chars=len(raw))
                continue
            if not isinstance(data, dict):
                continue
            event_type = str(data.get("type") or "")
            if event_type in {"response.created", "response.audio.started", "response.output_item.added"}:
                response_active = True
                if event_type == "response.audio.started":
                    last_response_audio_started_at = time.monotonic()
                diag("upstream_event", upstream_event=event_type)
            elif event_type in {"response.done", "response.completed", "response.interrupted"}:
                diag("upstream_event", upstream_event=event_type)
                response_active = False
            elif event_type == "input_audio_buffer.speech_started":
                stats["interrupts"] += 1
                since_audio_started_ms = (
                    int((time.monotonic() - last_response_audio_started_at) * 1000)
                    if last_response_audio_started_at > 0
                    else -1
                )
                diag(
                    "speech_started",
                    response_active=response_active,
                    since_audio_started_ms=since_audio_started_ms,
                    upstream_audio_frames=stats["upstream_audio_frames"],
                    client_downlink_frames=stats["client_downlink_frames"],
                )
                if chosen_protocol == CHAT2API_LIVE and response_active:
                    try:
                        await upstream.send('{"type":"response.cancel"}')
                        diag("barge_in_cancel_sent", since_audio_started_ms=since_audio_started_ms)
                    except Exception as exc:
                        diag("barge_in_cancel_failed", error_type=exc.__class__.__name__)
            if chosen_protocol == CHAT2API_LIVE:
                event = normalize_chat2api_live_event(data)
            else:
                event = normalize_realtime_event(data)
            if event:
                memory_event_type = str(event.get("type") or "")
                if memory_event_type == "user_transcript":
                    memory_recorder.add_user(str(event.get("text") or ""), source="voice")
                elif memory_event_type == "assistant_transcript":
                    memory_recorder.add_assistant_delta(str(event.get("delta") or ""))
                elif memory_event_type == "interrupt":
                    memory_recorder.complete(reason="interrupt")
                elif memory_event_type == "done":
                    if chosen_protocol == CHAT2API_LIVE:
                        memory_recorder.set_assistant_final(str(data.get("text") or ""))
                    memory_recorder.complete(reason="done")
                if event.get("type") == "audio" and chosen_protocol == OPENAI_REALTIME:
                    encoded = str(event.get("delta") or "")
                    try:
                        audio_len = len(base64.b64decode(encoded, validate=True))
                    except Exception:
                        audio_len = 0
                    if audio_len:
                        stats["upstream_audio_frames"] += 1
                        stats["upstream_audio_bytes"] += audio_len
                        stats["client_downlink_frames"] += 1
                        stats["client_downlink_bytes"] += audio_len
                        if stats["upstream_audio_frames"] == 1 or stats["upstream_audio_frames"] % 50 == 0:
                            diag(
                                "upstream_audio_progress",
                                frames=stats["upstream_audio_frames"],
                                bytes=stats["upstream_audio_bytes"],
                                chunk_bytes=audio_len,
                            )
                await _send_json(websocket, event)

    try:
        done, pending = await asyncio.wait(
            [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        diag("client_disconnected")
    except Exception as exc:
        diag("session_exception", error_type=exc.__class__.__name__, error=str(exc)[:220])
        try:
            await _send_json(websocket, {"type": "error", "message": f"实时语音连接中断：{str(exc)[:240]}"})
        except Exception:
            pass
    finally:
        diag(
            "session_summary",
            duration_ms=int((time.monotonic() - started_at) * 1000),
            provider=str((chosen_provider or {}).get("name") or ""),
            model=chosen_model,
            protocol=chosen_protocol,
            **stats,
        )
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
