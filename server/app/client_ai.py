from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.document_service import prepare_documents
from app.multimodal_service import (
    TASK_AUDIO,
    TASK_IMAGE,
    TASK_TEXT,
    TASK_VISION,
    RouteResult,
    chat_payload,
    detect_task,
    route_audio,
    route_image_generation,
    route_text,
)
from app.provider_manager import (
    api_roots,
    audio_model_candidates,
    image_model_candidates,
    provider_store,
    text_model_candidates,
)
from app.request_trace import log_ai_event, request_id_for

router = APIRouter(prefix="/api/client", tags=["android-client"])

IMAGE_GENERATION_MIN_TIMEOUT_SECONDS = 360
IMAGE_GENERATION_HEARTBEAT_SECONDS = 12
DOCUMENT_PREP_HEARTBEAT_SECONDS = 5
PROVIDER_WAIT_HEARTBEAT_SECONDS = 12


class ImageInput(BaseModel):
    url: str = Field(min_length=5, max_length=12_000_000)
    detail: Literal["auto", "low", "high"] = "auto"


class AudioInput(BaseModel):
    data: str = Field(min_length=4, max_length=24_000_000)
    format: Literal["wav", "mp3"] = "wav"


class DocumentInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    mime_type: str = Field(default="application/octet-stream", max_length=120)
    data: str = Field(min_length=4, max_length=12_000_000)


class AIRequest(BaseModel):
    system: str | None = Field(default=None, max_length=12000)
    prompt: str = Field(default="", max_length=40000)
    max_tokens: int = Field(default=1200, ge=32, le=4000)
    task: Literal["auto", "text", "vision", "image_generation", "audio"] = "auto"
    images: list[ImageInput] = Field(default_factory=list, max_length=4)
    audio: AudioInput | None = None
    documents: list[DocumentInput] = Field(default_factory=list, max_length=3)
    image_size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024"
    voice: str = Field(default="", max_length=80)
    audio_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "wav"

    @model_validator(mode="after")
    def require_input(self) -> "AIRequest":
        if not self.prompt.strip() and not self.images and self.audio is None and not self.documents:
            raise ValueError("prompt、images、audio 或 documents 至少需要提供一项")
        return self


class MediaItem(BaseModel):
    kind: Literal["image", "audio"]
    url: str
    mime_type: str = ""
    transcript: str = ""
    revised_prompt: str = ""


class AIResponse(BaseModel):
    content: str
    model: str
    provider: str = ""
    task: str = TASK_TEXT
    latency_ms: int
    media: list[MediaItem] = Field(default_factory=list)
    fallback_from: str = ""


def _images(payload: AIRequest) -> list[dict[str, str]]:
    return [{"url": item.url, "detail": item.detail} for item in payload.images]


def _audio(payload: AIRequest) -> dict[str, str] | None:
    if payload.audio is None:
        return None
    return {"data": payload.audio.data, "format": payload.audio.format}


def _documents(payload: AIRequest) -> list[dict[str, str]]:
    return [
        {"name": item.name, "mime_type": item.mime_type, "data": item.data}
        for item in payload.documents
    ]


def _payload_log_fields(payload: AIRequest) -> dict[str, Any]:
    return {
        "prompt_chars": len(payload.prompt),
        "system_chars": len(payload.system or ""),
        "requested_task": payload.task,
        "image_count": len(payload.images),
        "image_encoded_chars": sum(len(item.url) for item in payload.images),
        "audio_encoded_chars": len(payload.audio.data) if payload.audio else 0,
        "document_count": len(payload.documents),
        "document_encoded_chars": sum(len(item.data) for item in payload.documents),
        "document_names": [item.name[:100] for item in payload.documents],
        "document_mime_types": [item.mime_type[:80] for item in payload.documents],
    }


