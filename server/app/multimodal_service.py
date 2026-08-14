from __future__ import annotations

import base64
import binascii
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import httpx

from app.config import fresh_settings
from app.provider_manager import (
    api_roots,
    audio_model_candidates,
    image_model_candidates,
    provider_store,
    text_model_candidates,
)

TASK_AUTO = "auto"
TASK_TEXT = "text"
TASK_VISION = "vision"
TASK_IMAGE = "image_generation"
TASK_AUDIO = "audio"
TASKS = {TASK_AUTO, TASK_TEXT, TASK_VISION, TASK_IMAGE, TASK_AUDIO}

_IMAGE_INTENT = re.compile(
    r"(?:生成|画|绘制|创建|制作|做一张|设计).{0,12}(?:图片|图像|照片|海报|头像|插画|壁纸|logo|图标)|"
    r"(?:图片|图像|照片|海报|头像|插画|壁纸|logo|图标).{0,12}(?:生成|画|绘制|创建|制作|设计)|"
    r"\b(?:generate|create|draw|make|design)\b.{0,24}\b(?:image|picture|photo|poster|avatar|illustration|wallpaper|logo|icon)\b",
    re.IGNORECASE,
)
_AUDIO_INTENT = re.compile(
    r"(?:语音对话|语音聊天|语音回复|用语音|说给我听|读给我听|朗读|念一下|语音播报|声音回复)|"
    r"\b(?:voice\s*chat|voice\s*reply|speak\s*(?:it|this|to me)?|read\s*aloud)\b",
    re.IGNORECASE,
)
_TEXT_DISCUSSION_HINT = re.compile(r"(?:代码|教程|怎么|如何|原理|接口|API|文档|实现|开发|示例)", re.IGNORECASE)

_GENERATED_DIR = Path(fresh_settings().app_dir) / "server" / "data" / "generated"
_ALLOWED_MEDIA_SUFFIXES = {"png", "jpg", "jpeg", "webp", "mp3", "opus", "aac", "flac", "wav", "pcm"}


@dataclass
class RouteResult:
    ok: bool
    task: str
    content: str = ""
    provider: str = ""
    provider_id: int | None = None
    model: str = ""
    latency_ms: int = 0
    media: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fallback_from: str = ""


def detect_task(
    prompt: str,
    *,
    requested_task: str = TASK_AUTO,
    has_images: bool = False,
    has_audio: bool = False,
) -> tuple[str, bool]:
    """Return (task, explicit).

    Actual media inputs always win. Prompt heuristics are intentionally
    conservative and only apply in auto mode. Callers can always override with
    an explicit task.
    """
    requested = (requested_task or TASK_AUTO).strip().lower()
    if requested not in TASKS:
        requested = TASK_AUTO
    if requested != TASK_AUTO:
        return requested, True
    if has_audio:
        return TASK_AUDIO, False
    if has_images:
        return TASK_VISION, False

    text = (prompt or "").strip()
    if _IMAGE_INTENT.search(text) and not _TEXT_DISCUSSION_HINT.search(text):
        return TASK_IMAGE, False
    if _AUDIO_INTENT.search(text):
        return TASK_AUDIO, False
    return TASK_TEXT, False


