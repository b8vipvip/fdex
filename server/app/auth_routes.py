from __future__ import annotations

import asyncio
import hashlib

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.account_cleanup import purge_owned_agent_resources
from app.account_data_export import build_account_export
from app.account_operations import (
    AccountOperationBusy,
    account_operation,
    account_operation_status,
    mark_account_deleted,
)
from app.auth_email import AuthEmailUnavailable, send_password_reset_code
from app.central_auth import AuthRateLimitError, central_auth_store
from app.config import fresh_settings, get_settings
from app.memory_erasure import MemoryErasureError, erase_account_memory, memory_erasure_status
from app.memory_scope_registry import memory_scope_registry

settings = get_settings()
router = APIRouter(prefix=f"{settings.api_prefix}/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    company_name: str = Field(default="", max_length=120)
    device_name: str = Field(default="Android", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    device_name: str = Field(default="Android", max_length=120)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetConfirmRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=80)
    new_password: str = Field(min_length=8, max_length=256)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirmation: str = Field(min_length=1, max_length=80)


class ClearMemoryRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirmation: str = Field(min_length=1, max_length=80)


class RegisterMemoryScopeRequest(BaseModel):
    scope_token: str = Field(min_length=24, max_length=128)


def bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "").strip()
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(status_code=401, detail="FDEX access token is required")
    return value.strip()


def require_user(request: Request) -> dict[str, object]:
    user = central_auth_store().authenticate_access(bearer_token(request))
    if user is None:
        raise HTTPException(status_code=401, detail="FDEX login has expired")
    return user


def _client_ip(request: Request) -> str:
    direct = request.client.host if request.client else ""
    if direct in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:64]
    return direct[:64]


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:300]


def _expand_reset_code(email: str, code: str) -> str:
    raw = (code or "").strip()
    if not (len(raw) == 6 and raw.isdigit()):
        return raw
    store = central_auth_store()
    try:
        normalized = store.normalize_email(email)
    except ValueError:
        return raw
    with store.db() as conn:
        row = conn.execute(
            "SELECT id FROM password_reset_codes WHERE email=? AND used_at='' ORDER BY created_at DESC LIMIT 1",
            (normalized,),
        ).fetchone()
    return f"{row['id']}.{raw}" if row is not None else raw


def _operation_busy(exc: AccountOperationBusy) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "message": "当前账号正在执行其它数据操作，请稍后重试",
            "operation": exc.status.operation,
            "started_at": exc.status.started_at,
        },
    )


