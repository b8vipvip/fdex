from __future__ import annotations

import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_BASE_URL = "https://api.notion.com"
_API_VERSION = "2026-03-11"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_NOTION_ID_RE = re.compile(r"^[A-Fa-f0-9-]{32,36}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-notion.db"
KEY_PATH = _DATA_DIR / "plugin-notion.key"
_MAX_READ_BLOCKS = 500
_MAX_READ_CHARS = 512 * 1024
_MAX_READ_DEPTH = 8
_MAX_WRITE_TEXT = 30000


class NotionPluginError(RuntimeError):
    pass


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _token(value: str) -> str:
    clean = (value or "").strip()
    if len(clean) < 16 or len(clean) > 4096 or any(ch.isspace() for ch in clean) or "\x00" in clean:
        raise ValueError("Notion Integration Token 格式无效")
    return clean


def _notion_id(value: str, label: str) -> str:
    clean = (value or "").strip()
    if not _NOTION_ID_RE.fullmatch(clean):
        raise ValueError(f"{label} 格式无效")
    compact = clean.replace("-", "")
    if len(compact) != 32:
        raise ValueError(f"{label} 格式无效")
    return clean


def _page_size(value: int, maximum: int = 100) -> int:
    if isinstance(value, bool):
        raise ValueError("page_size 必须是整数")
    try:
        clean = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("page_size 必须是整数") from exc
    if clean < 1 or clean > maximum:
        raise ValueError(f"page_size 必须在 1-{maximum} 之间")
    return clean


class NotionCredentialStore:
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
                """CREATE TABLE IF NOT EXISTS notion_connections (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       owner_id TEXT NOT NULL UNIQUE,
                       token_cipher TEXT NOT NULL,
                       bot_id TEXT NOT NULL DEFAULT '',
                       bot_name TEXT NOT NULL DEFAULT '',
                       workspace_id TEXT NOT NULL DEFAULT '',
                       workspace_name TEXT NOT NULL DEFAULT '',
                       last_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notion_owner ON notion_connections(owner_id)")
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise NotionPluginError("Notion 插件凭据解密失败，请重新连接") from exc

    def get(self, owner_id: str, *, secret: bool = False) -> dict[str, Any] | None:
        self.init()
        clean_owner = _owner(owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM notion_connections WHERE owner_id=? LIMIT 1", (clean_owner,)).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = {
            "id": int(row["id"]),
            "owner_id": str(row["owner_id"]),
            "bot_id": str(row["bot_id"]),
            "bot_name": str(row["bot_name"]),
            "workspace_id": str(row["workspace_id"]),
            "workspace_name": str(row["workspace_name"]),
            "last_checked_at": str(row["last_checked_at"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "token_configured": bool(str(row["token_cipher"] or "")),
        }
        if secret:
            result["access_token"] = self._decrypt(str(row["token_cipher"]))
        return result

    def save_verified(self, owner_id: str, access_token: str, bot: dict[str, Any]) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        clean_token = _token(access_token)
        bot_meta = bot.get("bot") if isinstance(bot.get("bot"), dict) else {}
        bot_id = str(bot.get("id") or "")[:100]
        bot_name = str(bot.get("name") or "Notion")[:240]
        workspace_id = str(bot_meta.get("workspace_id") or "")[:100]
        workspace_name = str(bot_meta.get("workspace_name") or "")[:240]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO notion_connections(
                       owner_id,token_cipher,bot_id,bot_name,workspace_id,workspace_name,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       token_cipher=excluded.token_cipher,
                       bot_id=excluded.bot_id,
                       bot_name=excluded.bot_name,
                       workspace_id=excluded.workspace_id,
                       workspace_name=excluded.workspace_name,
                       last_checked_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP""",
                (clean_owner, self._encrypt(clean_token), bot_id, bot_name, workspace_id, workspace_name),
            )
        saved = self.get(clean_owner)
        if saved is None:
            raise NotionPluginError("Notion 插件连接保存失败")
        return saved

    def delete(self, owner_id: str) -> int:
        self.init()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM notion_connections WHERE owner_id=?", (_owner(owner_id),))
        return max(0, int(cur.rowcount or 0))

    def delete_owner(self, owner_id: str) -> int:
        return self.delete(owner_id)


@lru_cache(maxsize=1)
def notion_credential_store() -> NotionCredentialStore:
    store = NotionCredentialStore()
    store.init()
    return store


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": _API_VERSION,
        "User-Agent": "FDEX-Plugin-Runtime/1",
    }