async def _prepared_prompt_async(payload: AIRequest, request_id: str) -> tuple[str, int, list[str]]:
    started = perf_counter()
    log_ai_event("document_prepare_begin", request_id, document_count=len(payload.documents))
    prepared = await asyncio.to_thread(prepare_documents, payload.prompt, _documents(payload))
    elapsed_ms = int((perf_counter() - started) * 1000)
    log_ai_event(
        "document_prepare_end",
        request_id,
        elapsed_ms=elapsed_ms,
        extracted_count=prepared.extracted_count,
        note_count=len(prepared.notes),
        notes=prepared.notes,
        prepared_prompt_chars=len(prepared.prompt),
    )
    return prepared.prompt, prepared.extracted_count, prepared.notes


def _sse(event_type: str, **payload: Any) -> str:
    data = {"type": event_type, **payload}
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _extract_chat_chunk(data: dict[str, Any]) -> tuple[str, str, str]:
    """Return public status, public reasoning summary delta, and answer delta."""
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
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    raise TypeError("choices[0].message.content")


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


def _has_specialized(providers: list[dict[str, Any]], task: str) -> bool:
    if task == TASK_IMAGE:
        return any(image_model_candidates(provider) and provider.get("api_key") for provider in providers)
    if task == TASK_AUDIO:
        return any(audio_model_candidates(provider) and provider.get("api_key") for provider in providers)
    return True


def _failure_detail(result: RouteResult, fallback: str) -> str:
    return "；".join(result.errors[-8:]) or fallback


