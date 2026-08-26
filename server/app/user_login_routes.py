from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.central_auth import AuthRateLimitError, central_auth_store
from app.config import SERVER_DIR, fresh_settings
from app.user_portal_routes import (
    _USER_CSRF,
    _USER_SESSION,
    _client_ip,
    _csrf,
    _current_user,
    _verify_csrf,
)

router = APIRouter(prefix="/account", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "user": _current_user(request),
        "csrf_token": _csrf(request),
        "current_path": request.url.path,
        **extra,
    }


def _safe_next(value: str) -> str:
    clean = (value or "").strip()
    if not clean.startswith("/account") or clean.startswith("//"):
        return "/account"
    return clean


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request, next: str = "/account") -> Response:
    if _current_user(request) is not None:
        return RedirectResponse("/account", status_code=303)
    return templates.TemplateResponse(
        "user_login.html",
        _ctx(request, error="", next_path=_safe_next(next)),
    )


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    next_path: str = Form(default="/account"),
) -> Response:
    target = _safe_next(next_path)
    try:
        _verify_csrf(request, csrf_token)
        result = central_auth_store().login(
            email=email,
            password=password,
            device_name="FDEX Web",
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except AuthRateLimitError as exc:
        return templates.TemplateResponse(
            "user_login.html",
            _ctx(request, error=str(exc), next_path=target),
            status_code=429,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "user_login.html",
            _ctx(request, error=str(exc), next_path=target),
            status_code=401,
        )
    request.session[_USER_SESSION] = str(result["session_id"])
    request.session[_USER_CSRF] = secrets.token_urlsafe(32)
    return RedirectResponse(target, status_code=303)
