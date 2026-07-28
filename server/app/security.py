from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import fresh_settings

_LOGIN_WINDOW_SECONDS = 600
_LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _trim_failures(ip: str, now: float) -> deque[float]:
    attempts = _login_failures[ip]
    while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    return attempts


def login_is_limited(request: Request) -> bool:
    now = time.monotonic()
    ip = client_ip(request)
    with _login_lock:
        return len(_trim_failures(ip, now)) >= _LOGIN_MAX_FAILURES


def record_login_failure(request: Request) -> None:
    now = time.monotonic()
    ip = client_ip(request)
    with _login_lock:
        _trim_failures(ip, now).append(now)


def clear_login_failures(request: Request) -> None:
    with _login_lock:
        _login_failures.pop(client_ip(request), None)


def verify_admin_credentials(username: str, password: str) -> bool:
    settings = fresh_settings()
    if not settings.admin_ready:
        return False
    username_ok = hmac.compare_digest(username.strip(), settings.admin_username)
    password_ok = hmac.compare_digest(password, settings.admin_password)
    return username_ok and password_ok


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not isinstance(expected, str) or not hmac.compare_digest(expected, submitted or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")


def login_session(request: Request, username: str) -> None:
    request.session.clear()
    request.session["admin_user"] = username
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    request.session["login_at"] = int(time.time())


def logout_session(request: Request) -> None:
    request.session.clear()


def is_admin(request: Request) -> bool:
    username = request.session.get("admin_user")
    settings = fresh_settings()
    return bool(
        settings.admin_ready
        and isinstance(username, str)
        and hmac.compare_digest(username, settings.admin_username)
    )


def set_flash(request: Request, message: str, category: str = "success") -> None:
    request.session["flash"] = {"message": message, "category": category}


def pop_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("flash", None)
    return value if isinstance(value, dict) else None
