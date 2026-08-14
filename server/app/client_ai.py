from __future__ import annotations

import json
from time import perf_counter
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.provider_manager import model_candidates, provider_store

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
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    raise TypeError("choices[0].message.content")


def _chat_urls(base_url: str) -> list[str]:
    """Try the conventional /v1 root first while retaining root-only relays."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        roots = [base, base[:-3].rstrip("/")]
    else:
        roots = [base + "/v1", base]
    return list(dict.fromkeys(root.rstrip("/") + "/chat/completions" for root in roots if root))


def _providers() -> list[dict[str, Any]]:
    try:
        return provider_store().list(enabled_only=True, include_secret=True)
    except RuntimeError as exc:
        raise HTTPException(503, f"AI 供应商配置无法加载：{exc}") from exc


def _safe_error(value: str, api_key: str = "") -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip()
    if api_key:
        text = text.replace(api_key, "***")
    return text[:300]


@router.post("/ai", response_model=AIResponse)
async def client_ai(payload: AIRequest) -> AIResponse:
    providers = _providers()
    if not providers:
        raise HTTPException(503, "服务端尚未配置已启用的 AI 供应商，请先在管理后台添加供应商。")

    started_all = perf_counter()
    errors: list[str] = []
    for provider in providers:
        api_key = str(provider.get("api_key") or "")
        models = model_candidates(provider)
        if not api_key or not models:
            errors.append(f"{provider['name']}：API Key 或文本模型未完整配置")
            continue
        for model in models:
            for url in _chat_urls(provider["base_url"]):
                try:
                    timeout_seconds = float(provider.get("timeout_seconds") or 60)
                    timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        response = await client.post(
                            url,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                            },
                            json=_request_body(payload, model, stream=False),
                        )
                    if not response.is_success:
                        errors.append(f"{provider['name']} / {model}：HTTP {response.status_code} {_safe_error(response.text, api_key)}")
                        continue
                    try:
                        content = _extract_non_stream_content(response.json())
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        errors.append(f"{provider['name']} / {model}：响应解析失败 {str(exc)[:120]}")
                        continue
                    if not content:
                        errors.append(f"{provider['name']} / {model}：上游未返回正文")
                        continue
                    return AIResponse(
                        content=content,
                        model=model,
                        latency_ms=int((perf_counter() - started_all) * 1000),
                    )
                except httpx.HTTPError as exc:
                    errors.append(f"{provider['name']} / {model}：{_safe_error(str(exc))}")

    detail = "；".join(errors[-8:]) or "没有完整配置的供应商"
    raise HTTPException(502, f"所有 AI 供应商均调用失败：{detail}")


@router.post("/ai/stream")
async def client_ai_stream(payload: AIRequest) -> StreamingResponse:
    providers = _providers()
    if not providers:
        raise HTTPException(503, "服务端尚未配置已启用的 AI 供应商，请先在管理后台添加供应商。")

    async def generate() -> AsyncIterator[str]:
        started_all = perf_counter()
        errors: list[str] = []

        for provider in providers:
            api_key = str(provider.get("api_key") or "")
            models = model_candidates(provider)
            if not api_key or not models:
                errors.append(f"{provider['name']}：API Key 或文本模型未完整配置")
                continue

            for model in models:
                for url in _chat_urls(provider["base_url"]):
                    answer_seen = False
                    try:
                        timeout_seconds = float(provider.get("timeout_seconds") or 60)
                        timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                            async with client.stream(
                                "POST",
                                url,
                                headers={
                                    "Authorization": f"Bearer {api_key}",
                                    "Content-Type": "application/json",
                                    "Accept": "text/event-stream",
                                },
                                json=_request_body(payload, model, stream=True),
                            ) as response:
                                if response.status_code >= 400:
                                    raw = (await response.aread()).decode("utf-8", errors="replace")
                                    errors.append(
                                        f"{provider['name']} / {model}：HTTP {response.status_code} {_safe_error(raw, api_key)}"
                                    )
                                    continue

                                content_type = response.headers.get("content-type", "").lower()
                                if "text/event-stream" not in content_type:
                                    raw = (await response.aread()).decode("utf-8", errors="replace")
                                    try:
                                        data = json.loads(raw)
                                        content = _extract_non_stream_content(data)
                                    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                                        errors.append(f"{provider['name']} / {model}：非 SSE 响应无法解析 {str(exc)[:120]}")
                                        continue
                                    if not content:
                                        errors.append(f"{provider['name']} / {model}：上游未返回正文")
                                        continue
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

                        if answer_seen:
                            yield _sse(
                                "done",
                                model=model,
                                provider=str(provider["name"]),
                                latency_ms=int((perf_counter() - started_all) * 1000),
                            )
                            yield "data: [DONE]\n\n"
                            return
                        errors.append(f"{provider['name']} / {model}：流式响应结束但没有正文")
                    except httpx.HTTPError as exc:
                        if answer_seen:
                            yield _sse("error", message=f"{provider['name']} 流式连接中断：{_safe_error(str(exc))}")
                            yield "data: [DONE]\n\n"
                            return
                        errors.append(f"{provider['name']} / {model}：{_safe_error(str(exc))}")
                    except Exception as exc:
                        if answer_seen:
                            yield _sse("error", message=f"AI 流式处理失败：{_safe_error(str(exc))}")
                            yield "data: [DONE]\n\n"
                            return
                        errors.append(f"{provider['name']} / {model}：{_safe_error(str(exc))}")

        detail = "；".join(errors[-8:]) or "没有可用的 AI 供应商"
        yield _sse("error", message=f"所有 AI 供应商均调用失败：{detail}")
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
