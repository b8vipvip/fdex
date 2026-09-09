from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_ROOT = "https://www.googleapis.com/drive/v3"
_DOCS_ROOT = "https://docs.googleapis.com/v1"
_SHEETS_ROOT = "https://sheets.googleapis.com/v4"
_SLIDES_ROOT = "https://slides.googleapis.com/v1"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,240}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-google-drive.db"
KEY_PATH = _DATA_DIR / "plugin-google-drive.key"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_TEXT_BYTES = 512 * 1024
_MAX_TEXT_CHARS = 512 * 1024
_MAX_SHEETS = 10
_MAX_SHEET_ROWS = 500
_MAX_SHEET_COLUMNS = 26
_MAX_WRITE_TEXT = 30000

_GOOGLE_DOC = "application/vnd.google-apps.document"
_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
_GOOGLE_FOLDER = "application/vnd.google-apps.folder"
_TEXT_MIMES = {
    "text/plain", "text/markdown", "text/csv", "text/tab-separated-values", "text/html", "text/css",
    "application/json", "application/xml", "text/xml", "application/javascript", "application/x-javascript",
    "application/x-sh", "application/sql", "application/yaml", "text/yaml",
}


class GoogleDrivePluginError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _file_id(value: str, label: str = "file_id") -> str:
    clean = (value or "").strip()
    if not _FILE_ID_RE.fullmatch(clean):
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


def _oauth_ready() -> bool:
    return bool(fresh_settings().google_drive_oauth_ready)


