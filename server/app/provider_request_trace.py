from __future__ import annotations

import hmac
import logging
import re
from contextvars import ContextVar
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.request_records import request_record_store

_LOGGER = logging.getLogger("uvicorn.error")
_CURRENT_REQUEST_ID: ContextVar[str] = ContextVar("fdex_provider_request_id", default="")
_MODEL_RE = re.compile(rb'"model"\s*:\s*"([^"\\]{1,180})"', re.IGNORECASE)
_STREAM_RE = re.compile(rb'"stream"\s*:\s*true', re.IGNORECASE)
_AI_ENDPOINTS = (
    ("/chat/completions", "chat"),
    ("/responses", "responses"),
    ("/completions", "legacy"),
    ("/images/generations", "images"),
    ("/audio/speech", "audio_speech"),
    ("/audio/transcriptions", "audio_transcriptions"),
    ("/embeddings", "embeddings"),
)


def bind_request_id(request_id: str) -> None:
    _CURRENT_REQUEST_ID.set(str(request_id or "")[:80])


def current_request_id() -> str:
    return _CURRENT_REQUEST_ID.get()


def _target(request: httpx.Request) -> str:
    parsed = urlsplit(str(request.url))
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _protocol_for(path: str) -> str:
    lowered = path.rstrip("/").lower()
    for suffix, protocol in _AI_ENDPOINTS:
        if lowered.endswith(suffix):
            return protocol
    return ""


def _payload_metadata(request: httpx.Request) -> tuple[str, str]:
    try:
        raw = bytes(request.content or b"")[:65536]
    except (TypeError, RuntimeError):
        return "", ""
    match = _MODEL_RE.search(raw)
    model = match.group(1).decode("utf-8", errors="replace") if match else ""
    mode = "stream" if _STREAM_RE.search(raw) else "non_stream"
    return model[:180], mode


def _source_for(request: httpx.Request) -> str:
    user_agent = request.headers.get("user-agent", "")
    lowered = user_agent.lower()
    if "fdex-providerruntime" in lowered:
        return "provider_runtime"
    if "fdex-multimodal" in lowered:
        return "multimodal"
    if "fdex-memory-provider-proxy" in lowered:
        return "memory"
    if "fdex" in lowered:
        return "fdex"
    return "client_ai"


def _bearer(request: httpx.Request) -> str:
    value = request.headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def _matches_root(target: str, root: str) -> bool:
    normalized = str(root or "").strip().rstrip("/")
    return bool(normalized) and (target == normalized or target.startswith(normalized + "/"))


def _match_provider(request: httpx.Request) -> dict[str, Any] | None:
    if request.method.upper() != "POST":
        return None
    protocol = _protocol_for(request.url.path)
    if not protocol:
        return None

    try:
        from app.provider_manager import api_roots, provider_store

        providers = provider_store().list(enabled_only=False, include_secret=True)
    except (RuntimeError, ValueError, OSError):
        return None

    target = _target(request)
    supplied = _bearer(request)
    fallback: dict[str, Any] | None = None
    for provider in providers:
        try:
            roots = api_roots(str(provider.get("base_url") or ""))
        except (TypeError, ValueError):
            continue
        if not any(_matches_root(target, root) for root in roots):
            continue
        candidate = {
            "provider_id": provider.get("id"),
            "provider": str(provider.get("name") or "未命名供应商"),
            "protocol": protocol,
            "target": target,
        }
        if fallback is None:
            fallback = candidate
        configured = str(provider.get("api_key") or "")
        if supplied and configured and hmac.compare_digest(supplied, configured):
            return candidate
    return fallback


def _safe_error(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__).replace("\r", " ").replace("\n", " ")[:500]


def install_provider_request_tracing() -> None:
    current = httpx.AsyncClient.send
    if getattr(current, "__fdex_provider_request_tracing__", False):
        return
    original = current

    async def traced_send(self: httpx.AsyncClient, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        metadata = _match_provider(request)
        if metadata is None:
            return await original(self, request, *args, **kwargs)

        model, mode = _payload_metadata(request)
        record_id = ""
        started = perf_counter()
        try:
            record_id = request_record_store().begin(
                request_id=current_request_id(),
                provider_id=metadata.get("provider_id"),
                provider=str(metadata.get("provider") or ""),
                model=model,
                protocol=str(metadata.get("protocol") or ""),
                target=str(metadata.get("target") or ""),
                method=request.method,
                mode=mode,
                source=_source_for(request),
            )
        except Exception as exc:  # pragma: no cover - diagnostics must never break AI traffic
            _LOGGER.warning("FDEX provider request begin persistence failed: %s", type(exc).__name__)

        try:
            response = await original(self, request, *args, **kwargs)
        except Exception as exc:
            if record_id:
                try:
                    request_record_store().finish(
                        record_id,
                        elapsed_ms=int((perf_counter() - started) * 1000),
                        outcome="error",
                        error_type=type(exc).__name__,
                        error=_safe_error(exc),
                    )
                except Exception as store_exc:  # pragma: no cover
                    _LOGGER.warning("FDEX provider request error persistence failed: %s", type(store_exc).__name__)
            raise

        if record_id:
            try:
                status_code = int(response.status_code)
                request_record_store().finish(
                    record_id,
                    status_code=status_code,
                    elapsed_ms=int((perf_counter() - started) * 1000),
                    outcome="success" if status_code < 400 else "error",
                    error_type="" if status_code < 400 else "HTTPError",
                    error="" if status_code < 400 else f"HTTP {status_code}",
                    content_type=response.headers.get("content-type", ""),
                )
            except Exception as exc:  # pragma: no cover
                _LOGGER.warning("FDEX provider request finish persistence failed: %s", type(exc).__name__)
        return response

    setattr(traced_send, "__fdex_provider_request_tracing__", True)
    httpx.AsyncClient.send = traced_send
