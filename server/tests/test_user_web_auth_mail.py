from __future__ import annotations

from pathlib import Path

import pytest

from app import auth_email, mail_service
from app.config import Settings
from app.mail_service import MailServiceError, send_test_email, test_imap_connection as check_imap_connection
from app.user_account_auth_routes import router as user_auth_router


def _smtp_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "fdex_smtp_host": "smtp.example.test",
        "fdex_smtp_port": 587,
        "fdex_smtp_username": "mailer@example.test",
        "fdex_smtp_password": "smtp-secret",
        "fdex_smtp_from_email": "no-reply@example.test",
        "fdex_smtp_from_name": "FDEX Test",
        "fdex_smtp_starttls": True,
        "fdex_smtp_ssl": False,
        "fdex_smtp_timeout_seconds": 10,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _imap_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "fdex_imap_host": "imap.example.test",
        "fdex_imap_port": 993,
        "fdex_imap_username": "mailer@example.test",
        "fdex_imap_password": "imap-secret",
        "fdex_imap_mailbox": "INBOX",
        "fdex_imap_ssl": True,
        "fdex_imap_starttls": False,
        "fdex_imap_timeout_seconds": 10,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_user_web_auth_routes_expose_registration_and_password_recovery() -> None:
    methods = {(route.path, tuple(sorted(route.methods or []))) for route in user_auth_router.routes}
    assert ("/account/register", ("GET",)) in methods
    assert ("/account/register", ("POST",)) in methods
    assert ("/account/password/forgot", ("GET",)) in methods
    assert ("/account/password/forgot", ("POST",)) in methods
    assert ("/account/password/reset", ("GET",)) in methods
    assert ("/account/password/reset", ("POST",)) in methods
    assert all(not route.path.startswith("/admin") for route in user_auth_router.routes)


def test_user_login_links_to_register_and_forgot_password() -> None:
    root = Path(__file__).resolve().parents[2]
    login = (root / "server/app/templates/user_login.html").read_text(encoding="utf-8")
    register = (root / "server/app/templates/user_register.html").read_text(encoding="utf-8")
    forgot = (root / "server/app/templates/user_forgot_password.html").read_text(encoding="utf-8")
    reset = (root / "server/app/templates/user_reset_password.html").read_text(encoding="utf-8")

    assert 'href="/account/register"' in login
    assert 'href="/account/password/forgot"' in login
    assert 'action="/account/register"' in register
    assert 'action="/account/password/forgot"' in forgot
    assert 'action="/account/password/reset"' in reset
    assert 'autocomplete="one-time-code"' in reset


def test_password_recovery_does_not_render_or_return_internal_reset_identifier() -> None:
    root = Path(__file__).resolve().parents[2]
    routes = (root / "server/app/user_account_auth_routes.py").read_text(encoding="utf-8")
    forgot = (root / "server/app/templates/user_forgot_password.html").read_text(encoding="utf-8")

    assert "如果该邮箱已经注册" in routes
    assert "internal_code.rsplit" in routes
    assert "password_reset_codes" not in forgot
    assert "internal_code" not in forgot
    assert "reset[" not in forgot


def test_smtp_test_uses_tls_login_and_does_not_return_password(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float):
            calls["connect"] = (host, port, timeout)

        def starttls(self, context=None):
            calls["starttls"] = context is not None
            return (220, b"ready")

        def login(self, username: str, password: str):
            calls["login"] = (username, password)
            return (235, b"ok")

        def send_message(self, message):
            calls["subject"] = message["Subject"]
            calls["to"] = message["To"]
            return {}

        def quit(self):
            calls["quit"] = True
            return (221, b"bye")

        def close(self):
            calls["close"] = True

    monkeypatch.setattr(mail_service.smtplib, "SMTP", FakeSMTP)
    result = send_test_email("owner@example.test", settings=_smtp_settings())

    assert calls["connect"] == ("smtp.example.test", 587, 10.0)
    assert calls["starttls"] is True
    assert calls["login"] == ("mailer@example.test", "smtp-secret")
    assert calls["to"] == "owner@example.test"
    assert result["ok"] is True
    assert "password" not in result
    assert "secret" not in str(result)


def test_imap_test_is_readonly_and_reports_counts_without_fetching_content(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeIMAP:
        def __init__(self, host: str, port: int, ssl_context=None, timeout: float | None = None):
            calls.append(("connect", host, port, timeout, ssl_context is not None))

        def login(self, username: str, password: str):
            calls.append(("login", username, password))
            return "OK", [b"logged"]

        def select(self, mailbox: str, readonly: bool = False):
            calls.append(("select", mailbox, readonly))
            return "OK", [b"12"]

        def search(self, charset, criterion: str):
            calls.append(("search", charset, criterion))
            return "OK", [b"1 7 11"]

        def logout(self):
            calls.append(("logout",))
            return "BYE", [b"bye"]

    monkeypatch.setattr(mail_service.imaplib, "IMAP4_SSL", FakeIMAP)
    result = check_imap_connection(settings=_imap_settings())

    assert ("select", "INBOX", True) in calls
    assert not any(item and item[0] == "fetch" for item in calls)
    assert result["messages"] == 12
    assert result["unseen"] == 3
    assert "password" not in result


def test_reset_email_transport_failure_is_hidden_from_public_reset_request(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_delivery(message, *, settings=None):
        raise MailServiceError("transport down")

    monkeypatch.setattr(auth_email, "send_message", fail_delivery)
    delivered = auth_email.send_password_reset_code(
        "owner@example.test",
        "123456",
        settings=_smtp_settings(),
    )
    assert delivered is False


def test_mail_configuration_ui_masks_saved_passwords_and_has_real_tests() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "server/app/templates/mail_settings.html").read_text(encoding="utf-8")
    routes = (root / "server/app/mail_admin_routes.py").read_text(encoding="utf-8")
    env_example = (root / "server/.env.example").read_text(encoding="utf-8")

    assert "masked_smtp_password" in template
    assert "masked_imap_password" in template
    assert "settings.fdex_smtp_password" not in template
    assert "settings.fdex_imap_password" not in template
    assert 'action="/admin/mail/test-send"' in template
    assert 'action="/admin/mail/test-receive"' in template
    assert "FDEX_SMTP_PASSWORD" in routes
    assert "FDEX_IMAP_PASSWORD" in routes
    assert 'key not in {"FDEX_SMTP_PASSWORD", "FDEX_IMAP_PASSWORD"}' in routes
    assert "FDEX_IMAP_HOST=" in env_example
    assert "FDEX_IMAP_SSL=true" in env_example


def test_mail_service_fails_closed_when_transport_is_not_configured() -> None:
    with pytest.raises(MailServiceError, match="SMTP 尚未配置完整"):
        send_test_email("owner@example.test", settings=Settings(_env_file=None, fdex_smtp_host="", fdex_smtp_from_email=""))
    with pytest.raises(MailServiceError, match="IMAP 尚未配置完整"):
        check_imap_connection(settings=Settings(_env_file=None, fdex_imap_host="", fdex_imap_username=""))