class GoogleDriveStore:
    """Owner-scoped encrypted Google OAuth connection and short-lived browser-flow state."""

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

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        self._cipher()
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS google_drive_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL UNIQUE,
                    access_cipher TEXT NOT NULL,
                    refresh_cipher TEXT NOT NULL,
                    token_expires_at TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    account_email TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    permission_id TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_google_drive_owner ON google_drive_connections(owner_id);
                CREATE TABLE IF NOT EXISTS google_drive_oauth_flows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    state_hash TEXT NOT NULL UNIQUE,
                    verifier_cipher TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_google_drive_oauth_owner
                    ON google_drive_oauth_flows(owner_id, created_at DESC);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(str(value).encode("utf-8")).decode("ascii") if value else ""

    def _decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise GoogleDrivePluginError("Google Drive 插件凭据解密失败，请重新连接") from exc

    def get(self, owner_id: str, *, secret: bool = False) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM google_drive_connections WHERE owner_id=? LIMIT 1", (_owner(owner_id),)
            ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = {
            "id": int(row["id"]),
            "owner_id": str(row["owner_id"]),
            "scope": str(row["scope"]),
            "account_email": str(row["account_email"]),
            "display_name": str(row["display_name"]),
            "permission_id": str(row["permission_id"]),
            "token_expires_at": str(row["token_expires_at"]),
            "last_checked_at": str(row["last_checked_at"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "access_token_configured": bool(str(row["access_cipher"] or "")),
            "refresh_token_configured": bool(str(row["refresh_cipher"] or "")),
        }
        if secret:
            result["access_token"] = self._decrypt(str(row["access_cipher"]))
            result["refresh_token"] = self._decrypt(str(row["refresh_cipher"]))
        return result

    def save_connection(
        self,
        owner_id: str,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        scope: str,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        access = str(access_token or "").strip()
        refresh = str(refresh_token or "").strip()
        if len(access) < 16 or len(access) > 8192:
            raise GoogleDrivePluginError("Google 没有返回有效 access_token")
        if len(refresh) < 16 or len(refresh) > 8192:
            raise GoogleDrivePluginError("Google 没有返回可长期续期的 refresh_token，请重新授权")
        user = profile.get("user") if isinstance(profile.get("user"), dict) else {}
        now = _now()
        expires = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with self.db() as conn:
            conn.execute(
                """INSERT INTO google_drive_connections(
                       owner_id,access_cipher,refresh_cipher,token_expires_at,scope,account_email,
                       display_name,permission_id,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       access_cipher=excluded.access_cipher,
                       refresh_cipher=excluded.refresh_cipher,
                       token_expires_at=excluded.token_expires_at,
                       scope=excluded.scope,
                       account_email=excluded.account_email,
                       display_name=excluded.display_name,
                       permission_id=excluded.permission_id,
                       last_checked_at=excluded.last_checked_at,
                       updated_at=excluded.updated_at""",
                (
                    clean_owner,
                    self._encrypt(access),
                    self._encrypt(refresh),
                    _iso(expires),
                    str(scope or "")[:4000],
                    str(user.get("emailAddress") or "")[:320],
                    str(user.get("displayName") or "")[:320],
                    str(user.get("permissionId") or "")[:240],
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
        saved = self.get(clean_owner)
        if saved is None:
            raise GoogleDrivePluginError("Google Drive 插件连接保存失败")
        return saved

    def update_access_token(self, owner_id: str, access_token: str, expires_in: int, *, scope: str = "") -> None:
        access = str(access_token or "").strip()
        if len(access) < 16:
            raise GoogleDrivePluginError("Google 刷新 access_token 失败")
        now = _now()
        expires = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with self.db() as conn:
            if scope:
                conn.execute(
                    "UPDATE google_drive_connections SET access_cipher=?,token_expires_at=?,scope=?,last_checked_at=?,updated_at=? WHERE owner_id=?",
                    (self._encrypt(access), _iso(expires), scope[:4000], _iso(now), _iso(now), _owner(owner_id)),
                )
            else:
                conn.execute(
                    "UPDATE google_drive_connections SET access_cipher=?,token_expires_at=?,last_checked_at=?,updated_at=? WHERE owner_id=?",
                    (self._encrypt(access), _iso(expires), _iso(now), _iso(now), _owner(owner_id)),
                )

    def start_flow(self, owner_id: str) -> dict[str, str]:
        settings = fresh_settings()
        if not settings.google_drive_oauth_ready:
            raise GoogleDrivePluginError("FDEX Google OAuth 尚未配置")
        clean_owner = _owner(owner_id)
        self.init()
        state = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        flow_id = secrets.token_hex(16)
        now = _now()
        redirect_uri = settings.public_base_url.rstrip("/") + "/account/plugins/google-drive/oauth/callback"
        expires = now + timedelta(minutes=settings.fdex_google_oauth_flow_minutes)
        with self.db() as conn:
            conn.execute(
                "UPDATE google_drive_oauth_flows SET status='expired',error='superseded',verifier_cipher='' WHERE owner_id=? AND status='pending'",
                (clean_owner,),
            )
            conn.execute(
                """INSERT INTO google_drive_oauth_flows(
                       id,owner_id,state_hash,verifier_cipher,redirect_uri,status,created_at,expires_at
                   ) VALUES(?,?,?,?,?,'pending',?,?)""",
                (flow_id, clean_owner, _hash(state), self._encrypt(verifier), redirect_uri, _iso(now), _iso(expires)),
            )
            conn.execute(
                """DELETE FROM google_drive_oauth_flows WHERE owner_id=? AND id NOT IN (
                       SELECT id FROM google_drive_oauth_flows WHERE owner_id=? ORDER BY created_at DESC LIMIT 20
                   )""",
                (clean_owner, clean_owner),
            )
        params = {
            "client_id": settings.fdex_google_oauth_client_id.strip(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(settings.fdex_google_oauth_scope.split()),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent select_account",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return {"flow_id": flow_id, "authorize_url": f"{_AUTHORIZE_URL}?{urlencode(params)}"}

    def complete_flow(self, owner_id: str, *, state: str, code: str) -> dict[str, Any]:
        settings = fresh_settings()
        if not settings.google_drive_oauth_ready:
            raise GoogleDrivePluginError("FDEX Google OAuth 尚未配置")
        clean_owner = _owner(owner_id)
        state_hash = _hash(str(state or "").strip())
        clean_code = str(code or "").strip()
        if not state or not clean_code:
            raise GoogleDrivePluginError("Google OAuth 返回参数不完整")
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM google_drive_oauth_flows WHERE owner_id=? AND state_hash=?",
                (clean_owner, state_hash),
            ).fetchone()
            if row is None:
                raise GoogleDrivePluginError("Google OAuth 状态无效，请重新连接")
            if str(row["status"]) != "pending":
                raise GoogleDrivePluginError("该 Google OAuth 流程已经使用或失效")
            expires = _parse_time(str(row["expires_at"] or ""))
            if expires is None or expires <= _now():
                conn.execute(
                    "UPDATE google_drive_oauth_flows SET status='expired',error='expired',verifier_cipher='' WHERE id=?",
                    (row["id"],),
                )
                raise GoogleDrivePluginError("Google OAuth 已过期，请重新连接")
            verifier = self._decrypt(str(row["verifier_cipher"] or ""))
            redirect_uri = str(row["redirect_uri"] or "")
        token_payload = _token_request(
            {
                "client_id": settings.fdex_google_oauth_client_id.strip(),
                "client_secret": settings.fdex_google_oauth_client_secret.strip(),
                "code": clean_code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            context="Google OAuth 授权交换",
        )
        access = str(token_payload.get("access_token") or "").strip()
        refresh = str(token_payload.get("refresh_token") or "").strip()
        if not access or not refresh:
            self._fail_flow(clean_owner, state_hash, "missing_access_or_refresh_token")
            raise GoogleDrivePluginError("Google 未返回长期授权，请撤销旧授权后重新连接")
        profile = verify_google_drive_access(access)
        saved = self.save_connection(
            clean_owner,
            access_token=access,
            refresh_token=refresh,
            expires_in=int(token_payload.get("expires_in") or 3600),
            scope=str(token_payload.get("scope") or settings.fdex_google_oauth_scope),
            profile=profile,
        )
        with self.db() as conn:
            conn.execute(
                """UPDATE google_drive_oauth_flows SET status='authorized',verifier_cipher='',completed_at=?,error=''
                   WHERE owner_id=? AND state_hash=? AND status='pending'""",
                (_iso(_now()), clean_owner, state_hash),
            )
        return saved

    def _fail_flow(self, owner_id: str, state_hash: str, error: str) -> None:
        with self.db() as conn:
            conn.execute(
                "UPDATE google_drive_oauth_flows SET status='error',verifier_cipher='',error=? WHERE owner_id=? AND state_hash=?",
                ((error or "oauth_error")[:500], _owner(owner_id), state_hash),
            )

    def delete_connection(self, owner_id: str) -> int:
        self.init()
        with self.db() as conn:
            cur = conn.execute("DELETE FROM google_drive_connections WHERE owner_id=?", (_owner(owner_id),))
        return max(0, int(cur.rowcount or 0))

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        clean = _owner(owner_id)
        with self.db() as conn:
            connections = int(conn.execute("SELECT COUNT(*) FROM google_drive_connections WHERE owner_id=?", (clean,)).fetchone()[0])
            flows = int(conn.execute("SELECT COUNT(*) FROM google_drive_oauth_flows WHERE owner_id=?", (clean,)).fetchone()[0])
            conn.execute("DELETE FROM google_drive_connections WHERE owner_id=?", (clean,))
            conn.execute("DELETE FROM google_drive_oauth_flows WHERE owner_id=?", (clean,))
        return {"connections": connections, "oauth_flows": flows}


@lru_cache(maxsize=1)
def google_drive_store() -> GoogleDriveStore:
    store = GoogleDriveStore()
    store.init()
    return store


def _token_request(form: dict[str, str], *, context: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                _TOKEN_URL,
                data=form,
                headers={"Accept": "application/json", "User-Agent": "FDEX-Plugin-Runtime/1"},
            )
    except httpx.HTTPError as exc:
        raise GoogleDrivePluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    return _decode_json(response, context=context)


def _decode_json(response: httpx.Response, *, context: str) -> dict[str, Any]:
    if response.is_redirect:
        raise GoogleDrivePluginError(f"{context}返回了未允许的重定向")
    if len(response.content) > _MAX_JSON_BYTES:
        raise GoogleDrivePluginError(f"{context}返回数据过大")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleDrivePluginError(f"{context}返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise GoogleDrivePluginError(f"{context}返回格式无效")
    if response.status_code < 200 or response.status_code >= 300:
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("status") or error.get("code") or "")[:700]
        else:
            detail = str(error or payload.get("error_description") or "")[:700]
        raise GoogleDrivePluginError(f"{context}失败（HTTP {response.status_code}）{('：' + detail) if detail else ''}")
    return payload


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "FDEX-Plugin-Runtime/1",
    }


def _request_json_with_token(
    access_token: str,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=False) as client:
            response = client.request(
                method.upper(), url, headers={**_headers(access_token), "Content-Type": "application/json"},
                params=params, json=json_body,
            )
    except httpx.HTTPError as exc:
        raise GoogleDrivePluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    return _decode_json(response, context=context)


def verify_google_drive_access(access_token: str) -> dict[str, Any]:
    token = str(access_token or "").strip()
    if len(token) < 16:
        raise ValueError("Google access_token 格式无效")
    profile = _request_json_with_token(
        token,
        "GET",
        f"{_DRIVE_ROOT}/about",
        params={"fields": "user(displayName,emailAddress,permissionId),storageQuota(limit,usage)"},
        context="Google Drive 鉴权",
    )
    _request_json_with_token(
        token,
        "GET",
        f"{_DRIVE_ROOT}/files",
        params={"pageSize": 1, "spaces": "drive", "fields": "files(id,name,mimeType)"},
        context="Google Drive 文件权限检查",
    )
    return profile


def google_drive_connection_status(owner_id: str) -> dict[str, Any]:
    row = google_drive_store().get(owner_id)
    if row is None:
        ready = _oauth_ready()
        return {
            "connected": False,
            "connection_count": 0,
            "account_label": "",
            "connectable": ready,
            "state": "available" if ready else "configuration_required",
        }
    label = str(row.get("account_email") or row.get("display_name") or "Google Drive")
    return {
        "connected": True,
        "connection_count": 1,
        "account_label": label[:320],
        "connectable": _oauth_ready(),
        "state": "connected",
        "last_checked_at": str(row.get("last_checked_at") or ""),
    }


def start_google_drive_oauth(owner_id: str) -> dict[str, str]:
    return google_drive_store().start_flow(owner_id)


def complete_google_drive_oauth(owner_id: str, *, state: str, code: str) -> dict[str, Any]:
    return google_drive_store().complete_flow(owner_id, state=state, code=code)


def disconnect_google_drive(owner_id: str) -> int:
    return google_drive_store().delete_connection(owner_id)


def _access_token(owner_id: str, *, force_refresh: bool = False) -> str:
    row = google_drive_store().get(owner_id, secret=True)
    if row is None:
        raise GoogleDrivePluginError("Google Drive 插件尚未连接")
    access = str(row.get("access_token") or "")
    expires = _parse_time(str(row.get("token_expires_at") or ""))
    if not force_refresh and access and expires is not None and expires > _now() + timedelta(seconds=90):
        return access
    settings = fresh_settings()
    if not settings.google_drive_oauth_ready:
        raise GoogleDrivePluginError("Google OAuth 服务端配置缺失，无法续期访问令牌")
    refresh = str(row.get("refresh_token") or "")
    if not refresh:
        raise GoogleDrivePluginError("Google Drive refresh_token 缺失，请重新连接")
    payload = _token_request(
        {
            "client_id": settings.fdex_google_oauth_client_id.strip(),
            "client_secret": settings.fdex_google_oauth_client_secret.strip(),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        context="Google OAuth 令牌续期",
    )
    token = str(payload.get("access_token") or "").strip()
    google_drive_store().update_access_token(
        owner_id,
        token,
        int(payload.get("expires_in") or 3600),
        scope=str(payload.get("scope") or ""),
    )
    return token


def _api_request(
    owner_id: str,
    method: str,
    root: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str,
) -> dict[str, Any]:
    if root not in {_DRIVE_ROOT, _DOCS_ROOT, _SHEETS_ROOT, _SLIDES_ROOT}:
        raise ValueError("Google API root 无效")
    clean_path = "/" + str(path or "").strip().lstrip("/")
    if ".." in clean_path or "\\" in clean_path:
        raise ValueError("Google API 路径无效")
    token = _access_token(owner_id)
    try:
        return _request_json_with_token(token, method, root + clean_path, params=params, json_body=json_body, context=context)
    except GoogleDrivePluginError as exc:
        if "HTTP 401" not in str(exc):
            raise
    token = _access_token(owner_id, force_refresh=True)
    return _request_json_with_token(token, method, root + clean_path, params=params, json_body=json_body, context=context)


def _escape_drive_query(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _normalize_file(item: dict[str, Any]) -> dict[str, Any]:
    owners = item.get("owners") if isinstance(item.get("owners"), list) else []
    owner_labels = []
    for owner in owners[:5]:
        if isinstance(owner, dict):
            label = str(owner.get("emailAddress") or owner.get("displayName") or "").strip()
            if label:
                owner_labels.append(label[:320])
    capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    return {
        "id": str(item.get("id") or "")[:240],
        "name": str(item.get("name") or "")[:1000],
        "mime_type": str(item.get("mimeType") or "")[:240],
        "created_time": str(item.get("createdTime") or "")[:80],
        "modified_time": str(item.get("modifiedTime") or "")[:80],
        "size": str(item.get("size") or "")[:80],
        "web_view_link": str(item.get("webViewLink") or "")[:1500],
        "parents": [str(value)[:240] for value in (item.get("parents") if isinstance(item.get("parents"), list) else [])[:20]],
        "drive_id": str(item.get("driveId") or "")[:240],
        "trashed": bool(item.get("trashed")),
        "starred": bool(item.get("starred")),
        "owners": owner_labels,
        "can_edit": bool(capabilities.get("canEdit")),
        "can_download": bool(capabilities.get("canDownload")),
        "can_copy": bool(capabilities.get("canCopy")),
    }


def search_google_drive_files(
    owner_id: str,
    query: str = "",
    *,
    page_size: int = 50,
    page_token: str = "",
) -> dict[str, Any]:
    size = _page_size(page_size)
    clean_query = str(query or "").strip()
    if len(clean_query) > 500:
        raise ValueError("Google Drive 搜索词过长")
    q = "trashed = false"
    if clean_query:
        literal = _escape_drive_query(clean_query)
        q += f" and (name contains '{literal}' or fullText contains '{literal}')"
    params: dict[str, Any] = {
        "q": q,
        "spaces": "drive",
        "pageSize": size,
        "orderBy": "modifiedTime desc",
        "includeItemsFromAllDrives": "true",
        "fields": (
            "nextPageToken,files(id,name,mimeType,createdTime,modifiedTime,size,webViewLink,parents,driveId,"
            "trashed,starred,owners(displayName,emailAddress),capabilities(canEdit,canDownload,canCopy))"
        ),
    }
    if page_token:
        params["pageToken"] = str(page_token)[:2000]
    payload = _api_request(owner_id, "GET", _DRIVE_ROOT, "/files", params=params, context="Google Drive 文件搜索")
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    rows = [_normalize_file(item) for item in files[:size] if isinstance(item, dict)]
    return {
        "plugin_id": "google-drive",
        "query": clean_query,
        "files": rows,
        "count": len(rows),
        "next_page_token": str(payload.get("nextPageToken") or "")[:2000],
    }


def _file_metadata(owner_id: str, file_id: str) -> dict[str, Any]:
    clean_id = _file_id(file_id)
    payload = _api_request(
        owner_id,
        "GET",
        _DRIVE_ROOT,
        f"/files/{quote(clean_id, safe='')}",
        params={
            "supportsAllDrives": "true",
            "fields": (
                "id,name,mimeType,createdTime,modifiedTime,size,webViewLink,parents,driveId,trashed,starred,"
                "owners(displayName,emailAddress),capabilities(canEdit,canDownload,canCopy)"
            ),
        },
        context="Google Drive 文件元数据",
    )
    return _normalize_file(payload)


def _append_text(parts: list[str], text: str, budget: list[int]) -> None:
    if budget[0] <= 0 or not text:
        return
    chunk = str(text)[:budget[0]]
    parts.append(chunk)
    budget[0] -= len(chunk)


def _doc_structural_text(structural: Any, parts: list[str], budget: list[int]) -> None:
    if not isinstance(structural, list):
        return
    for item in structural:
        if budget[0] <= 0 or not isinstance(item, dict):
            break
        paragraph = item.get("paragraph") if isinstance(item.get("paragraph"), dict) else {}
        for element in paragraph.get("elements") if isinstance(paragraph.get("elements"), list) else []:
            if not isinstance(element, dict):
                continue
            run = element.get("textRun") if isinstance(element.get("textRun"), dict) else {}
            _append_text(parts, str(run.get("content") or ""), budget)
        table = item.get("table") if isinstance(item.get("table"), dict) else {}
        rows = table.get("tableRows") if isinstance(table.get("tableRows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cells = row.get("tableCells") if isinstance(row.get("tableCells"), list) else []
            for cell in cells:
                if isinstance(cell, dict):
                    _doc_structural_text(cell.get("content"), parts, budget)
                    _append_text(parts, "\t", budget)
            _append_text(parts, "\n", budget)
        toc = item.get("tableOfContents") if isinstance(item.get("tableOfContents"), dict) else {}
        _doc_structural_text(toc.get("content"), parts, budget)


def _doc_tab_text(tab: dict[str, Any], parts: list[str], budget: list[int]) -> None:
    props = tab.get("tabProperties") if isinstance(tab.get("tabProperties"), dict) else {}
    title = str(props.get("title") or "").strip()
    if title:
        _append_text(parts, f"\n## {title}\n", budget)
    content = tab.get("documentTab") if isinstance(tab.get("documentTab"), dict) else {}
    body = content.get("body") if isinstance(content.get("body"), dict) else {}
    _doc_structural_text(body.get("content"), parts, budget)
    children = tab.get("childTabs") if isinstance(tab.get("childTabs"), list) else []
    for child in children:
        if isinstance(child, dict):
            _doc_tab_text(child, parts, budget)


def _read_google_doc(owner_id: str, file_id: str) -> tuple[str, bool]:
    payload = _api_request(
        owner_id, "GET", _DOCS_ROOT, f"/documents/{quote(file_id, safe='')}",
        params={"includeTabsContent": "true"}, context="Google Docs 文档读取",
    )
    parts: list[str] = []
    budget = [_MAX_TEXT_CHARS]
    tabs = payload.get("tabs") if isinstance(payload.get("tabs"), list) else []
    if tabs:
        for tab in tabs:
            if isinstance(tab, dict):
                _doc_tab_text(tab, parts, budget)
    else:
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        _doc_structural_text(body.get("content"), parts, budget)
    return "".join(parts), budget[0] <= 0


def _sheet_range(title: str) -> str:
    return "'" + str(title).replace("'", "''") + f"'!A1:Z{_MAX_SHEET_ROWS}"


def _read_google_sheet(owner_id: str, file_id: str) -> tuple[str, bool]:
    meta = _api_request(
        owner_id, "GET", _SHEETS_ROOT, f"/spreadsheets/{quote(file_id, safe='')}",
        params={"fields": "properties(title),sheets(properties(sheetId,title,index,gridProperties))"},
        context="Google Sheets 元数据读取",
    )
    sheets = meta.get("sheets") if isinstance(meta.get("sheets"), list) else []
    parts: list[str] = []
    budget = [_MAX_TEXT_CHARS]
    truncated = len(sheets) > _MAX_SHEETS
    for sheet in sheets[:_MAX_SHEETS]:
        if budget[0] <= 0 or not isinstance(sheet, dict):
            truncated = True
            break
        props = sheet.get("properties") if isinstance(sheet.get("properties"), dict) else {}
        title = str(props.get("title") or "Sheet")[:500]
        _append_text(parts, f"\n## {title}\n", budget)
        range_text = _sheet_range(title)
        values = _api_request(
            owner_id,
            "GET",
            _SHEETS_ROOT,
            f"/spreadsheets/{quote(file_id, safe='')}/values/{quote(range_text, safe='')}",
            params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
            context="Google Sheets 单表读取",
        )
        rows = values.get("values") if isinstance(values.get("values"), list) else []
        if len(rows) >= _MAX_SHEET_ROWS:
            truncated = True
        for row in rows[:_MAX_SHEET_ROWS]:
            if budget[0] <= 0:
                truncated = True
                break
            cells = row if isinstance(row, list) else []
            line = "\t".join(str(cell)[:2000] for cell in cells[:_MAX_SHEET_COLUMNS])
            _append_text(parts, line + "\n", budget)
    return "".join(parts), truncated or budget[0] <= 0


def _read_google_slides(owner_id: str, file_id: str) -> tuple[str, bool]:
    payload = _api_request(
        owner_id, "GET", _SLIDES_ROOT, f"/presentations/{quote(file_id, safe='')}",
        context="Google Slides 演示文稿读取",
    )
    slides = payload.get("slides") if isinstance(payload.get("slides"), list) else []
    parts: list[str] = []
    budget = [_MAX_TEXT_CHARS]
    for index, slide in enumerate(slides, 1):
        if budget[0] <= 0:
            break
        _append_text(parts, f"\n## Slide {index}\n", budget)
        elements = slide.get("pageElements") if isinstance(slide, dict) and isinstance(slide.get("pageElements"), list) else []
        for element in elements:
            if not isinstance(element, dict):
                continue
            shape = element.get("shape") if isinstance(element.get("shape"), dict) else {}
            text = shape.get("text") if isinstance(shape.get("text"), dict) else {}
            text_elements = text.get("textElements") if isinstance(text.get("textElements"), list) else []
            for text_element in text_elements:
                if not isinstance(text_element, dict):
                    continue
                run = text_element.get("textRun") if isinstance(text_element.get("textRun"), dict) else {}
                _append_text(parts, str(run.get("content") or ""), budget)
    return "".join(parts), budget[0] <= 0


def _read_blob_text(owner_id: str, file_id: str) -> tuple[str, bool]:
    token = _access_token(owner_id)
    url = f"{_DRIVE_ROOT}/files/{quote(file_id, safe='')}"
    params = {"alt": "media", "supportsAllDrives": "true"}
    def request(current_token: str) -> httpx.Response:
        try:
            with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=False) as client:
                return client.get(
                    url,
                    headers={**_headers(current_token), "Range": f"bytes=0-{_MAX_TEXT_BYTES - 1}"},
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise GoogleDrivePluginError(f"Google Drive 文本文件读取网络请求失败：{type(exc).__name__}") from exc
    response = request(token)
    if response.status_code == 401:
        response = request(_access_token(owner_id, force_refresh=True))
    if response.is_redirect:
        raise GoogleDrivePluginError("Google Drive 文本文件读取返回了未允许的重定向")
    if response.status_code not in {200, 206}:
        try:
            _decode_json(response, context="Google Drive 文本文件读取")
        except GoogleDrivePluginError:
            raise
    content = response.content[:_MAX_TEXT_BYTES]
    truncated = response.status_code == 206 or len(response.content) >= _MAX_TEXT_BYTES
    return content.decode("utf-8", errors="replace"), truncated


def read_google_drive_file(owner_id: str, file_id: str) -> dict[str, Any]:
    clean_id = _file_id(file_id)
    meta = _file_metadata(owner_id, clean_id)
    mime = str(meta.get("mime_type") or "")
    content = ""
    truncated = False
    content_type = "metadata"
    unsupported_reason = ""
    if mime == _GOOGLE_DOC:
        content, truncated = _read_google_doc(owner_id, clean_id)
        content_type = "google-doc"
    elif mime == _GOOGLE_SHEET:
        content, truncated = _read_google_sheet(owner_id, clean_id)
        content_type = "google-sheet"
    elif mime == _GOOGLE_SLIDES:
        content, truncated = _read_google_slides(owner_id, clean_id)
        content_type = "google-slides"
    elif mime in _TEXT_MIMES or mime.startswith("text/"):
        content, truncated = _read_blob_text(owner_id, clean_id)
        content_type = "text-file"
    elif mime == _GOOGLE_FOLDER:
        unsupported_reason = "文件夹请使用搜索 Tool 枚举内容"
    else:
        unsupported_reason = "当前版本仅直接解析 Google Docs/Sheets/Slides 与 UTF-8 文本类文件；其他二进制文件返回元数据"
    return {
        "plugin_id": "google-drive",
        "file": meta,
        "content_type": content_type,
        "content": content[:_MAX_TEXT_CHARS],
        "truncated": bool(truncated),
        "unsupported_reason": unsupported_reason,
    }


def _clean_title(value: str, label: str = "标题") -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 1000 or "\x00" in clean:
        raise ValueError(f"{label}必须在 1-1000 字符之间")
    return clean


def create_google_document(owner_id: str, title: str) -> dict[str, Any]:
    clean_title = _clean_title(title, "文档标题")
    payload = _api_request(
        owner_id, "POST", _DOCS_ROOT, "/documents", json_body={"title": clean_title}, context="Google Docs 创建文档"
    )
    document_id = str(payload.get("documentId") or "")[:240]
    if not document_id:
        raise GoogleDrivePluginError("Google Docs 创建成功但没有返回 documentId")
    return {
        "plugin_id": "google-drive",
        "file_id": document_id,
        "name": str(payload.get("title") or clean_title)[:1000],
        "mime_type": _GOOGLE_DOC,
    }


def _document_end_index(owner_id: str, file_id: str) -> int:
    payload = _api_request(
        owner_id, "GET", _DOCS_ROOT, f"/documents/{quote(file_id, safe='')}", context="Google Docs 写入前读取"
    )
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    content = body.get("content") if isinstance(body.get("content"), list) else []
    indexes = [int(item.get("endIndex") or 0) for item in content if isinstance(item, dict)]
    return max(1, max(indexes, default=1) - 1)


def append_google_document_text(owner_id: str, file_id: str, text: str) -> dict[str, Any]:
    clean_id = _file_id(file_id)
    clean_text = str(text or "")
    if not clean_text or len(clean_text) > _MAX_WRITE_TEXT:
        raise ValueError(f"追加正文必须在 1-{_MAX_WRITE_TEXT} 字符之间")
    meta = _file_metadata(owner_id, clean_id)
    if str(meta.get("mime_type") or "") != _GOOGLE_DOC:
        raise ValueError("当前追加正文 Tool 只允许写入 Google Docs 文档")
    index = _document_end_index(owner_id, clean_id)
    payload = _api_request(
        owner_id,
        "POST",
        _DOCS_ROOT,
        f"/documents/{quote(clean_id, safe='')}:batchUpdate",
        json_body={"requests": [{"insertText": {"location": {"index": index}, "text": clean_text}}]},
        context="Google Docs 追加正文",
    )
    replies = payload.get("replies") if isinstance(payload.get("replies"), list) else []
    return {
        "plugin_id": "google-drive",
        "file_id": clean_id,
        "name": str(meta.get("name") or "")[:1000],
        "insert_index": index,
        "reply_count": len(replies),
    }


def rename_google_drive_file(owner_id: str, file_id: str, name: str) -> dict[str, Any]:
    clean_id = _file_id(file_id)
    clean_name = _clean_title(name, "文件名")
    payload = _api_request(
        owner_id,
        "PATCH",
        _DRIVE_ROOT,
        f"/files/{quote(clean_id, safe='')}",
        params={"supportsAllDrives": "true", "fields": "id,name,mimeType,modifiedTime,webViewLink"},
        json_body={"name": clean_name},
        context="Google Drive 重命名文件",
    )
    return {
        "plugin_id": "google-drive",
        "file_id": str(payload.get("id") or clean_id)[:240],
        "name": str(payload.get("name") or clean_name)[:1000],
        "mime_type": str(payload.get("mimeType") or "")[:240],
        "modified_time": str(payload.get("modifiedTime") or "")[:80],
        "web_view_link": str(payload.get("webViewLink") or "")[:1500],
    }
