from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.auth_routes import require_user
from app.client_runtime_logs import client_runtime_log_store
from app.config import get_settings

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


@router.post("/batch")
def upload_client_logs(body: ClientLogBatch, request: Request) -> dict[str, object]:
    """Accept bounded diagnostics from the currently authenticated FDEX client.

    Account/session attribution is derived exclusively from the access token. The payload has no
    owner-id field on purpose, so one client cannot write diagnostics into another account scope.
    """
    user = require_user(request)
    accepted = client_runtime_log_store().append_batch(
        owner_id=str(user["id"]),
        session_id=str(user.get("session_id") or ""),
        device_name=body.device_name,
        platform=body.platform,
        app_version=body.app_version,
        git_sha=body.git_sha,
        os_version=body.os_version,
        entries=[entry.model_dump() for entry in body.entries],
    )
    return {"ok": True, "accepted": accepted}
