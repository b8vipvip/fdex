from __future__ import annotations

import json
from time import perf_counter
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import fresh_settings

router = APIRouter(prefix="/api/client", tags=["android-client"])


class AIRequest(BaseModel):
    system: str | None = Field(default=None, max_length=12000)
    prompt: str = Field(min_length=1, max_length=40000)
    max_tokens: int = Field(default=1200, ge=32, le=4000)


class AIResponse(BaseModel):
    content: str
    model: str
    latency_ms: int


def _messages(payload: AIRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if payload.system and payload.system.strip():
        messages.append({"role": "system", "content": payload.system.strip()})
    messages.append({"role": "user", "content": payload.prompt})
    return messages


def _request_body(payload: AIRequest, model: str, *, stream: bool) -> dict[str, Any]:
    return {
        "model": model,
        "messages": _messages(payload),
        "temperature": 0.5,
        "max_tokens": payload.max_tokens,
        "stream": stream,
    }


def _sse(event_type: str, **payload: Any) -> str:
    data = {"type": event_type, **payload}
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _extract_chat_chunk(data: dict[str, Any]) -> tuple[str, str, str]:
    """Return public status, public reasoning summary delta, and answer delta.

    FDEX deliberately does not expose hidden chain-of-thought. It only forwards
    status or reasoning-summary fields that the upstream protocol explicitly
    marks as user-visible summaries.
    """
    status = ""
    reasoning = ""
    content = ""

    extension = data.get("chat2api")
    if isinstance(extension, dict):
        status = str(extension.get("reasoning_status") or extension.get("status") or "")
        reasoning = str(
            extension.get("reasoning_summary_delta")
            or extension.get("reasoning_summary")
            or ""
        )

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice, dict) else {}
        if isinstance(delta, dict):
            value = delta.get("content")
            if isinstance(value, str):
                content = value
            if not reasoning:
                value = delta.get("reasoning_summary")
                if isinstance(value, str):
                    reasoning = value
            if not status:
                value = delta.get("reasoning_status")
                if isinstance(value, str):
                    status = value

    # Responses-style events are only accepted when they explicitly identify
    # the public reasoning summary channel.
    event_type = str(data.get("type") or "")
    if event_type == "response.output_text.delta":
        value = data.get("delta")
        if isinstance(value, str):
            content = value
    elif event_type == "response.reasoning_summary_text.delta":
        value = data.get("delta")
        if isinstance(value, str):
            reasoning = value

    return status, reasoning, content


def _extract_non_stream_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise KeyError("choices[0]")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise KeyError("choices[0].message")
    value = message.get("content")
    if not isinstance(value, str):
        raise TypeError("choices[0].message.content")
    return value.strip()


@router.post("/ai", response_model=AIResponse)
async def client_ai(payload: AIRequest) -> AIResponse:
    settings = fresh_settings()
    if not settings.ai_enabled:
        raise HTTPException(503, "服务端尚未配置 AI 接口，请先在管理后台完成 AI 配置。")

    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key}",
        "Content-Type": "application/json",
    }
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(
                url,
                headers=headers,
                json=_request_body(payload, settings.ai_model, stream=False),
            )
            response.raise_for_status()
            data = response.json()
        content = _extract_non_stream_content(data)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise HTTPException(502, f"上游 AI 返回错误：{detail}") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"AI 接口调用失败：{str(exc)[:300]}") from exc

    return AIResponse(
        content=content,
        model=settings.ai_model,
        latency_ms=int((perf_counter() - started) * 1000),
    )


@router.post("/ai/stream")
async def client_ai_stream(payload: AIRequest) -> StreamingResponse:
    settings = fresh_settings()
    if not settings.ai_enabled:
        raise HTTPException(503, "服务端尚未配置 AI 接口，请先在管理后台完成 AI 配置。")

    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    async def generate() -> AsyncIterator[str]:
        started = perf_counter()
        answer_seen = False
        try:
            timeout = httpx.Timeout(settings.ai_timeout_seconds, connect=min(15.0, settings.ai_timeout_seconds))
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=_request_body(payload, settings.ai_model, stream=True),
                ) as response:
                    if response.status_code >= 400:
                        raw = (await response.aread()).decode("utf-8", errors="replace")
                        yield _sse("error", message=f"上游 AI 返回 HTTP {response.status_code}：{raw[:300]}")
                        yield "data: [DONE]\n\n"
                        return

                    content_type = response.headers.get("content-type", "").lower()
                    if "text/event-stream" not in content_type:
                        raw = (await response.aread()).decode("utf-8", errors="replace")
                        try:
                            data = json.loads(raw)
                            content = _extract_non_stream_content(data)
                        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                            yield _sse("error", message=f"上游未返回可识别的 SSE/JSON：{str(exc)[:220]}")
                            yield "data: [DONE]\n\n"
                            return
                        if content:
                            answer_seen = True
                            yield _sse("content", delta=content)
                    else:
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or line.startswith(":") or line.startswith("event:"):
                                continue
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            status, reasoning, content = _extract_chat_chunk(data)
                            if status:
                                yield _sse("status", status=status)
                            if reasoning:
                                yield _sse("reasoning", delta=reasoning)
                            if content:
                                answer_seen = True
                                yield _sse("content", delta=content)

            latency_ms = int((perf_counter() - started) * 1000)
            if not answer_seen:
                yield _sse("status", status="回答生成完成，但未收到正文内容")
            yield _sse("done", model=settings.ai_model, latency_ms=latency_ms)
            yield "data: [DONE]\n\n"
        except httpx.HTTPError as exc:
            yield _sse("error", message=f"AI 流式连接失败：{str(exc)[:300]}")
            yield "data: [DONE]\n\n"
        except Exception as exc:  # keep an established SSE response parseable by Android
            yield _sse("error", message=f"AI 流式处理失败：{str(exc)[:300]}")
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
