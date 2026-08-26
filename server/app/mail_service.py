from __future__ import annotations

import imaplib
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import Settings, fresh_settings


class MailServiceError(RuntimeError):
    pass


def _smtp_connection(cfg: Settings):
    context = ssl.create_default_context()
    smtp = None
    try:
        if cfg.fdex_smtp_ssl:
            smtp = smtplib.SMTP_SSL(
                cfg.fdex_smtp_host.strip(),
                cfg.fdex_smtp_port,
                timeout=cfg.fdex_smtp_timeout_seconds,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                cfg.fdex_smtp_host.strip(),
                cfg.fdex_smtp_port,
                timeout=cfg.fdex_smtp_timeout_seconds,
            )
            if cfg.fdex_smtp_starttls:
                smtp.starttls(context=context)
        if cfg.fdex_smtp_username.strip():
            smtp.login(cfg.fdex_smtp_username.strip(), cfg.fdex_smtp_password)
        return smtp
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        if smtp is not None:
            try:
                smtp.close()
            except (OSError, smtplib.SMTPException):
                pass
        raise MailServiceError("SMTP 连接、TLS 或登录失败") from exc


def send_message(message: EmailMessage, *, settings: Settings | None = None) -> None:
    """Send one prepared message without exposing SMTP credentials to callers."""
    cfg = settings or fresh_settings()
    if not cfg.smtp_ready:
        raise MailServiceError("SMTP 尚未配置完整")
    smtp = _smtp_connection(cfg)
    try:
        smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailServiceError("SMTP 邮件发送失败") from exc
    finally:
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            try:
                smtp.close()
            except (OSError, smtplib.SMTPException):
                pass


def send_test_email(recipient: str, *, settings: Settings | None = None) -> dict[str, object]:
    cfg = settings or fresh_settings()
    if not cfg.smtp_ready:
        raise MailServiceError("SMTP 尚未配置完整")
    target = (recipient or "").strip()
    if not target or "@" not in target or len(target) > 254:
        raise MailServiceError("测试收件邮箱格式无效")

    message = EmailMessage()
    message["Subject"] = "FDEX 邮件服务测试"
    message["From"] = formataddr((cfg.fdex_smtp_from_name.strip() or "FDEX", cfg.fdex_smtp_from_email.strip()))
    message["To"] = target
    message.set_content(
        "这是一封来自 FDEX 中心服务端的 SMTP 测试邮件。\n\n"
        "收到此邮件说明发件服务器、认证和发件人配置可用。\n"
    )
    send_message(message, settings=cfg)
    return {"ok": True, "recipient": target, "host": cfg.fdex_smtp_host.strip(), "port": cfg.fdex_smtp_port}


def test_imap_connection(*, settings: Settings | None = None) -> dict[str, object]:
    cfg = settings or fresh_settings()
    if not cfg.imap_ready:
        raise MailServiceError("IMAP 尚未配置完整")
    context = ssl.create_default_context()
    client: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
    try:
        if cfg.fdex_imap_ssl:
            client = imaplib.IMAP4_SSL(
                cfg.fdex_imap_host.strip(),
                cfg.fdex_imap_port,
                ssl_context=context,
                timeout=cfg.fdex_imap_timeout_seconds,
            )
        else:
            client = imaplib.IMAP4(
                cfg.fdex_imap_host.strip(),
                cfg.fdex_imap_port,
                timeout=cfg.fdex_imap_timeout_seconds,
            )
            if cfg.fdex_imap_starttls:
                client.starttls(ssl_context=context)
        client.login(cfg.fdex_imap_username.strip(), cfg.fdex_imap_password)
        status, data = client.select(cfg.fdex_imap_mailbox.strip() or "INBOX", readonly=True)
        if status != "OK":
            raise MailServiceError("IMAP 登录成功，但无法打开指定邮箱目录")
        total = 0
        if data and data[0]:
            try:
                total = int(data[0])
            except (TypeError, ValueError):
                total = 0
        unseen = 0
        search_status, search_data = client.search(None, "UNSEEN")
        if search_status == "OK" and search_data and search_data[0]:
            unseen = len(search_data[0].split())
        return {
            "ok": True,
            "host": cfg.fdex_imap_host.strip(),
            "port": cfg.fdex_imap_port,
            "mailbox": cfg.fdex_imap_mailbox.strip() or "INBOX",
            "messages": total,
            "unseen": unseen,
        }
    except MailServiceError:
        raise
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise MailServiceError("IMAP 连接、TLS 或登录失败") from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
