from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect

from app.multimodal_service import infer_audio_protocol
from app.provider_manager import audio_model_candidates, provider_store

router = APIRouter(prefix="/api/client/voice", tags=["android-client"])


def realtime_ws_url(base_url: str, model: str) -> str:
    """Build an OpenAI-compatible Realtime websocket URL from a provider BaseUrl."""
    raw = (base_url or "").strip().rstrip("/")
    if raw.endswith("/chat/completions"):
        raw = raw[: -len("/chat/completions")].rstrip("/")
    parsed = urlsplit(raw)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") or "/v1"
    return urlunsplit((scheme, parsed.netloc, f"{path}/realtime", f"model={quote(model, safe='')}", ""))


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


def normalize_realtime_event(data: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(data.get("type") or "")
    if event_type in {"session.created", "session.updated"}:
        return {"type": "status", "status": "语音会话已连接"}
    if event_type in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}:
        return {
            "type": "status",
            "status": "正在听…" if event_type.endswith("started") else "正在处理语音…",
        }
    if event_type in {"response.created", "response.output_item.added"}:
        return {"type": "status", "status": "正在回答…"}
    if event_type in {"response.audio.delta", "response.output_audio.delta"}:
        delta = data.get("delta")
        if isinstance(delta, str) and delta:
            return {"type": "audio", "delta": delta}
    if event_type in {
        "response.audio_transcript.delta",
        "response.output_audio_transcript.delta",
    }:
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


def model_looks_realtime(model: str) -> bool:
    lowered = (model or "").strip().lower()
    return "realtime" in lowered or "gpt-live" in lowered or lowered.endswith("-live")


def _realtime_candidates() -> list[tuple[dict[str, Any], str]]:
    candidates: list[tuple[dict[str, Any], str]] = []
    for provider in provider_store().list(enabled_only=True, include_secret=True):
        if not provider.get("api_key"):
            continue
        for model in audio_model_candidates(provider):
            if infer_audio_protocol(provider, model) == "realtime" or model_looks_realtime(model):
                candidates.append((provider, model))
    return candidates


async def _send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


@router.websocket("/realtime")
async def realtime_voice(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        start = json.loads(first)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        await _send_json(websocket, {"type": "error", "message": "实时语音会话初始化失败"})
        await websocket.close(code=1008)
        return

    if start.get("type") != "start":
        await _send_json(websocket, {"type": "error", "message": "首条消息必须是 start"})
        await websocket.close(code=1008)
        return

    candidates = _realtime_candidates()
    if not candidates:
        await _send_json(
            websocket,
            {
                "type": "error",
                "message": "没有可用的 Realtime 语音供应商；请在供应商管理中配置名称含 realtime/live 的语音模型。",
            },
        )
        await websocket.close(code=1013)
        return

    system = str(start.get("system") or "").strip()
    requested_voice = str(start.get("voice") or "").strip()
    upstream = None
    chosen_provider: dict[str, Any] | None = None
    chosen_model = ""
    errors: list[str] = []

    for provider, model in candidates:
        url = realtime_ws_url(str(provider.get("base_url") or ""), model)
        try:
            upstream = await connect(
                url,
                additional_headers={
                    "Authorization": f"Bearer {provider['api_key']}",
                    "OpenAI-Beta": "realtime=v1",
                    "User-Agent": "FDEX-Realtime/1.0",
                },
                open_timeout=min(15.0, float(provider.get("timeout_seconds") or 60)),
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=16 * 1024 * 1024,
            )
            chosen_provider = provider
            chosen_model = model
            break
        except Exception as exc:
            errors.append(f"{provider.get('name', '供应商')} / {model}: {str(exc)[:160]}")

    if upstream is None or chosen_provider is None:
        await _send_json(
            websocket,
            {"type": "error", "message": "Realtime 供应商连接失败：" + "；".join(errors[-4:])},
        )
        await websocket.close(code=1011)
        return

    voice = requested_voice or str(chosen_provider.get("audio_voice") or "alloy")
    await upstream.send(
        json.dumps(
            {
                "type": "session.update",
                "session": build_realtime_session(voice=voice, instructions=system),
            },
            separators=(",", ":"),
        )
    )
    await _send_json(
        websocket,
        {
            "type": "ready",
            "provider": str(chosen_provider.get("name") or ""),
            "model": chosen_model,
            "voice": voice,
            "sample_rate": 24000,
        },
    )

    async def client_to_upstream() -> None:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event_type = str(data.get("type") or "")
            if event_type == "audio":
                chunk = str(data.get("data") or "")
                if chunk:
                    await upstream.send(json.dumps({"type": "input_audio_buffer.append", "audio": chunk}, separators=(",", ":")))
            elif event_type == "commit":
                await upstream.send('{"type":"input_audio_buffer.commit"}')
                await upstream.send('{"type":"response.create"}')
            elif event_type == "cancel":
                await upstream.send('{"type":"response.cancel"}')
            elif event_type == "clear":
                await upstream.send('{"type":"input_audio_buffer.clear"}')
            elif event_type == "stop":
                return

    async def upstream_to_client() -> None:
        async for raw in upstream:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            event = normalize_realtime_event(data)
            if event:
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
        pass
    except Exception as exc:
        try:
            await _send_json(websocket, {"type": "error", "message": f"实时语音连接中断：{str(exc)[:240]}"})
        except Exception:
            pass
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
