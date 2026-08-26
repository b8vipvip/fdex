from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr

from app.config import Settings, fresh_settings
from app.mail_service import MailServiceError, send_message


class AuthEmailUnavailable(RuntimeError):
    pass


def send_password_reset_code(email: str, code: str, *, settings: Settings | None = None) -> None:
    cfg = settings or fresh_settings()
    if not cfg.smtp_ready:
        raise AuthEmailUnavailable("FDEX password-reset email is not configured")

    message = EmailMessage()
    message["Subject"] = "FDEX 密码重置验证码"
    message["From"] = formataddr((cfg.fdex_smtp_from_name.strip() or "FDEX", cfg.fdex_smtp_from_email.strip()))
    message["To"] = email.strip()
    message.set_content(
        "你的 FDEX 密码重置验证码是：\n\n"
        f"{code}\n\n"
        f"验证码 {cfg.fdex_auth_reset_code_minutes} 分钟内有效。"
        "如果不是你本人操作，请忽略本邮件，不要把验证码告诉任何人。\n"
    )
    try:
        send_message(message, settings=cfg)
    except MailServiceError as exc:
        raise AuthEmailUnavailable("FDEX password-reset email delivery failed") from exc
