from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_BASE_URL = "https://open.feishu.cn"
_API_ROOT = f"{_BASE_URL}/open-apis"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,200}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,240}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-feishu.db"
KEY_PATH = _DATA_DIR / "plugin-feishu.key"
_MAX_DOC_TEXT = 512 * 1024
_MAX_WRITE_TEXT = 30000


class FeishuPluginError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _app_id(value: str) -> str:
    clean = (value or "").strip()
    if not _APP_ID_RE.fullmatch(clean):
        raise ValueError("飞书 App ID 格式无效")
    return clean


def _secret(value: str) -> str:
    clean = (value or "").strip()
    if len(clean) < 8 or len(clean) > 4096:
        raise ValueError("飞书 App Secret 格式无效")
    return clean


def _opaque_id(value: str, label: str) -> str:
    clean = (value or "").strip()
    if not _ID_RE.fullmatch(clean):
        raise ValueError(f"{label} 格式无效")
    return clean


def _page_size(value: int, maximum: int) -> int:
    try:
        clean = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("page_size 必须是整数") from exc
    if clean < 1 or clean > maximum:
        raise ValueError(f"page_size 必须在 1-{maximum} 之间")
    return clean


class FeishuCredentialStore:
    def __init__(self, db_path: Path = DB_PATH, key_path: Path = KEY_PATH) -> None:
        self.db_path = db_path.resolve()
        self.key_path = key_path.resolve()
        self._fernet: Fernet | None = None

    def _cipher(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.key_path.parent, 0o700)
        except OSError:
            pass
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, key + b"\n")
            finally:
                os.close(fd)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = Fernet(key)
        return self._fernet

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS feishu_connections (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       owner_id TEXT NOT NULL UNIQUE,
                       app_id TEXT NOT NULL,
                       app_secret_cipher TEXT NOT NULL,
                       token_cipher TEXT NOT NULL DEFAULT '',
                       token_expires_at TEXT NOT NULL DEFAULT '',
                       last_checked_at TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feishu_owner ON feishu_connections(owner_id)")
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def _decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise FeishuPluginError("飞书插件凭据解密失败，请重新连接") from exc

    def get(self, owner_id: str, *, secret: bool = False) -> dict[str, Any] | None:
        self.init()
        clean_owner = _owner(owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM feishu_connections WHERE owner_id=? LIMIT 1", (clean_owner,)).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = {
            "id": int(row["id"]),
            "owner_id": str(row["owner_id"]),
            "app_id": str(row["app_id"]),
            "token_expires_at": str(row["token_expires_at"]),
            "last_checked_at": str(row["last_checked_at"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "secret_configured": bool(str(row["app_secret_cipher"] or "")),
        }
        if secret:
            result["app_secret"] = self._decrypt(str(row["app_secret_cipher"]))
            result["tenant_access_token"] = self._decrypt(str(row["token_cipher"]))
        return result

    def save_verified(
        self,
        owner_id: str,
        *,
        app_id: str,
        app_secret: str,
        tenant_access_token: str,
        expires_in: int,
    ) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        clean_app_id = _app_id(app_id)
        clean_secret = _secret(app_secret)
        token = (tenant_access_token or "").strip()
        if len(token) < 8 or len(token) > 8192:
            raise FeishuPluginError("飞书没有返回有效 tenant_access_token")
        now = _now()
        expires = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO feishu_connections(
                       owner_id,app_id,app_secret_cipher,token_cipher,token_expires_at,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       app_id=excluded.app_id,
                       app_secret_cipher=excluded.app_secret_cipher,
                       token_cipher=excluded.token_cipher,
                       token_expires_at=excluded.token_expires_at,
                       last_checked_at=excluded.last_checked_at,
                       updated_at=excluded.updated_at""",
                (
                    clean_owner,
                    clean_app_id,
                    self._encrypt(clean_secret),
                    self._encrypt(token),
                    _iso(expires),
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
        saved = self.get(clean_owner)
        if saved is None:
            raise FeishuPluginError("飞书插件连接保存失败")
        return saved

    def cache_token(self, owner_id: str, token: str, expires_in: int) -> None:
        clean_owner = _owner(owner_id)
        clean_token = (token or "").strip()
        if len(clean_token) < 8:
            raise FeishuPluginError("飞书 tenant_access_token 无效")
        now = _now()
        expires = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE feishu_connections SET token_cipher=?,token_expires_at=?,last_checked_at=?,updated_at=? WHERE owner_id=?",
                (self._encrypt(clean_token), _iso(expires), _iso(now), _iso(now), clean_owner),
            )

    def delete(self, owner_id: str) -> int:
        self.init()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM feishu_connections WHERE owner_id=?", (_owner(owner_id),))
        return max(0, int(cur.rowcount or 0))

    def delete_owner(self, owner_id: str) -> int:
        return self.delete(owner_id)


@lru_cache(maxsize=1)
def feishu_credential_store() -> FeishuCredentialStore:
    store = FeishuCredentialStore()
    store.init()
    return store


def _decode(response: httpx.Response, *, context: str) -> dict[str, Any]:
    if response.is_redirect:
        raise FeishuPluginError("飞书 OpenAPI 返回了未允许的重定向")
    try:
        payload = response.json()
    except ValueError as exc:
        raise FeishuPluginError(f"{context}返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise FeishuPluginError(f"{context}返回格式无效")
    if response.status_code < 200 or response.status_code >= 300:
        detail = str(payload.get("msg") or payload.get("message") or "")[:500]
        raise FeishuPluginError(f"{context}失败（HTTP {response.status_code}）{('：' + detail) if detail else ''}")
    code = payload.get("code", 0)
    try:
        code_value = int(code or 0)
    except (TypeError, ValueError):
        code_value = -1
    if code_value != 0:
        detail = str(payload.get("msg") or payload.get("message") or "")[:500]
        raise FeishuPluginError(f"{context}失败（code {code_value}）{('：' + detail) if detail else ''}")
    return payload


def verify_feishu_credentials(app_id: str, app_secret: str) -> dict[str, Any]:
    clean_app_id = _app_id(app_id)
    clean_secret = _secret(app_secret)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"{_API_ROOT}/auth/v3/tenant_access_token/internal",
                headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "FDEX-Plugin-Runtime/1"},
                json={"app_id": clean_app_id, "app_secret": clean_secret},
            )
    except httpx.HTTPError as exc:
        raise FeishuPluginError(f"飞书鉴权网络请求失败：{type(exc).__name__}") from exc
    payload = _decode(response, context="飞书鉴权")
    token = str(payload.get("tenant_access_token") or "").strip()
    if not token:
        raise FeishuPluginError("飞书鉴权成功但没有返回 tenant_access_token")
    try:
        expires = int(payload.get("expire") or 0)
    except (TypeError, ValueError):
        expires = 0
    return {"app_id": clean_app_id, "tenant_access_token": token, "expire": max(60, expires)}


def connect_feishu(owner_id: str, app_id: str, app_secret: str) -> dict[str, Any]:
    verified = verify_feishu_credentials(app_id, app_secret)
    return feishu_credential_store().save_verified(
        owner_id,
        app_id=verified["app_id"],
        app_secret=app_secret,
        tenant_access_token=verified["tenant_access_token"],
        expires_in=int(verified["expire"]),
    )


def disconnect_feishu(owner_id: str) -> int:
    return feishu_credential_store().delete(owner_id)


def feishu_connection_status(owner_id: str) -> dict[str, Any]:
    row = feishu_credential_store().get(owner_id)
    if row is None:
        return {"connected": False, "connection_count": 0, "account_label": "", "connectable": True, "state": "available"}
    return {
        "connected": True,
        "connection_count": 1,
        "account_label": str(row.get("app_id") or ""),
        "connectable": True,
        "state": "connected",
        "last_checked_at": str(row.get("last_checked_at") or ""),
    }


def _tenant_token(owner_id: str, *, force: bool = False) -> str:
    row = feishu_credential_store().get(owner_id, secret=True)
    if row is None:
        raise FeishuPluginError("飞书插件尚未连接")
    cached = str(row.get("tenant_access_token") or "")
    expires_at = str(row.get("token_expires_at") or "")
    if not force and cached and expires_at:
        try:
            if _parse_time(expires_at) > _now() + timedelta(seconds=90):
                return cached
        except ValueError:
            pass
    verified = verify_feishu_credentials(str(row.get("app_id") or ""), str(row.get("app_secret") or ""))
    token = str(verified["tenant_access_token"])
    feishu_credential_store().cache_token(owner_id, token, int(verified["expire"]))
    return token


def _request(
    owner_id: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_path = "/" + (path or "").strip().lstrip("/")
    if ".." in clean_path or "\\" in clean_path:
        raise ValueError("飞书 API 路径无效")
    token = _tenant_token(owner_id)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.request(
                method.upper(),
                f"{_API_ROOT}{clean_path}",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "FDEX-Plugin-Runtime/1",
                },
                params=params,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise FeishuPluginError(f"飞书 OpenAPI 网络请求失败：{type(exc).__name__}") from exc
    if response.status_code == 401:
        token = _tenant_token(owner_id, force=True)
        try:
            with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
                response = client.request(
                    method.upper(),
                    f"{_API_ROOT}{clean_path}",
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json; charset=utf-8",
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FDEX-Plugin-Runtime/1",
                    },
                    params=params,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise FeishuPluginError(f"飞书 OpenAPI 重试失败：{type(exc).__name__}") from exc
    return _decode(response, context="飞书 OpenAPI")


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FeishuPluginError("飞书 OpenAPI data 返回格式无效")
    return data


def list_feishu_chats(owner_id: str, *, page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    size = _page_size(page_size, 100)
    params: dict[str, Any] = {"user_id_type": "open_id", "page_size": size}
    if page_token:
        params["page_token"] = str(page_token)[:2000]
    data = _data(_request(owner_id, "GET", "/im/v1/chats", params=params))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    normalized = []
    for item in items[:size]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "chat_id": str(item.get("chat_id") or "")[:240],
            "name": str(item.get("name") or "")[:300],
            "description": str(item.get("description") or "")[:1000],
            "owner_id": str(item.get("owner_id") or "")[:240],
            "external": bool(item.get("external")),
        })
    return {
        "plugin_id": "feishu",
        "items": normalized,
        "count": len(normalized),
        "has_more": bool(data.get("has_more")),
        "page_token": str(data.get("page_token") or "")[:2000],
    }


def list_feishu_messages(
    owner_id: str,
    chat_id: str,
    *,
    page_size: int = 20,
    page_token: str = "",
) -> dict[str, Any]:
    clean_chat = _opaque_id(chat_id, "chat_id")
    size = _page_size(page_size, 50)
    params: dict[str, Any] = {
        "container_id_type": "chat",
        "container_id": clean_chat,
        "sort_type": "ByCreateTimeDesc",
        "page_size": size,
        "user_id_type": "open_id",
        "card_msg_content_type": "user_card_content",
    }
    if page_token:
        params["page_token"] = str(page_token)[:2000]
    data = _data(_request(owner_id, "GET", "/im/v1/messages", params=params))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    normalized = []
    for item in items[:size]:
        if not isinstance(item, dict):
            continue
        sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
        body = item.get("body") if isinstance(item.get("body"), dict) else {}
        normalized.append({
            "message_id": str(item.get("message_id") or "")[:240],
            "root_id": str(item.get("root_id") or "")[:240],
            "parent_id": str(item.get("parent_id") or "")[:240],
            "msg_type": str(item.get("msg_type") or "")[:80],
            "create_time": str(item.get("create_time") or "")[:80],
            "sender_id": str(sender.get("id") or "")[:240],
            "sender_type": str(sender.get("sender_type") or "")[:80],
            "content": str(body.get("content") or "")[:50000],
        })
    return {
        "plugin_id": "feishu",
        "chat_id": clean_chat,
        "items": normalized,
        "count": len(normalized),
        "has_more": bool(data.get("has_more")),
        "page_token": str(data.get("page_token") or "")[:2000],
    }


def send_feishu_text(owner_id: str, receive_id: str, text: str, *, receive_id_type: str = "chat_id") -> dict[str, Any]:
    clean_type = (receive_id_type or "chat_id").strip().lower()
    if clean_type not in {"chat_id", "open_id", "user_id", "union_id", "email"}:
        raise ValueError("receive_id_type 不受支持")
    clean_receive = (receive_id or "").strip()
    if not clean_receive or len(clean_receive) > 320 or "\x00" in clean_receive:
        raise ValueError("receive_id 格式无效")
    clean_text = str(text or "")
    if not clean_text or len(clean_text) > 30000:
        raise ValueError("消息正文必须在 1-30000 字符之间")
    data = _data(_request(
        owner_id,
        "POST",
        "/im/v1/messages",
        params={"receive_id_type": clean_type},
        json_body={"receive_id": clean_receive, "msg_type": "text", "content": json.dumps({"text": clean_text}, ensure_ascii=False)},
    ))
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    return {
        "plugin_id": "feishu",
        "message_id": str(message.get("message_id") or "")[:240],
        "chat_id": str(message.get("chat_id") or "")[:240],
        "create_time": str(message.get("create_time") or "")[:80],
        "msg_type": str(message.get("msg_type") or "text")[:80],
    }


def read_feishu_document(owner_id: str, document_id: str) -> dict[str, Any]:
    clean_doc = _opaque_id(document_id, "document_id")
    data = _data(_request(owner_id, "GET", f"/docx/v1/documents/{quote(clean_doc, safe='')}/raw_content"))
    content = str(data.get("content") or "")
    truncated = len(content) > _MAX_DOC_TEXT
    return {
        "plugin_id": "feishu",
        "document_id": clean_doc,
        "content": content[:_MAX_DOC_TEXT],
        "truncated": truncated,
    }


def create_feishu_document(owner_id: str, title: str, *, folder_token: str = "") -> dict[str, Any]:
    clean_title = (title or "").strip()
    if not clean_title or len(clean_title) > 500:
        raise ValueError("文档标题必须在 1-500 字符之间")
    body: dict[str, Any] = {"title": clean_title}
    if folder_token:
        body["folder_token"] = _opaque_id(folder_token, "folder_token")
    data = _data(_request(owner_id, "POST", "/docx/v1/documents", json_body=body))
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    return {
        "plugin_id": "feishu",
        "document_id": str(document.get("document_id") or "")[:240],
        "revision_id": document.get("revision_id"),
        "title": str(document.get("title") or clean_title)[:500],
    }


def _text_blocks(text: str) -> list[dict[str, Any]]:
    clean = str(text or "")
    if not clean or len(clean) > _MAX_WRITE_TEXT:
        raise ValueError(f"追加正文必须在 1-{_MAX_WRITE_TEXT} 字符之间")
    chunks: list[str] = []
    remaining = clean
    while remaining:
        if len(remaining) <= 1800:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, 1800)
        if split_at < 900:
            split_at = 1800
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return [
        {
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": chunk}}]},
        }
        for chunk in chunks
        if chunk
    ]


def append_feishu_document_text(
    owner_id: str,
    document_id: str,
    text: str,
    *,
    parent_block_id: str = "",
) -> dict[str, Any]:
    clean_doc = _opaque_id(document_id, "document_id")
    clean_parent = _opaque_id(parent_block_id or clean_doc, "parent_block_id")
    blocks = _text_blocks(text)
    data = _data(_request(
        owner_id,
        "POST",
        f"/docx/v1/documents/{quote(clean_doc, safe='')}/blocks/{quote(clean_parent, safe='')}/children",
        json_body={"children": blocks},
    ))
    children = data.get("children") if isinstance(data.get("children"), list) else []
    return {
        "plugin_id": "feishu",
        "document_id": clean_doc,
        "parent_block_id": clean_parent,
        "created_blocks": [str(item.get("block_id") or "")[:240] for item in children if isinstance(item, dict)],
        "count": len(children),
    }
