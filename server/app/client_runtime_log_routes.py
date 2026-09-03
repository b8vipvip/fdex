from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth_routes import require_user
from app.client_runtime_logs import client_runtime_log_store
from app.config import get_settings
from app.user_portal_routes import _current_user, _verify_csrf

settings = get_settings()
router = APIRouter(prefix=f"{settings.api_prefix}/client-logs", tags=["client-logs"])


class ClientLogEntry(BaseModel):
    time: str = Field(default="", max_length=80)
    level: str = Field(default="info", max_length=20)
    component: str = Field(default="client", max_length=120)
    event: str = Field(default="event", max_length=160)
    message: str = Field(default="", max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)


class ClientLogBatch(BaseModel):
    device_name: str = Field(default="Android", max_length=160)
    platform: str = Field(default="android", max_length=40)
    app_version: str = Field(default="", max_length=80)
    git_sha: str = Field(default="", max_length=80)
    os_version: str = Field(default="", max_length=120)
    entries: list[ClientLogEntry] = Field(min_length=1, max_length=100)


def _append(body: ClientLogBatch, *, user: dict[str, object], force_platform: str = "") -> int:
    return client_runtime_log_store().append_batch(
        owner_id=str(user["id"]),
        session_id=str(user.get("session_id") or ""),
        device_name=body.device_name,
        platform=force_platform or body.platform,
        app_version=body.app_version,
        git_sha=body.git_sha,
        os_version=body.os_version,
        entries=[entry.model_dump() for entry in body.entries],
    )


@router.post("/batch")
def upload_client_logs(body: ClientLogBatch, request: Request) -> dict[str, object]:
    """Accept bounded diagnostics from the currently authenticated native FDEX client.

    Account/session attribution is derived exclusively from the access token. The payload has no
    owner-id field on purpose, so one client cannot write diagnostics into another account scope.
    """
    user = require_user(request)
    accepted = _append(body, user=user)
    return {"ok": True, "accepted": accepted}


@router.post("/web-batch", include_in_schema=False)
def upload_web_client_logs(body: ClientLogBatch, request: Request) -> dict[str, object]:
    """Accept browser diagnostics from the currently authenticated FDEX Web session.

    Browser uploads use the normal signed web session plus the existing per-session CSRF token.
    Owner/session attribution is always taken from server-side session state; client-supplied
    platform values are ignored so Web diagnostics cannot masquerade as native Android records.
    """
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="FDEX Web login has expired")
    try:
        _verify_csrf(request, request.headers.get("x-fdex-csrf-token", ""))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    accepted = _append(body, user=user, force_platform="web")
    return {"ok": True, "accepted": accepted}