def _providers(providers: Iterable[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if providers is not None:
        return list(providers)
    return provider_store().list(enabled_only=True, include_secret=True)


def _headers(api_key: str, accept: str = "application/json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": accept,
        "User-Agent": "FDEX-Multimodal/1.0",
    }


def _safe_error(value: str, api_key: str = "") -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip()
    if api_key:
        text = text.replace(api_key, "***")
    return text[:360]


def _extract_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
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
    return ""


def build_chat_messages(
    system: str | None,
    prompt: str,
    images: list[dict[str, str]] | None = None,
    audio: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system and system.strip():
        messages.append({"role": "system", "content": system.strip()})

    if not images and not audio:
        messages.append({"role": "user", "content": prompt})
        return messages

    content: list[dict[str, Any]] = []
    if prompt.strip():
        content.append({"type": "text", "text": prompt})
    for image in images or []:
        url = str(image.get("url") or "").strip()
        if not url:
            continue
        detail = str(image.get("detail") or "auto").lower()
        if detail not in {"auto", "low", "high"}:
            detail = "auto"
        content.append({"type": "image_url", "image_url": {"url": url, "detail": detail}})
    if audio:
        raw = str(audio.get("data") or "").strip()
        audio_format = str(audio.get("format") or "wav").strip().lower()
        if raw:
            content.append({"type": "input_audio", "input_audio": {"data": raw, "format": audio_format}})
    messages.append({"role": "user", "content": content})
    return messages


def chat_payload(
    *,
    model: str,
    system: str | None,
    prompt: str,
    max_tokens: int,
    stream: bool,
    images: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": build_chat_messages(system, prompt, images=images),
        "temperature": 0.5,
        "max_tokens": max_tokens,
        "stream": stream,
    }


def _media_url(path: Path) -> str:
    base = fresh_settings().public_base_url.rstrip("/")
    return f"{base}/generated/{path.name}"


def _cleanup_generated(max_age_seconds: int = 7 * 24 * 3600) -> None:
    try:
        if not _GENERATED_DIR.exists():
            return
        cutoff = time.time() - max_age_seconds
        for path in _GENERATED_DIR.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        return


def save_base64_media(data: str, suffix: str) -> str:
    suffix = suffix.lower().lstrip(".")
    if suffix not in _ALLOWED_MEDIA_SUFFIXES:
        suffix = "bin"
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("上游返回的媒体 Base64 无效") from exc
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("上游返回的媒体文件超过 32 MB")
    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os_mode = 0o700
        _GENERATED_DIR.chmod(os_mode)
    except OSError:
        pass
    filename = f"{int(time.time())}-{secrets.token_hex(8)}.{suffix}"
    path = _GENERATED_DIR / filename
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(raw)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    _cleanup_generated()
    return _media_url(path)


async def route_text(
    *,
    system: str | None,
    prompt: str,
    max_tokens: int,
    images: list[dict[str, str]] | None = None,
    providers: Iterable[dict[str, Any]] | None = None,
) -> RouteResult:
    started_all = perf_counter()
    errors: list[str] = []
    vision = bool(images)
    for provider in _providers(providers):
        api_key = str(provider.get("api_key") or "")
        models = text_model_candidates(provider, vision=vision)
        if not api_key or not models:
            errors.append(f"{provider.get('name','供应商')}：API Key 或 {'视觉/文本' if vision else '文本'}模型未配置")
            continue
        for model in models:
            body = chat_payload(
                model=model,
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                stream=False,
                images=images,
            )
            for root in api_roots(str(provider.get("base_url") or "")):
                url = root.rstrip("/") + "/chat/completions"
                try:
                    timeout_seconds = float(provider.get("timeout_seconds") or 60)
                    timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        response = await client.post(url, headers=_headers(api_key), json=body)
                    if not response.is_success:
                        errors.append(
                            f"{provider['name']} / {model}：HTTP {response.status_code} {_safe_error(response.text, api_key)}"
                        )
                        continue
                    try:
                        content = _extract_text(response.json())
                    except ValueError as exc:
                        errors.append(f"{provider['name']} / {model}：JSON 解析失败 {str(exc)[:120]}")
                        continue
                    if not content:
                        errors.append(f"{provider['name']} / {model}：上游未返回正文")
                        continue
                    return RouteResult(
                        ok=True,
                        task=TASK_VISION if vision else TASK_TEXT,
                        content=content,
                        provider=str(provider["name"]),
                        provider_id=int(provider["id"]),
                        model=model,
                        latency_ms=int((perf_counter() - started_all) * 1000),
                        errors=errors,
                    )
                except httpx.HTTPError as exc:
                    errors.append(f"{provider['name']} / {model}：{_safe_error(str(exc))}")
    return RouteResult(
        ok=False,
        task=TASK_VISION if vision else TASK_TEXT,
        latency_ms=int((perf_counter() - started_all) * 1000),
        errors=errors,
    )


def _extract_images(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("data")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        revised_prompt = str(item.get("revised_prompt") or "")
        url = str(item.get("url") or "").strip()
        b64 = str(item.get("b64_json") or "").strip()
        if url:
            out.append({"kind": "image", "url": url, "mime_type": "image/png", "revised_prompt": revised_prompt})
        elif b64:
            local_url = save_base64_media(b64, "png")
            out.append({"kind": "image", "url": local_url, "mime_type": "image/png", "revised_prompt": revised_prompt})
    return out


async def route_image_generation(
    *,
    prompt: str,
    size: str = "1024x1024",
    providers: Iterable[dict[str, Any]] | None = None,
) -> RouteResult:
    started_all = perf_counter()
    errors: list[str] = []
    for provider in _providers(providers):
        api_key = str(provider.get("api_key") or "")
        models = image_model_candidates(provider)
        if not api_key or not models:
            continue
        for model in models:
            payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
            for root in api_roots(str(provider.get("base_url") or "")):
                url = root.rstrip("/") + "/images/generations"
                try:
                    timeout_seconds = float(provider.get("timeout_seconds") or 60)
                    timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        response = await client.post(url, headers=_headers(api_key), json=payload)
                    if not response.is_success:
                        errors.append(
                            f"{provider['name']} / {model}：HTTP {response.status_code} {_safe_error(response.text, api_key)}"
                        )
                        continue
                    try:
                        media = _extract_images(response.json())
                    except (ValueError, TypeError) as exc:
                        errors.append(f"{provider['name']} / {model}：图片响应解析失败 {str(exc)[:120]}")
                        continue
                    if not media:
                        errors.append(f"{provider['name']} / {model}：上游未返回图片")
                        continue
                    content = "\n".join(f"[查看生成图片 {index + 1}]({item['url']})" for index, item in enumerate(media))
                    return RouteResult(
                        ok=True,
                        task=TASK_IMAGE,
                        content=content,
                        provider=str(provider["name"]),
                        provider_id=int(provider["id"]),
                        model=model,
                        latency_ms=int((perf_counter() - started_all) * 1000),
                        media=media,
                        errors=errors,
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"{provider['name']} / {model}：{_safe_error(str(exc))}")
    return RouteResult(
        ok=False,
        task=TASK_IMAGE,
        latency_ms=int((perf_counter() - started_all) * 1000),
        errors=errors,
    )


def infer_audio_protocol(provider: dict[str, Any], model: str) -> str:
    configured = str(provider.get("audio_protocol") or "auto").strip().lower()
    if configured in {"chat_audio", "speech"}:
        return configured
    lowered = model.lower()
    if "tts" in lowered or lowered.startswith("tts-") or "speech" in lowered:
        return "speech"
    if "realtime" in lowered:
        return "realtime"
    return "chat_audio"


def _audio_from_chat(data: dict[str, Any], *, default_format: str) -> tuple[str, list[dict[str, Any]]]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", []
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return "", []
    transcript = _extract_text(data)
    audio = message.get("audio")
    if not isinstance(audio, dict):
        return transcript, []
    transcript = str(audio.get("transcript") or transcript or "")
    url = str(audio.get("url") or "").strip()
    raw = str(audio.get("data") or "").strip()
    audio_format = str(audio.get("format") or default_format or "wav").lower()
    mime = "audio/wav" if audio_format == "wav" else f"audio/{audio_format}"
    if url:
        return transcript, [{"kind": "audio", "url": url, "mime_type": mime, "transcript": transcript}]
    if raw:
        local_url = save_base64_media(raw, audio_format)
        return transcript, [{"kind": "audio", "url": local_url, "mime_type": mime, "transcript": transcript}]
    return transcript, []


async def _chat_audio(
    provider: dict[str, Any],
    model: str,
    *,
    system: str | None,
    prompt: str,
    max_tokens: int,
    audio_input: dict[str, str] | None,
    voice: str,
    audio_format: str,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    api_key = str(provider.get("api_key") or "")
    payload: dict[str, Any] = {
        "model": model,
        "messages": build_chat_messages(system, prompt, audio=audio_input),
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": audio_format},
        "max_tokens": max_tokens,
        "temperature": 0.5,
        "stream": False,
    }
    for root in api_roots(str(provider.get("base_url") or "")):
        url = root.rstrip("/") + "/chat/completions"
        try:
            timeout_seconds = float(provider.get("timeout_seconds") or 60)
            timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.post(url, headers=_headers(api_key), json=payload)
            if not response.is_success:
                errors.append(f"HTTP {response.status_code} {_safe_error(response.text, api_key)}")
                continue
            try:
                transcript, media = _audio_from_chat(response.json(), default_format=audio_format)
            except (ValueError, TypeError) as exc:
                errors.append(f"语音响应解析失败 {str(exc)[:120]}")
                continue
            if media:
                return transcript, media, errors
            errors.append("上游返回成功但没有音频内容")
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(_safe_error(str(exc)))
    return "", [], errors


async def _speech_audio(
    provider: dict[str, Any],
    model: str,
    *,
    text: str,
    voice: str,
    audio_format: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    api_key = str(provider.get("api_key") or "")
    payload = {
        "model": model,
        "input": text[:4096],
        "voice": voice,
        "response_format": audio_format,
    }
    for root in api_roots(str(provider.get("base_url") or "")):
        url = root.rstrip("/") + "/audio/speech"
        try:
            timeout_seconds = float(provider.get("timeout_seconds") or 60)
            timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.post(url, headers=_headers(api_key, accept="audio/*"), json=payload)
            if not response.is_success:
                errors.append(f"HTTP {response.status_code} {_safe_error(response.text, api_key)}")
                continue
            raw = response.content
            if not raw:
                errors.append("语音接口返回空文件")
                continue
            b64 = base64.b64encode(raw).decode("ascii")
            local_url = save_base64_media(b64, audio_format)
            mime = response.headers.get("content-type", "").split(";", 1)[0] or (
                "audio/wav" if audio_format == "wav" else f"audio/{audio_format}"
            )
            return [{"kind": "audio", "url": local_url, "mime_type": mime, "transcript": text}], errors
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(_safe_error(str(exc)))
    return [], errors


async def route_audio(
    *,
    system: str | None,
    prompt: str,
    max_tokens: int,
    audio_input: dict[str, str] | None = None,
    requested_voice: str = "",
    requested_format: str = "",
    providers: Iterable[dict[str, Any]] | None = None,
) -> RouteResult:
    started_all = perf_counter()
    errors: list[str] = []
    provider_list = _providers(providers)
    text_answer: RouteResult | None = None

    for provider in provider_list:
        api_key = str(provider.get("api_key") or "")
        models = audio_model_candidates(provider)
        if not api_key or not models:
            continue
        voice = requested_voice.strip() or str(provider.get("audio_voice") or "alloy")
        audio_format = (requested_format.strip().lower() or str(provider.get("audio_format") or "wav").lower())
        if audio_format not in {"mp3", "opus", "aac", "flac", "wav", "pcm"}:
            audio_format = "wav"

        for model in models:
            protocol = infer_audio_protocol(provider, model)
            if protocol == "realtime":
                errors.append(f"{provider['name']} / {model}：Realtime 模型需要实时会话，不适用于当前单次聊天请求")
                continue
            if protocol == "chat_audio":
                transcript, media, attempt_errors = await _chat_audio(
                    provider,
                    model,
                    system=system,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    audio_input=audio_input,
                    voice=voice,
                    audio_format=audio_format,
                )
                errors.extend(f"{provider['name']} / {model}：{item}" for item in attempt_errors)
                if media:
                    content = transcript or "语音回答已生成。"
                    content += f"\n\n[播放语音]({media[0]['url']})"
                    return RouteResult(
                        ok=True,
                        task=TASK_AUDIO,
                        content=content,
                        provider=str(provider["name"]),
                        provider_id=int(provider["id"]),
                        model=model,
                        latency_ms=int((perf_counter() - started_all) * 1000),
                        media=media,
                        errors=errors,
                    )
                continue

            # TTS models cannot understand raw audio. They synthesize the text
            # answer produced by the normal text/vision routing pool.
            if audio_input:
                errors.append(f"{provider['name']} / {model}：speech/TTS 模型不能直接理解输入音频")
                continue
            if text_answer is None:
                text_answer = await route_text(
                    system=system,
                    prompt=prompt,
                    max_tokens=max_tokens,
                )
            if not text_answer.ok:
                errors.extend(text_answer.errors)
                continue
            media, attempt_errors = await _speech_audio(
                provider,
                model,
                text=text_answer.content,
                voice=voice,
                audio_format=audio_format,
            )
            errors.extend(f"{provider['name']} / {model}：{item}" for item in attempt_errors)
            if media:
                content = text_answer.content + f"\n\n[播放语音]({media[0]['url']})"
                return RouteResult(
                    ok=True,
                    task=TASK_AUDIO,
                    content=content,
                    provider=str(provider["name"]),
                    provider_id=int(provider["id"]),
                    model=model,
                    latency_ms=int((perf_counter() - started_all) * 1000),
                    media=media,
                    errors=errors,
                )

    return RouteResult(
        ok=False,
        task=TASK_AUDIO,
        latency_ms=int((perf_counter() - started_all) * 1000),
        errors=errors,
    )


async def probe_specialized_capabilities(provider_id: int) -> dict[str, Any]:
    """Manual, potentially billable specialist probe.

    This is never called by the scheduled deep-test timer.
    """
    store = provider_store()
    provider = store.get(provider_id, include_secret=True)
    results: dict[str, Any] = {}

    # Vision: text models are the default vision pool unless an override exists.
    tiny_png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl9Z5sAAAAASUVORK5CYII="
    vision = await route_text(
        system=None,
        prompt="Describe this image in one short phrase.",
        max_tokens=32,
        images=[{"url": tiny_png, "detail": "low"}],
        providers=[provider],
    )
    results["vision"] = {"ok": vision.ok, "model": vision.model, "error": "；".join(vision.errors[-2:])}

    if image_model_candidates(provider):
        image = await route_image_generation(
            prompt="A simple blue square on a white background, test image",
            size="1024x1024",
            providers=[provider],
        )
        results["image_generation"] = {
            "ok": image.ok,
            "model": image.model,
            "error": "；".join(image.errors[-2:]),
        }
    else:
        results["image_generation"] = {"ok": False, "skipped": True, "error": "未配置图片生成模型"}

    if audio_model_candidates(provider):
        audio = await route_audio(
            system=None,
            prompt="Reply briefly: FDEX audio test OK.",
            max_tokens=64,
            providers=[provider],
        )
        results["audio"] = {"ok": audio.ok, "model": audio.model, "error": "；".join(audio.errors[-2:])}
    else:
        results["audio"] = {"ok": False, "skipped": True, "error": "未配置语音模型"}

    return results
