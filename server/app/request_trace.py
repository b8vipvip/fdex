from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from fastapi import Request

from app.provider_request_trace import bind_request_id, install_provider_request_tracing

_LOGGER = logging.getLogger("uvicorn.error")
_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._:-]+")

# Request history in the admin console is specifically outbound AI Provider traffic. Install the
# transport tracer once when the server request-trace module is imported; ordinary inbound API
# request events remain in the runtime log only and are no longer persisted in that admin list.
install_provider_request_tracing()


def normalize_request_id(value: str | None) -> str:
    text = _REQUEST_ID_RE.sub("-", (value or "").strip())[:80].strip("-._:")
    return text or uuid.uuid4().hex


def request_id_for(request: Request) -> str:
    existing = getattr(request.state, "fdex_request_id", "")
    if existing:
        request_id = str(existing)
        bind_request_id(request_id)
        return request_id
    request_id = normalize_request_id(request.headers.get("x-fdex-request-id"))
    request.state.fdex_request_id = request_id
    bind_request_id(request_id)
    return request_id


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.replace("\r", " ").replace("\n", " ")[:500]
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key)[:80]: _safe_value(item) for key, item in list(value.items())[:30]}
    return _safe_value(str(value))


def _log_event(component: str, event: str, request_id: str, *, level: str = "info", **fields: Any) -> None:
    payload = {
        "component": component,
        "event": event,
        "request_id": request_id,
        **{key: _safe_value(value) for key, value in fields.items()},
    }
    prefix = "FDEX_AI" if component == "client_ai" else "FDEX_HTTP"
    message = prefix + " " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if level == "error":
        _LOGGER.error(message)
    elif level == "warning":
        _LOGGER.warning(message)
    else:
        _LOGGER.info(message)


def log_request_event(event: str, request_id: str, *, level: str = "info", **fields: Any) -> None:
    _log_event("http", event, request_id, level=level, **fields)


def log_ai_event(event: str, request_id: str, *, level: str = "info", **fields: Any) -> None:
    _log_event("client_ai", event, request_id, level=level, **fields)
