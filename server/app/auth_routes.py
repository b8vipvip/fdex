from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.central_auth import central_auth_store
from app.config import get_settings

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


@router.post("/register")
def register(body: RegisterRequest) -> dict[str, object]:
    if not settings.fdex_auth_registration_enabled:
        raise HTTPException(status_code=403, detail="FDEX registration is disabled")
    try:
        return central_auth_store().register(
            name=body.name,
            email=body.email,
            password=body.password,
            company_name=body.company_name,
            device_name=body.device_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login")
def login(body: LoginRequest) -> dict[str, object]:
    try:
        return central_auth_store().login(email=body.email, password=body.password, device_name=body.device_name)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh")
def refresh(body: RefreshRequest) -> dict[str, object]:
    try:
        return central_auth_store().refresh(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    return require_user(request)


@router.post("/logout")
def logout(request: Request) -> dict[str, object]:
    central_auth_store().revoke_access(bearer_token(request))
    return {"ok": True}
