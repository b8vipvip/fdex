from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.auth_email import AuthEmailUnavailable, send_password_reset_code
from app.central_auth import central_auth_store
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


def _expand_reset_code(email: str, code: str) -> str:
    raw = (code or "").strip()
    if not (len(raw) == 6 and raw.isdigit()):
        return raw
    store = central_auth_store()
    try:
        normalized = store.normalize_email(email)
    except ValueError:
        return raw
    store.init()
    with store.db() as conn:
        row = conn.execute(
            "SELECT id FROM password_reset_codes WHERE email=? AND used_at='' ORDER BY created_at DESC LIMIT 1",
            (normalized,),
        ).fetchone()
    return f"{row['id']}.{raw}" if row is not None else raw


@router.get("/register", response_class=HTMLResponse, response_model=None)
def register_page(request: Request) -> Response:
    if _current_user(request) is not None:
        return RedirectResponse("/account/github", status_code=303)
    return templates.TemplateResponse(
        "user_register.html",
        _ctx(request, error="", registration_enabled=fresh_settings().fdex_auth_registration_enabled),
    )


@router.post("/register", response_class=HTMLResponse, response_model=None)
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    company_name: str = Form(default=""),
    csrf_token: str = Form(...),
) -> Response:
    cfg = fresh_settings()
    try:
        _verify_csrf(request, csrf_token)
        if not cfg.fdex_auth_registration_enabled:
            raise ValueError("FDEX 当前已关闭新用户注册")
        if password != confirm_password:
            raise ValueError("两次输入的密码不一致")
        result = central_auth_store().register(
            name=name,
            email=email,
            password=password,
            company_name=company_name,
            device_name="FDEX Web",
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "user_register.html",
            _ctx(
                request,
                error=str(exc),
                registration_enabled=cfg.fdex_auth_registration_enabled,
                form_name=name[:100],
                form_email=email[:254],
                form_company=company_name[:120],
            ),
            status_code=400,
        )
    request.session[_USER_SESSION] = str(result["session_id"])
    request.session[_USER_CSRF] = secrets.token_urlsafe(32)
    return RedirectResponse("/account/github", status_code=303)


@router.get("/password/forgot", response_class=HTMLResponse, response_model=None)
def forgot_password_page(request: Request, email: str = "") -> Response:
    if _current_user(request) is not None:
        return RedirectResponse("/account/github", status_code=303)
    cfg = fresh_settings()
    return templates.TemplateResponse(
        "user_forgot_password.html",
        _ctx(request, error="", message="", email=email[:254], mail_ready=cfg.smtp_ready),
    )


@router.post("/password/forgot", response_class=HTMLResponse, response_model=None)
def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(...),
) -> Response:
    cfg = fresh_settings()
    error = ""
    message = ""
    normalized = email.strip()[:254]
    try:
        _verify_csrf(request, csrf_token)
        if not cfg.smtp_ready:
            raise AuthEmailUnavailable("邮件服务尚未配置，请联系 FDEX 管理员")
        normalized = central_auth_store().normalize_email(email)
        reset = central_auth_store().create_password_reset_code(normalized, client_ip=_client_ip(request))
        if reset is not None:
            user, internal_code = reset
            visible_code = internal_code.rsplit(".", 1)[-1]
            send_password_reset_code(str(user["email"]), visible_code, settings=cfg)
        message = "如果该邮箱已经注册，密码重置验证码会发送到该邮箱。"
    except AuthEmailUnavailable as exc:
        error = str(exc)
    except ValueError as exc:
        error = str(exc)

    status = 503 if error and not cfg.smtp_ready else 400 if error else 200
    return templates.TemplateResponse(
        "user_forgot_password.html",
        _ctx(request, error=error, message=message, email=normalized, mail_ready=cfg.smtp_ready),
        status_code=status,
    )


@router.get("/password/reset", response_class=HTMLResponse, response_model=None)
def reset_password_page(request: Request, email: str = "") -> Response:
    if _current_user(request) is not None:
        return RedirectResponse("/account/github", status_code=303)
    return templates.TemplateResponse(
        "user_reset_password.html",
        _ctx(request, error="", email=email[:254]),
    )


@router.post("/password/reset", response_class=HTMLResponse, response_model=None)
def reset_password_submit(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
) -> Response:
    try:
        _verify_csrf(request, csrf_token)
        if new_password != confirm_password:
            raise ValueError("两次输入的新密码不一致")
        central_auth_store().confirm_password_reset(
            email,
            _expand_reset_code(email, code),
            new_password,
            client_ip=_client_ip(request),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "user_reset_password.html",
            _ctx(request, error=str(exc), email=email[:254]),
            status_code=400,
        )
    return RedirectResponse("/account/login?reset=success", status_code=303)