def _image_providers(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    for provider in providers:
        item = dict(provider)
        configured = float(item.get("timeout_seconds") or 60)
        item["timeout_seconds"] = max(configured, IMAGE_GENERATION_MIN_TIMEOUT_SECONDS)
        adjusted.append(item)
    return adjusted


def _provider_target(url: str) -> str:
    try:
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except ValueError:
        return url[:180]


async def _route_non_stream(
    payload: AIRequest,
    providers: list[dict[str, Any]],
    prompt: str,
    request_id: str,
) -> RouteResult:
    images = _images(payload)
    audio = _audio(payload)
    task, explicit = detect_task(
        prompt,
        requested_task=payload.task,
        has_images=bool(images),
        has_audio=audio is not None,
    )
    log_ai_event(
        "route_selected",
        request_id,
        mode="non_stream",
        task=task,
        explicit=explicit,
        provider_count=len(providers),
    )

    if task == TASK_IMAGE:
        if _has_specialized(providers, TASK_IMAGE):
            result = await route_image_generation(
                prompt=prompt,
                size=payload.image_size,
                providers=_image_providers(providers),
            )
            if result.ok or explicit:
                return result
        elif explicit:
            return RouteResult(False, TASK_IMAGE, errors=["未配置可用的图片生成模型供应商"])
        fallback = await route_text(
            system=payload.system,
            prompt=prompt,
            max_tokens=payload.max_tokens,
            providers=providers,
        )
        fallback.fallback_from = TASK_IMAGE
        return fallback

    if task == TASK_AUDIO:
        if _has_specialized(providers, TASK_AUDIO):
            result = await route_audio(
                system=payload.system,
                prompt=prompt,
                max_tokens=payload.max_tokens,
                audio_input=audio,
                requested_voice=payload.voice,
                requested_format=payload.audio_format,
                providers=providers,
            )
            if result.ok or explicit or audio is not None:
                return result
        elif explicit or audio is not None:
            return RouteResult(False, TASK_AUDIO, errors=["未配置可用的语音模型供应商"])
        fallback = await route_text(
            system=payload.system,
            prompt=prompt,
            max_tokens=payload.max_tokens,
            providers=providers,
        )
        fallback.fallback_from = TASK_AUDIO
        return fallback

    return await route_text(
        system=payload.system,
        prompt=prompt,
        max_tokens=payload.max_tokens,
        images=images if task == TASK_VISION else None,
        providers=providers,
    )


@router.post("/ai", response_model=AIResponse)
async def client_ai(request: Request, payload: AIRequest) -> AIResponse:
    request_id = request_id_for(request)
    started = perf_counter()
    log_ai_event("payload_ready", request_id, mode="non_stream", **_payload_log_fields(payload))
    providers = _providers()
    if not providers:
        log_ai_event("no_provider", request_id, level="warning", mode="non_stream")
        raise HTTPException(503, "服务端尚未配置已启用的 AI 供应商，请先在管理后台添加供应商。")

    prompt, _, _ = await _prepared_prompt_async(payload, request_id)
    result = await _route_non_stream(payload, providers, prompt, request_id)
    elapsed_ms = int((perf_counter() - started) * 1000)
    if not result.ok:
        detail = _failure_detail(result, "没有可用模型")
        log_ai_event(
            "non_stream_failed",
            request_id,
            level="warning",
            elapsed_ms=elapsed_ms,
            task=result.task,
            errors=result.errors,
        )
        status = 503 if not result.errors else 502
        raise HTTPException(status, f"AI 能力路由失败：{detail}")

    log_ai_event(
        "non_stream_done",
        request_id,
        elapsed_ms=elapsed_ms,
        provider=result.provider,
        model=result.model,
        task=result.task,
        answer_chars=len(result.content),
        media_count=len(result.media),
    )
    return AIResponse(
        content=result.content,
        model=result.model,
        provider=result.provider,
        task=result.task,
        latency_ms=result.latency_ms,
        media=[MediaItem(**item) for item in result.media],
        fallback_from=result.fallback_from,
    )


async def _emit_specialized(
    payload: AIRequest,
    providers: list[dict[str, Any]],
    task: str,
    explicit: bool,
    prompt: str,
    request_id: str,
) -> AsyncIterator[str]:
    log_ai_event("specialized_begin", request_id, task=task, explicit=explicit)
    if task == TASK_IMAGE:
        if not _has_specialized(providers, TASK_IMAGE):
            if explicit:
                log_ai_event("specialized_unavailable", request_id, level="warning", task=task)
                yield _sse("error", message="未配置可用的图片生成模型供应商")
                yield "data: [DONE]\n\n"
                return
            return
        yield _sse("status", status="已识别为图片生成请求，正在调用图片模型…")
        image_task = asyncio.create_task(
            route_image_generation(
                prompt=prompt,
                size=payload.image_size,
                providers=_image_providers(providers),
            )
        )
        waited = 0
        try:
            while True:
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(image_task),
                        timeout=IMAGE_GENERATION_HEARTBEAT_SECONDS,
                    )
                    break
                except asyncio.TimeoutError:
                    waited += IMAGE_GENERATION_HEARTBEAT_SECONDS
                    log_ai_event("specialized_heartbeat", request_id, task=task, waited_seconds=waited)
                    yield _sse("status", status=f"图片仍在生成，请稍候… 已等待 {waited} 秒")
        except asyncio.CancelledError:
            image_task.cancel()
            raise
        except Exception as exc:
            result = RouteResult(False, TASK_IMAGE, errors=[f"图片生成处理异常：{_safe_error(str(exc))}"])
        finally:
            if not image_task.done():
                image_task.cancel()
    else:
        if not _has_specialized(providers, TASK_AUDIO):
            if explicit or payload.audio is not None:
                log_ai_event("specialized_unavailable", request_id, level="warning", task=task)
                yield _sse("error", message="未配置可用的语音模型供应商")
                yield "data: [DONE]\n\n"
                return
            return
        yield _sse("status", status="已识别为语音请求，正在切换语音模型…")
        result = await route_audio(
            system=payload.system,
            prompt=prompt,
            max_tokens=payload.max_tokens,
            audio_input=_audio(payload),
            requested_voice=payload.voice,
            requested_format=payload.audio_format,
            providers=providers,
        )

    if not result.ok:
        detail = _failure_detail(result, "没有可用模型")
        log_ai_event("specialized_failed", request_id, level="warning", task=task, errors=result.errors)
        if explicit or payload.audio is not None:
            yield _sse("error", message=f"{task} 调用失败：{detail}")
            yield "data: [DONE]\n\n"
        elif task == TASK_IMAGE:
            yield _sse("status", status=f"图片生成线路本次未成功：{detail[:180]}；正在回退文本模型。")
        return

    log_ai_event(
        "specialized_done",
        request_id,
        task=task,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        answer_chars=len(result.content),
        media_count=len(result.media),
    )
    for media in result.media:
        yield _sse("media", **media)
    if result.content:
        yield _sse("content", delta=result.content)
    yield _sse(
        "done",
        model=result.model,
        provider=result.provider,
        task=result.task,
        latency_ms=result.latency_ms,
    )
    yield "data: [DONE]\n\n"


