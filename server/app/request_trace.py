from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from fastapi import Request

_LOGGER = logging.getLogger("uvicorn.error")
_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._:-]+")


def normalize_request_id(value: str | None) -> str:
    text = _REQUEST_ID_RE.sub("-", (value or "").strip())[:80].strip("-._:")
    return text or uuid.uuid4().hex


def request_id_for(request: Request) -> str:
    existing = getattr(request.state, "fdex_request_id", "")
    if existing:
        return str(existing)
    request_id = normalize_request_id(request.headers.get("x-fdex-request-id"))
    request.state.fdex_request_id = request_id
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


def log_ai_event(event: str, request_id: str, *, level: str = "info", **fields: Any) -> None:
    payload = {
        "component": "client_ai",
        "event": event,
        "request_id": request_id,
        **{key: _safe_value(value) for key, value in fields.items()},
    }
    message = "FDEX_AI " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if level == "error":
        _LOGGER.error(message)
    elif level == "warning":
        _LOGGER.warning(message)
    else:
        _LOGGER.info(message)
