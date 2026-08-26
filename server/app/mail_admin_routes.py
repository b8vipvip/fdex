from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.central_auth import central_auth_store
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import mask_secret, read_env, write_env
from app.mail_service import MailServiceError, send_test_email, test_imap_connection
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/admin/mail", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _guard() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


def _int(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} 必须在 {minimum}-{maximum} 之间")
    return parsed


def _float(value: str, name: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} 必须在 {minimum:g}-{maximum:g} 之间")
    return parsed


@router.get("", response_class=HTMLResponse, response_model=None)
def mail_settings_page(request: Request) -> Response:
    if not is_admin(request):
        return _guard()
    cfg = fresh_settings()
    return templates.TemplateResponse(
        "mail_settings.html",
        _ctx(
            request,
            masked_smtp_password=mask_secret(cfg.fdex_smtp_password),
            masked_imap_password=mask_secret(cfg.fdex_imap_password),
            env_path=str(Path(cfg.app_dir) / "server" / ".env"),
        ),
    )


@router.post("", response_model=None)
def save_mail_settings(
    request: Request,
    csrf_token: str = Form(...),
    registration_enabled: str | None = Form(None),
    reset_code_minutes: str = Form("10"),
    reset_max_attempts: str = Form("5"),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from_email: str = Form(""),
    smtp_from_name: str = Form("FDEX"),
    smtp_starttls: str | None = Form(None),
    smtp_ssl: str | None = Form(None),
    smtp_timeout_seconds: str = Form("15"),
    clear_smtp_password: str | None = Form(None),
    imap_host: str = Form(""),
    imap_port: str = Form("993"),
    imap_username: str = Form(""),
    imap_password: str = Form(""),
    imap_mailbox: str = Form("INBOX"),
    imap_ssl: str | None = Form(None),
    imap_starttls: str | None = Form(None),
    imap_timeout_seconds: str = Form("15"),
    clear_imap_password: str | None = Form(None),
) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        reset_minutes = _int(reset_code_minutes, "验证码有效期", 2, 60)
        reset_attempts = _int(reset_max_attempts, "验证码最大尝试次数", 1, 20)
        smtp_port_value = _int(smtp_port, "SMTP 端口", 1, 65535)
        imap_port_value = _int(imap_port, "IMAP 端口", 1, 65535)
        smtp_timeout = _float(smtp_timeout_seconds, "SMTP 超时", 2, 120)
        imap_timeout = _float(imap_timeout_seconds, "IMAP 超时", 2, 120)
        from_email = smtp_from_email.strip()
        if from_email:
            central_auth_store().normalize_email(from_email)
        if len(smtp_host.strip()) > 255 or len(imap_host.strip()) > 255:
            raise ValueError("邮件服务器地址过长")
        if len(smtp_username.strip()) > 254 or len(imap_username.strip()) > 254:
            raise ValueError("邮件账号过长")
        mailbox = (imap_mailbox or "INBOX").strip()[:120] or "INBOX"
        use_smtp_ssl = bool(smtp_ssl)
        use_imap_ssl = bool(imap_ssl)
        use_smtp_starttls = bool(smtp_starttls) and not use_smtp_ssl
        use_imap_starttls = bool(imap_starttls) and not use_imap_ssl
    except ValueError as exc:
        write_audit(request, "save_mail_settings", success=False, error=str(exc))
        set_flash(request, str(exc), "error")
        return RedirectResponse("/admin/mail", status_code=303)

    current = read_env()
    updates = {
        "FDEX_AUTH_REGISTRATION_ENABLED": "true" if registration_enabled else "false",
        "FDEX_AUTH_RESET_CODE_MINUTES": str(reset_minutes),
        "FDEX_AUTH_RESET_MAX_ATTEMPTS": str(reset_attempts),
        "FDEX_SMTP_HOST": smtp_host.strip(),
        "FDEX_SMTP_PORT": str(smtp_port_value),
        "FDEX_SMTP_USERNAME": smtp_username.strip(),
        "FDEX_SMTP_FROM_EMAIL": from_email,
        "FDEX_SMTP_FROM_NAME": (smtp_from_name or "FDEX").strip()[:100] or "FDEX",
        "FDEX_SMTP_STARTTLS": "true" if use_smtp_starttls else "false",
        "FDEX_SMTP_SSL": "true" if use_smtp_ssl else "false",
        "FDEX_SMTP_TIMEOUT_SECONDS": str(smtp_timeout),
        "FDEX_IMAP_HOST": imap_host.strip(),
        "FDEX_IMAP_PORT": str(imap_port_value),
        "FDEX_IMAP_USERNAME": imap_username.strip(),
        "FDEX_IMAP_MAILBOX": mailbox,
        "FDEX_IMAP_SSL": "true" if use_imap_ssl else "false",
        "FDEX_IMAP_STARTTLS": "true" if use_imap_starttls else "false",
        "FDEX_IMAP_TIMEOUT_SECONDS": str(imap_timeout),
    }
    smtp_password_changed = False
    imap_password_changed = False
    if clear_smtp_password:
        updates["FDEX_SMTP_PASSWORD"] = ""
        smtp_password_changed = bool(current.get("FDEX_SMTP_PASSWORD"))
    elif smtp_password:
        updates["FDEX_SMTP_PASSWORD"] = smtp_password
        smtp_password_changed = True
    if clear_imap_password:
        updates["FDEX_IMAP_PASSWORD"] = ""
        imap_password_changed = bool(current.get("FDEX_IMAP_PASSWORD"))
    elif imap_password:
        updates["FDEX_IMAP_PASSWORD"] = imap_password
        imap_password_changed = True

    backup = write_env(updates)
    get_settings.cache_clear()
    changed = [key for key, value in updates.items() if current.get(key, "") != value]
    write_audit(
        request,
        "save_mail_settings",
        changed_keys=[key for key in changed if key not in {"FDEX_SMTP_PASSWORD", "FDEX_IMAP_PASSWORD"}],
        smtp_password_changed=smtp_password_changed,
        imap_password_changed=imap_password_changed,
        backup=str(backup) if backup else "",
    )
    set_flash(request, "邮件与账号验证配置已保存，可直接使用测试按钮验证 SMTP/IMAP。", "success")
    return RedirectResponse("/admin/mail", status_code=303)


@router.post("/test-send", response_model=None)
def test_send(request: Request, csrf_token: str = Form(...), recipient: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        target = central_auth_store().normalize_email(recipient)
        result = send_test_email(target, settings=fresh_settings())
        write_audit(request, "test_smtp", success=True, host=result["host"], port=result["port"])
        set_flash(request, f"SMTP 测试邮件已发送到 {target}", "success")
    except (ValueError, MailServiceError) as exc:
        write_audit(request, "test_smtp", success=False, error=str(exc))
        set_flash(request, f"SMTP 测试失败：{exc}", "error")
    return RedirectResponse("/admin/mail#test", status_code=303)


@router.post("/test-receive", response_model=None)
def test_receive(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        result = test_imap_connection(settings=fresh_settings())
        write_audit(request, "test_imap", success=True, host=result["host"], port=result["port"])
        set_flash(
            request,
            f"IMAP 收件连接正常：{result['mailbox']} 共 {result['messages']} 封，未读 {result['unseen']} 封。",
            "success",
        )
    except MailServiceError as exc:
        write_audit(request, "test_imap", success=False, error=str(exc))
        set_flash(request, f"IMAP 测试失败：{exc}", "error")
    return RedirectResponse("/admin/mail#test", status_code=303)