async def _wait_document_prepare(
    payload: AIRequest,
    request_id: str,
) -> AsyncIterator[tuple[str, tuple[str, int, list[str]] | None]]:
    """Yield status messages while document parsing runs off the event loop, then the result."""
    task = asyncio.create_task(_prepared_prompt_async(payload, request_id))
    waited = 0
    try:
        while True:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=DOCUMENT_PREP_HEARTBEAT_SECONDS,
                )
                yield "", result
                return
            except asyncio.TimeoutError:
                waited += DOCUMENT_PREP_HEARTBEAT_SECONDS
                log_ai_event("document_prepare_heartbeat", request_id, waited_seconds=waited)
                yield f"服务端正在解析附件正文… 已处理 {waited} 秒", None
    finally:
        if not task.done():
            task.cancel()


@router.post("/ai/stream")
async def client_ai_stream(request: Request, payload: AIRequest) -> StreamingResponse:
    request_id = request_id_for(request)
    log_ai_event("payload_ready", request_id, mode="stream", **_payload_log_fields(payload))
    providers = _providers()
    if not providers:
        log_ai_event("no_provider", request_id, level="warning", mode="stream")
        raise HTTPException(503, "服务端尚未配置已启用的 AI 供应商，请先在管理后台添加供应商。")

    images = _images(payload)

    async def generate() -> AsyncIterator[str]:
        started_all = perf_counter()
        errors: list[str] = []
        log_ai_event("stream_begin", request_id, provider_count=len(providers))
        yield _sse("status", status="FDEX 服务端已接收请求，正在解析附件与选择模型…")

        prompt = payload.prompt
        extracted_documents = 0
        document_notes: list[str] = []
        async for status, prepared_result in _wait_document_prepare(payload, request_id):
            if status:
                yield _sse("status", status=status)
            if prepared_result is not None:
                prompt, extracted_documents, document_notes = prepared_result
                break

        if payload.documents:
            if extracted_documents:
                yield _sse("status", status=f"已从 {extracted_documents} 份附件提取正文，正在结合内容分析…")
            elif document_notes:
                yield _sse("status", status="附件未提取到可读正文，AI 只会基于实际可用内容回答。")

        task, explicit = detect_task(
            prompt,
            requested_task=payload.task,
            has_images=bool(images),
            has_audio=payload.audio is not None,
        )
        log_ai_event(
            "route_selected",
            request_id,
            mode="stream",
            task=task,
            explicit=explicit,
            image_count=len(images),
            document_extracted_count=extracted_documents,
        )
        yield _sse("status", status=f"附件解析完成，已选择 {task} 路由，正在连接 AI…")

        if task in {TASK_IMAGE, TASK_AUDIO}:
            completed = False
            async for event in _emit_specialized(payload, providers, task, explicit, prompt, request_id):
                if '"type":"done"' in event or event == "data: [DONE]\n\n":
                    completed = True
                yield event
            if completed:
                log_ai_event("stream_done", request_id, task=task, elapsed_ms=int((perf_counter() - started_all) * 1000))
                return
            if explicit or payload.audio is not None:
                log_ai_event("stream_end_specialized_failure", request_id, level="warning", task=task)
                return
            if task == TASK_AUDIO:
                yield _sse("status", status="语音专项模型不可用，已回退文本模型回答。")

        vision = task == TASK_VISION
        for provider in providers:
            provider_name = str(provider.get("name") or "未命名供应商")
            api_key = str(provider.get("api_key") or "")
            models = text_model_candidates(provider, vision=vision)
            if not api_key or not models:
                error = f"{provider_name}：API Key 或模型未完整配置"
                errors.append(error)
                log_ai_event("provider_skipped", request_id, level="warning", provider=provider_name, reason=error)
                continue

            for model in models:
                body = chat_payload(
                    model=model,
                    system=payload.system,
                    prompt=prompt,
                    max_tokens=payload.max_tokens,
                    stream=True,
                    images=images if vision else None,
                )
                for root in api_roots(str(provider.get("base_url") or "")):
                    url = root.rstrip("/") + "/chat/completions"
                    answer_seen = False
                    response: httpx.Response | None = None
                    send_task: asyncio.Task[httpx.Response] | None = None
                    try:
                        timeout_seconds = float(provider.get("timeout_seconds") or 60)
                        timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
                        log_ai_event(
                            "provider_attempt_begin",
                            request_id,
                            provider=provider_name,
                            model=model,
                            target=_provider_target(url),
                            timeout_seconds=timeout_seconds,
                            vision=vision,
                            image_count=len(images) if vision else 0,
                        )
                        yield _sse("status", status=f"正在连接 {provider_name} / {model}…")

                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                            request_upstream = client.build_request(
                                "POST",
                                url,
                                headers={
                                    "Authorization": f"Bearer {api_key}",
                                    "Content-Type": "application/json",
                                    "Accept": "text/event-stream",
                                },
                                json=body,
                            )
                            send_task = asyncio.create_task(client.send(request_upstream, stream=True))
                            waited_headers = 0
                            while True:
                                try:
                                    response = await asyncio.wait_for(
                                        asyncio.shield(send_task),
                                        timeout=PROVIDER_WAIT_HEARTBEAT_SECONDS,
                                    )
                                    break
                                except asyncio.TimeoutError:
                                    waited_headers += PROVIDER_WAIT_HEARTBEAT_SECONDS
                                    log_ai_event(
                                        "provider_wait_headers",
                                        request_id,
                                        provider=provider_name,
                                        model=model,
                                        waited_seconds=waited_headers,
                                    )
                                    yield _sse(
                                        "status",
                                        status=f"附件已上传到 AI 线路，正在等待 {provider_name} / {model} 响应… {waited_headers} 秒",
                                    )

                            log_ai_event(
                                "provider_response_headers",
                                request_id,
                                provider=provider_name,
                                model=model,
                                status_code=response.status_code,
                                content_type=response.headers.get("content-type", ""),
                                waited_seconds=waited_headers,
                            )
                            if response.status_code >= 400:
                                raw = (await response.aread()).decode("utf-8", errors="replace")
                                error = f"{provider_name} / {model}：HTTP {response.status_code} {_safe_error(raw, api_key)}"
                                errors.append(error)
                                log_ai_event(
                                    "provider_http_error",
                                    request_id,
                                    level="warning",
                                    provider=provider_name,
                                    model=model,
                                    status_code=response.status_code,
                                    error=error,
                                )
                                continue

                            content_type = response.headers.get("content-type", "").lower()
                            if "text/event-stream" not in content_type:
                                raw = (await response.aread()).decode("utf-8", errors="replace")
                                try:
                                    content = _extract_non_stream_content(json.loads(raw))
                                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                                    error = f"{provider_name} / {model}：非 SSE 响应无法解析 {str(exc)[:120]}"
                                    errors.append(error)
                                    log_ai_event(
                                        "provider_non_sse_parse_error",
                                        request_id,
                                        level="warning",
                                        provider=provider_name,
                                        model=model,
                                        error=error,
                                    )
                                    continue
                                if not content:
                                    error = f"{provider_name} / {model}：上游未返回正文"
                                    errors.append(error)
                                    log_ai_event(
                                        "provider_empty_non_sse",
                                        request_id,
                                        level="warning",
                                        provider=provider_name,
                                        model=model,
                                    )
                                    continue
                                answer_seen = True
                                log_ai_event(
                                    "provider_first_content",
                                    request_id,
                                    provider=provider_name,
                                    model=model,
                                    mode="non_sse",
                                )
                                yield _sse("content", delta=content)
                            else:
                                iterator = response.aiter_lines().__aiter__()
                                first_upstream_event = False
                                while True:
                                    next_task = asyncio.create_task(iterator.__anext__())
                                    waited_line = 0
                                    try:
                                        while True:
                                            try:
                                                line = await asyncio.wait_for(
                                                    asyncio.shield(next_task),
                                                    timeout=PROVIDER_WAIT_HEARTBEAT_SECONDS,
                                                )
                                                break
                                            except asyncio.TimeoutError:
                                                waited_line += PROVIDER_WAIT_HEARTBEAT_SECONDS
                                                log_ai_event(
                                                    "provider_wait_stream_event",
                                                    request_id,
                                                    provider=provider_name,
                                                    model=model,
                                                    waited_seconds=waited_line,
                                                    answer_seen=answer_seen,
                                                )
                                                yield _sse(
                                                    "status",
                                                    status=f"AI 线路已连接，正在等待模型输出… {waited_line} 秒",
                                                )
                                    except StopAsyncIteration:
                                        break

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
                                    if (status or reasoning or content) and not first_upstream_event:
                                        first_upstream_event = True
                                        log_ai_event(
                                            "provider_first_stream_event",
                                            request_id,
                                            provider=provider_name,
                                            model=model,
                                        )
                                    if status:
                                        yield _sse("status", status=status)
                                    if reasoning:
                                        yield _sse("reasoning", delta=reasoning)
                                    if content:
                                        if not answer_seen:
                                            log_ai_event(
                                                "provider_first_content",
                                                request_id,
                                                provider=provider_name,
                                                model=model,
                                                mode="sse",
                                            )
                                        answer_seen = True
                                        yield _sse("content", delta=content)

                        if answer_seen:
                            elapsed_ms = int((perf_counter() - started_all) * 1000)
                            log_ai_event(
                                "provider_attempt_success",
                                request_id,
                                provider=provider_name,
                                model=model,
                                elapsed_ms=elapsed_ms,
                            )
                            yield _sse(
                                "done",
                                model=model,
                                provider=provider_name,
                                task=TASK_VISION if vision else TASK_TEXT,
                                latency_ms=elapsed_ms,
                            )
                            yield "data: [DONE]\n\n"
                            log_ai_event("stream_done", request_id, elapsed_ms=elapsed_ms, task=task)
                            return
                        error = f"{provider_name} / {model}：流式响应结束但没有正文"
                        errors.append(error)
                        log_ai_event(
                            "provider_stream_empty",
                            request_id,
                            level="warning",
                            provider=provider_name,
                            model=model,
                        )
                    except httpx.HTTPError as exc:
                        safe = _safe_error(str(exc), api_key)
                        log_ai_event(
                            "provider_http_exception",
                            request_id,
                            level="warning" if not answer_seen else "error",
                            provider=provider_name,
                            model=model,
                            error_type=type(exc).__name__,
                            error=safe,
                            answer_seen=answer_seen,
                        )
                        if answer_seen:
                            yield _sse("error", message=f"{provider_name} 流式连接中断：{safe}")
                            yield "data: [DONE]\n\n"
                            return
                        errors.append(f"{provider_name} / {model}：{safe}")
                    except Exception as exc:
                        safe = _safe_error(str(exc), api_key)
                        log_ai_event(
                            "provider_exception",
                            request_id,
                            level="error",
                            provider=provider_name,
                            model=model,
                            error_type=type(exc).__name__,
                            error=safe,
                            answer_seen=answer_seen,
                        )
                        if answer_seen:
                            yield _sse("error", message=f"AI 流式处理失败：{safe}")
                            yield "data: [DONE]\n\n"
                            return
                        errors.append(f"{provider_name} / {model}：{safe}")
                    finally:
                        if send_task is not None and not send_task.done():
                            send_task.cancel()
                        if response is not None:
                            await response.aclose()

        detail = "；".join(errors[-8:]) or "没有可用的 AI 供应商"
        elapsed_ms = int((perf_counter() - started_all) * 1000)
        log_ai_event(
            "stream_all_providers_failed",
            request_id,
            level="warning",
            elapsed_ms=elapsed_ms,
            errors=errors[-8:],
        )
        yield _sse("error", message=f"所有 AI 供应商均调用失败：{detail}")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-FDEX-Request-ID": request_id,
        },
    )