def _decode(response: httpx.Response, *, context: str) -> dict[str, Any]:
    if response.is_redirect:
        raise NotionPluginError("Notion API 返回了未允许的重定向")
    try:
        payload = response.json()
    except ValueError as exc:
        raise NotionPluginError(f"{context}返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise NotionPluginError(f"{context}返回格式无效")
    if response.status_code < 200 or response.status_code >= 300:
        code = str(payload.get("code") or "")[:120]
        detail = str(payload.get("message") or payload.get("error") or "")[:700]
        suffix = ""
        if code:
            suffix += f" {code}"
        if detail:
            suffix += f"：{detail}"
        raise NotionPluginError(f"{context}失败（HTTP {response.status_code}）{suffix}")
    return payload


def _request_with_token(
    access_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str = "Notion API",
) -> dict[str, Any]:
    clean_path = "/" + (path or "").strip().lstrip("/")
    if ".." in clean_path or "\\" in clean_path or not clean_path.startswith("/v1/"):
        raise ValueError("Notion API 路径无效")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.request(
                method.upper(),
                f"{_BASE_URL}{clean_path}",
                headers=_headers(access_token),
                params=params,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise NotionPluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    return _decode(response, context=context)


def verify_notion_token(access_token: str) -> dict[str, Any]:
    clean_token = _token(access_token)
    bot = _request_with_token(clean_token, "GET", "/v1/users/me", context="Notion 鉴权")
    # The bot endpoint validates the token itself. Search additionally verifies the connection can
    # reach the content surface, even when the user has intentionally shared zero pages yet.
    _request_with_token(
        clean_token,
        "POST",
        "/v1/search",
        json_body={"page_size": 1, "sort": {"direction": "descending", "timestamp": "last_edited_time"}},
        context="Notion 内容权限检查",
    )
    return bot


def connect_notion(owner_id: str, access_token: str) -> dict[str, Any]:
    bot = verify_notion_token(access_token)
    return notion_credential_store().save_verified(owner_id, access_token, bot)


def disconnect_notion(owner_id: str) -> int:
    return notion_credential_store().delete(owner_id)


def notion_connection_status(owner_id: str) -> dict[str, Any]:
    row = notion_credential_store().get(owner_id)
    if row is None:
        return {"connected": False, "connection_count": 0, "account_label": "", "connectable": True, "state": "available"}
    label = str(row.get("workspace_name") or row.get("bot_name") or "Notion")
    return {
        "connected": True,
        "connection_count": 1,
        "account_label": label[:240],
        "connectable": True,
        "state": "connected",
        "last_checked_at": str(row.get("last_checked_at") or ""),
    }


def _access_token(owner_id: str) -> str:
    row = notion_credential_store().get(owner_id, secret=True)
    if row is None:
        raise NotionPluginError("Notion 插件尚未连接")
    return _token(str(row.get("access_token") or ""))


def _request(
    owner_id: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str = "Notion API",
) -> dict[str, Any]:
    return _request_with_token(
        _access_token(owner_id),
        method,
        path,
        params=params,
        json_body=json_body,
        context=context,
    )


def _rich_plain(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        plain = item.get("plain_text")
        if isinstance(plain, str):
            parts.append(plain)
            continue
        text = item.get("text") if isinstance(item.get("text"), dict) else {}
        content = text.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


def _page_title(payload: dict[str, Any]) -> str:
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    for prop in properties.values():
        if isinstance(prop, dict) and str(prop.get("type") or "") == "title":
            title = _rich_plain(prop.get("title"))
            if title:
                return title[:1000]
    title = _rich_plain(payload.get("title"))
    return title[:1000]


def _parent_summary(payload: dict[str, Any]) -> tuple[str, str]:
    parent = payload.get("parent") if isinstance(payload.get("parent"), dict) else {}
    parent_type = str(parent.get("type") or "")[:80]
    parent_id = ""
    if parent_type:
        raw = parent.get(parent_type)
        if isinstance(raw, str):
            parent_id = raw
        elif isinstance(raw, dict):
            parent_id = str(raw.get("id") or "")
    return parent_type, parent_id[:100]


def _normalize_object(item: dict[str, Any]) -> dict[str, Any]:
    object_type = str(item.get("object") or "")[:40]
    parent_type, parent_id = _parent_summary(item)
    return {
        "object": object_type,
        "id": str(item.get("id") or "")[:100],
        "title": _page_title(item),
        "url": str(item.get("url") or "")[:1200],
        "created_time": str(item.get("created_time") or "")[:80],
        "last_edited_time": str(item.get("last_edited_time") or "")[:80],
        "in_trash": bool(item.get("in_trash")),
        "parent_type": parent_type,
        "parent_id": parent_id,
    }


def search_notion(
    owner_id: str,
    query: str = "",
    *,
    object_filter: str = "",
    page_size: int = 50,
    start_cursor: str = "",
) -> dict[str, Any]:
    size = _page_size(page_size)
    clean_query = str(query or "").strip()
    if len(clean_query) > 500:
        raise ValueError("Notion 搜索词过长")
    clean_filter = str(object_filter or "").strip().lower()
    if clean_filter not in {"", "page", "data_source"}:
        raise ValueError("object_filter 只能是 page/data_source")
    body: dict[str, Any] = {
        "page_size": size,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    }
    if clean_query:
        body["query"] = clean_query
    if clean_filter:
        body["filter"] = {"property": "object", "value": clean_filter}
    if start_cursor:
        body["start_cursor"] = str(start_cursor)[:200]
    payload = _request(owner_id, "POST", "/v1/search", json_body=body, context="Notion 搜索")
    raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
    results = [_normalize_object(item) for item in raw_results[:size] if isinstance(item, dict)]
    return {
        "plugin_id": "notion",
        "query": clean_query,
        "object_filter": clean_filter,
        "results": results,
        "count": len(results),
        "has_more": bool(payload.get("has_more")),
        "next_cursor": str(payload.get("next_cursor") or "")[:200],
    }


def query_notion_data_source(
    owner_id: str,
    data_source_id: str,
    *,
    page_size: int = 50,
    start_cursor: str = "",
) -> dict[str, Any]:
    clean_id = _notion_id(data_source_id, "data_source_id")
    size = _page_size(page_size)
    body: dict[str, Any] = {"page_size": size, "result_type": "page"}
    if start_cursor:
        body["start_cursor"] = str(start_cursor)[:200]
    payload = _request(
        owner_id,
        "POST",
        f"/v1/data_sources/{quote(clean_id, safe='')}/query",
        json_body=body,
        context="Notion Data Source 查询",
    )
    raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
    results = [_normalize_object(item) for item in raw_results[:size] if isinstance(item, dict)]
    return {
        "plugin_id": "notion",
        "data_source_id": clean_id,
        "results": results,
        "count": len(results),
        "has_more": bool(payload.get("has_more")),
        "next_cursor": str(payload.get("next_cursor") or "")[:200],
    }


def _block_text(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    value = block.get(block_type) if isinstance(block.get(block_type), dict) else {}
    if block_type in {"child_page", "child_database"}:
        return str(value.get("title") or "")
    if block_type == "equation":
        return str(value.get("expression") or "")
    text = _rich_plain(value.get("rich_text"))
    if block_type == "to_do" and text:
        text = f"[{'x' if bool(value.get('checked')) else ' '}] {text}"
    elif block_type == "bulleted_list_item" and text:
        text = f"• {text}"
    elif block_type == "numbered_list_item" and text:
        text = f"1. {text}"
    elif block_type in {"heading_1", "heading_2", "heading_3", "heading_4"} and text:
        level = int(block_type[-1]) if block_type[-1].isdigit() else 1
        text = f"{'#' * level} {text}"
    elif block_type == "quote" and text:
        text = f"> {text}"
    elif block_type == "code" and text:
        language = str(value.get("language") or "")
        text = f"```{language}\n{text}\n```"
    if not text and block_type in {"bookmark", "embed", "link_preview"}:
        text = str(value.get("url") or "")
    return text


def _read_children(
    owner_id: str,
    block_id: str,
    *,
    depth: int,
    state: dict[str, Any],
) -> None:
    if depth > _MAX_READ_DEPTH or state["truncated"]:
        state["truncated"] = True
        return
    cursor = ""
    while not state["truncated"]:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        payload = _request(
            owner_id,
            "GET",
            f"/v1/blocks/{quote(block_id, safe='')}/children",
            params=params,
            context="Notion 页面内容读取",
        )
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            state["block_count"] += 1
            if state["block_count"] > _MAX_READ_BLOCKS:
                state["truncated"] = True
                break
            text = _block_text(raw)
            if text:
                line = f"{'  ' * depth}{text}"
                remaining = _MAX_READ_CHARS - state["chars"]
                if remaining <= 0:
                    state["truncated"] = True
                    break
                clipped = line[:remaining]
                state["lines"].append(clipped)
                state["chars"] += len(clipped) + 1
                if len(clipped) < len(line):
                    state["truncated"] = True
                    break
            if bool(raw.get("has_children")) and not state["truncated"]:
                child_id = str(raw.get("id") or "")
                if child_id:
                    _read_children(owner_id, child_id, depth=depth + 1, state=state)
        if state["truncated"] or not bool(payload.get("has_more")):
            break
        cursor = str(payload.get("next_cursor") or "")
        if not cursor:
            break


def read_notion_page(owner_id: str, page_id: str) -> dict[str, Any]:
    clean_id = _notion_id(page_id, "page_id")
    page = _request(owner_id, "GET", f"/v1/pages/{quote(clean_id, safe='')}", context="Notion 页面读取")
    state: dict[str, Any] = {"lines": [], "chars": 0, "block_count": 0, "truncated": False}
    _read_children(owner_id, clean_id, depth=0, state=state)
    normalized = _normalize_object(page)
    return {
        "plugin_id": "notion",
        **normalized,
        "content": "\n".join(state["lines"]),
        "block_count": int(state["block_count"]),
        "truncated": bool(state["truncated"]),
    }


def _text_blocks(text: str) -> list[dict[str, Any]]:
    clean = str(text or "")
    if not clean or len(clean) > _MAX_WRITE_TEXT:
        raise ValueError(f"Notion 正文必须在 1-{_MAX_WRITE_TEXT} 字符之间")
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
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        }
        for chunk in chunks
        if chunk
    ]


def create_notion_page(
    owner_id: str,
    parent_page_id: str,
    title: str,
    *,
    text: str = "",
) -> dict[str, Any]:
    clean_parent = _notion_id(parent_page_id, "parent_page_id")
    clean_title = str(title or "").strip()
    if not clean_title or len(clean_title) > 1000:
        raise ValueError("Notion 页面标题必须在 1-1000 字符之间")
    body: dict[str, Any] = {
        "parent": {"page_id": clean_parent},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": clean_title}}],
            }
        },
    }
    if text:
        body["children"] = _text_blocks(text)
    payload = _request(owner_id, "POST", "/v1/pages", json_body=body, context="Notion 页面创建")
    normalized = _normalize_object(payload)
    return {"plugin_id": "notion", **normalized, "parent_page_id": clean_parent}