@router.post("/register")
def register(body: RegisterRequest, request: Request) -> dict[str, object]:
    if not settings.fdex_auth_registration_enabled:
        raise HTTPException(status_code=403, detail="FDEX registration is disabled")
    try:
        return central_auth_store().register(
            name=body.name,
            email=body.email,
            password=body.password,
            company_name=body.company_name,
            device_name=body.device_name,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login")
def login(body: LoginRequest, request: Request) -> dict[str, object]:
    try:
        return central_auth_store().login(
            email=body.email,
            password=body.password,
            device_name=body.device_name,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except AuthRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh")
def refresh(body: RefreshRequest, request: Request) -> dict[str, object]:
    try:
        return central_auth_store().refresh(
            body.refresh_token,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    return require_user(request)


@router.get("/sessions")
def sessions(request: Request) -> dict[str, object]:
    user = require_user(request)
    current = str(user.get("session_id") or "")
    items = central_auth_store().list_sessions(str(user["id"]))
    for item in items:
        item["current"] = item.get("id") == current
    return {"sessions": items}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: str, request: Request) -> dict[str, object]:
    user = require_user(request)
    if not session_id.startswith("ses_"):
        raise HTTPException(status_code=400, detail="Invalid FDEX session id")
    if not central_auth_store().revoke_session(str(user["id"]), session_id):
        raise HTTPException(status_code=404, detail="Session not found or already revoked")
    return {"ok": True, "current_session_revoked": session_id == str(user.get("session_id") or "")}


@router.post("/logout-all")
def logout_all(request: Request) -> dict[str, object]:
    user = require_user(request)
    revoked = central_auth_store().revoke_user_sessions(str(user["id"]))
    return {"ok": True, "revoked": revoked}


@router.post("/password/change")
def change_password(body: ChangePasswordRequest, request: Request) -> dict[str, object]:
    user = require_user(request)
    try:
        revoked = central_auth_store().change_password(
            str(user["id"]),
            body.current_password,
            body.new_password,
            current_session_id=str(user.get("session_id") or ""),
        )
        return {"ok": True, "other_sessions_revoked": revoked}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/password/reset/request")
def request_password_reset(body: ResetRequest, request: Request) -> dict[str, object]:
    cfg = fresh_settings()
    if not cfg.smtp_ready:
        raise HTTPException(status_code=503, detail="FDEX 邮件服务尚未配置，请联系管理员")
    try:
        reset = central_auth_store().create_password_reset_code(body.email, client_ip=_client_ip(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if reset is not None:
        user, internal_code = reset
        visible_code = internal_code.rsplit(".", 1)[-1]
        try:
            send_password_reset_code(str(user["email"]), visible_code, settings=cfg)
        except AuthEmailUnavailable as exc:
            raise HTTPException(status_code=503, detail="验证码邮件发送失败，请稍后重试") from exc
    return {"ok": True, "message": "如果该邮箱已注册，验证码邮件将很快送达"}


@router.post("/password/reset/confirm")
def confirm_password_reset(body: ResetConfirmRequest, request: Request) -> dict[str, object]:
    try:
        central_auth_store().confirm_password_reset(
            body.email,
            _expand_reset_code(body.email, body.code),
            body.new_password,
            client_ip=_client_ip(request),
        )
        return {"ok": True, "message": "密码已重置，请重新登录"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/security-events")
def security_events(request: Request, limit: int = 30) -> dict[str, object]:
    user = require_user(request)
    return {"events": central_auth_store().security_events(str(user["id"]), limit=limit)}


@router.post("/memory/register-scope")
def register_memory_scope(body: RegisterMemoryScopeRequest, request: Request) -> dict[str, object]:
    """Register a current-device legacy local scope without ever accepting an owner id."""
    user = require_user(request)
    token = body.scope_token.strip()
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in token):
        raise HTTPException(status_code=400, detail="Invalid FDEX memory scope token")
    user_id = str(user["id"])
    bound = hashlib.sha256(f"{user_id}:{token}".encode("utf-8")).hexdigest()
    memory_scope_registry().register(user_id, bound)
    return {"ok": True, "registered_scopes": memory_scope_registry().scope_count(user_id)}


@router.get("/memory/status")
def memory_status(request: Request) -> dict[str, object]:
    user = require_user(request)
    user_id = str(user["id"])
    return {
        **memory_erasure_status(user_id),
        "operation": account_operation_status(user_id).to_dict(),
        "registered_device_scopes": memory_scope_registry().scope_count(user_id),
    }


@router.post("/memory/clear")
def clear_memory(body: ClearMemoryRequest, request: Request) -> dict[str, object]:
    user = require_user(request)
    user_id = str(user["id"])
    if body.confirmation.strip() != "CLEAR MY FDEX MEMORY":
        raise HTTPException(status_code=400, detail="请输入 CLEAR MY FDEX MEMORY 确认清空长期记忆")
    store = central_auth_store()
    try:
        with account_operation(user_id, "memory_clear"):
            if not store.verify_password(user_id, body.password):
                raise HTTPException(status_code=400, detail="密码错误，无法清空长期记忆")
            report = asyncio.run(erase_account_memory(user_id))
    except AccountOperationBusy as exc:
        raise _operation_busy(exc) from exc
    except MemoryErasureError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": "远程长期记忆尚未完全清除，可稍后重试", "code": exc.code},
        ) from exc
    return {"ok": True, "memory_cleanup": report}


@router.get("/data-export")
def data_export(request: Request) -> dict[str, object]:
    user = require_user(request)
    user_id = str(user["id"])
    try:
        with account_operation(user_id, "data_export"):
            return build_account_export(user_id)
    except AccountOperationBusy as exc:
        raise _operation_busy(exc) from exc


@router.post("/account/delete")
def delete_account(body: DeleteAccountRequest, request: Request) -> dict[str, object]:
    user = require_user(request)
    if body.confirmation.strip() != "DELETE MY FDEX":
        raise HTTPException(status_code=400, detail="请输入 DELETE MY FDEX 确认注销")
    store = central_auth_store()
    user_id = str(user["id"])
    try:
        with account_operation(user_id, "account_delete"):
            if not store.verify_password(user_id, body.password):
                raise HTTPException(status_code=400, detail="密码错误，无法注销账号")
            cleanup = purge_owned_agent_resources(user_id)
            deleted = store.delete_account(user_id, body.password)
            # Keep the tombstone write inside the same cross-worker critical section so no
            # stale response can observe an unlocked-but-not-yet-tombstoned identity.
            mark_account_deleted(user_id)
    except AccountOperationBusy as exc:
        raise _operation_busy(exc) from exc
    except MemoryErasureError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": "远程长期记忆清理失败，账号尚未注销，可稍后重试", "code": exc.code},
        ) from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="账号资源清理失败，账号尚未注销") from exc
    return {"ok": True, "deleted_user_id": str(deleted["id"]), "account_cleanup": cleanup}


@router.post("/logout")
def logout(request: Request) -> dict[str, object]:
    central_auth_store().revoke_access(bearer_token(request))
    return {"ok": True}