def append_notion_page_text(owner_id: str, page_id: str, text: str) -> dict[str, Any]:
    clean_id = _notion_id(page_id, "page_id")
    blocks = _text_blocks(text)
    payload = _request(
        owner_id,
        "PATCH",
        f"/v1/blocks/{quote(clean_id, safe='')}/children",
        json_body={"children": blocks, "position": {"type": "end"}},
        context="Notion 页面追加",
    )
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    return {
        "plugin_id": "notion",
        "page_id": clean_id,
        "created_blocks": [str(item.get("id") or "")[:100] for item in results if isinstance(item, dict)],
        "count": len(results),
    }


def update_notion_page_title(owner_id: str, page_id: str, title: str) -> dict[str, Any]:
    clean_id = _notion_id(page_id, "page_id")
    clean_title = str(title or "").strip()
    if not clean_title or len(clean_title) > 1000:
        raise ValueError("Notion 页面标题必须在 1-1000 字符之间")
    current = _request(owner_id, "GET", f"/v1/pages/{quote(clean_id, safe='')}", context="Notion 页面读取")
    properties = current.get("properties") if isinstance(current.get("properties"), dict) else {}
    title_key = next(
        (str(key) for key, value in properties.items() if isinstance(value, dict) and str(value.get("type") or "") == "title"),
        "",
    )
    if not title_key:
        raise NotionPluginError("当前页面没有可更新的 title 属性")
    payload = _request(
        owner_id,
        "PATCH",
        f"/v1/pages/{quote(clean_id, safe='')}",
        json_body={
            "properties": {
                title_key: {"title": [{"type": "text", "text": {"content": clean_title}}]},
            }
        },
        context="Notion 页面标题更新",
    )
    normalized = _normalize_object(payload)
    return {"plugin_id": "notion", **normalized}
