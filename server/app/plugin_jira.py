from __future__ import annotations

import json
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


_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
_ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
_API_ROOT = "https://api.atlassian.com/ex/jira"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_CLOUD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-jira.db"
KEY_PATH = _DATA_DIR / "plugin-jira.key"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_TEXT = 30000
_MAX_JQL = 5000


class JiraPluginError(RuntimeError):
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


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _cloud_id(value: str) -> str:
    clean = str(value or "").strip()
    if not _CLOUD_ID_RE.fullmatch(clean):
        raise ValueError("Jira cloud_id 格式无效")
    return clean


def _ref(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not _REF_RE.fullmatch(clean):
        raise ValueError(f"{label} 格式无效")
    return clean


def _bounded_text(value: str, label: str, *, required: bool = False, limit: int = _MAX_TEXT) -> str:
    clean = str(value or "")
    if required and not clean.strip():
        raise ValueError(f"{label} 不能为空")
    if "\x00" in clean or len(clean) > limit:
        raise ValueError(f"{label} 过长或包含无效字符")
    return clean


def _bounded_int(value: int, label: str, *, minimum: int = 1, maximum: int = 100) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是整数")
    try:
        clean = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是整数") from exc
    if clean < minimum or clean > maximum:
        raise ValueError(f"{label} 必须在 {minimum}-{maximum} 之间")
    return clean


def _scope_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())[:4000]
    return " ".join(str(value or "").replace(",", " ").split())[:4000]


def _scope_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = [str(item).strip() for item in value]
    else:
        raw = str(value or "").replace(",", " ").split()
    return list(dict.fromkeys(item for item in raw if item))


class JiraStore:
    """Encrypted owner OAuth grant plus current accessible Jira sites and browser state."""

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
                CREATE TABLE IF NOT EXISTS jira_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL UNIQUE,
                    access_cipher TEXT NOT NULL,
                    refresh_cipher TEXT NOT NULL,
                    token_expires_at TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jira_owner ON jira_connections(owner_id);
                CREATE TABLE IF NOT EXISTS jira_sites (
                    owner_id TEXT NOT NULL,
                    cloud_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, cloud_id)
                );
                CREATE INDEX IF NOT EXISTS idx_jira_sites_owner ON jira_sites(owner_id, name);
                CREATE TABLE IF NOT EXISTS jira_oauth_flows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    state_hash TEXT NOT NULL UNIQUE,
                    redirect_uri TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_jira_oauth_owner
                    ON jira_oauth_flows(owner_id, created_at DESC);
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
            raise JiraPluginError("Jira 插件凭据解密失败，请重新连接") from exc

    def get(self, owner_id: str, *, secret: bool = False) -> dict[str, Any] | None:
        self.init()
        clean_owner = _owner(owner_id)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM jira_connections WHERE owner_id=? LIMIT 1", (clean_owner,)).fetchone()
            site_count = int(conn.execute("SELECT COUNT(*) FROM jira_sites WHERE owner_id=?", (clean_owner,)).fetchone()[0])
        if row is None:
            return None
        result: dict[str, Any] = {
            "id": int(row["id"]),
            "owner_id": str(row["owner_id"]),
            "scope": str(row["scope"]),
            "token_expires_at": str(row["token_expires_at"]),
            "last_checked_at": str(row["last_checked_at"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "site_count": site_count,
            "access_token_configured": bool(str(row["access_cipher"] or "")),
            "refresh_token_configured": bool(str(row["refresh_cipher"] or "")),
        }
        if secret:
            result["access_token"] = self._decrypt(str(row["access_cipher"] or ""))
            result["refresh_token"] = self._decrypt(str(row["refresh_cipher"] or ""))
        return result

    def list_sites(self, owner_id: str) -> list[dict[str, Any]]:
        self.init()
        clean_owner = _owner(owner_id)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM jira_sites WHERE owner_id=? ORDER BY lower(name),cloud_id", (clean_owner,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                scopes = json.loads(str(row["scopes_json"] or "[]"))
            except json.JSONDecodeError:
                scopes = []
            result.append(
                {
                    "cloud_id": str(row["cloud_id"]),
                    "name": str(row["name"]),
                    "url": str(row["url"]),
                    "scopes": [str(item) for item in scopes if isinstance(item, str)][:100],
                    "avatar_url": str(row["avatar_url"]),
                    "updated_at": str(row["updated_at"]),
                }
            )
        return result

    def replace_sites(self, owner_id: str, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.init()
        clean_owner = _owner(owner_id)
        sites = _normalize_accessible_resources(resources)
        now = _iso(_now())
        with self.db() as conn:
            conn.execute("DELETE FROM jira_sites WHERE owner_id=?", (clean_owner,))
            for site in sites:
                conn.execute(
                    """INSERT INTO jira_sites(owner_id,cloud_id,name,url,scopes_json,avatar_url,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        clean_owner,
                        site["cloud_id"],
                        site["name"],
                        site["url"],
                        json.dumps(site["scopes"], ensure_ascii=False, separators=(",", ":")),
                        site["avatar_url"],
                        now,
                    ),
                )
            conn.execute(
                "UPDATE jira_connections SET last_checked_at=?,updated_at=? WHERE owner_id=?", (now, now, clean_owner)
            )
        return self.list_sites(clean_owner)

    def save_connection(
        self,
        owner_id: str,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        scope: str,
        resources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        access = str(access_token or "").strip()
        refresh = str(refresh_token or "").strip()
        if len(access) < 16 or len(access) > 8192:
            raise JiraPluginError("Atlassian 没有返回有效 access_token")
        if len(refresh) < 16 or len(refresh) > 8192:
            raise JiraPluginError("Atlassian 没有返回 refresh_token；请确认 OAuth scope 包含 offline_access 后重新授权")
        sites = _normalize_accessible_resources(resources)
        if not sites:
            raise JiraPluginError("Atlassian 授权成功，但没有返回可访问的 Jira Cloud 站点")
        now = _now()
        expiry = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO jira_connections(
                       owner_id,access_cipher,refresh_cipher,token_expires_at,scope,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       access_cipher=excluded.access_cipher,
                       refresh_cipher=excluded.refresh_cipher,
                       token_expires_at=excluded.token_expires_at,
                       scope=excluded.scope,
                       last_checked_at=excluded.last_checked_at,
                       updated_at=excluded.updated_at""",
                (
                    clean_owner,
                    self._encrypt(access),
                    self._encrypt(refresh),
                    _iso(expiry),
                    _scope_text(scope),
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
            conn.execute("DELETE FROM jira_sites WHERE owner_id=?", (clean_owner,))
            for site in sites:
                conn.execute(
                    "INSERT INTO jira_sites(owner_id,cloud_id,name,url,scopes_json,avatar_url,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        clean_owner,
                        site["cloud_id"],
                        site["name"],
                        site["url"],
                        json.dumps(site["scopes"], ensure_ascii=False, separators=(",", ":")),
                        site["avatar_url"],
                        _iso(now),
                    ),
                )
        saved = self.get(clean_owner)
        if saved is None:
            raise JiraPluginError("Jira 插件连接保存失败")
        saved["sites"] = self.list_sites(clean_owner)
        return saved

    def update_tokens(
        self,
        owner_id: str,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        scope: str = "",
    ) -> None:
        access = str(access_token or "").strip()
        refresh = str(refresh_token or "").strip()
        if len(access) < 16:
            raise JiraPluginError("Atlassian 刷新 access_token 失败")
        if len(refresh) < 16:
            raise JiraPluginError("Atlassian 没有返回旋转后的 refresh_token，为避免丢失授权已停止刷新")
        now = _now()
        expiry = now + timedelta(seconds=max(60, int(expires_in or 0)))
        clean_scope = _scope_text(scope)
        with self.db() as conn:
            cur = conn.execute(
                """UPDATE jira_connections
                   SET access_cipher=?,refresh_cipher=?,token_expires_at=?,
                       scope=CASE WHEN ?<>'' THEN ? ELSE scope END,last_checked_at=?,updated_at=?
                   WHERE owner_id=?""",
                (
                    self._encrypt(access), self._encrypt(refresh), _iso(expiry), clean_scope, clean_scope,
                    _iso(now), _iso(now), _owner(owner_id),
                ),
            )
        if int(cur.rowcount or 0) != 1:
            raise JiraPluginError("Jira 连接不存在，请重新授权")

    def start_flow(self, owner_id: str) -> dict[str, str]:
        settings = fresh_settings()
        if not settings.jira_oauth_ready:
            raise JiraPluginError("FDEX Jira OAuth 尚未配置")
        clean_owner = _owner(owner_id)
        self.init()
        state = secrets.token_urlsafe(32)
        flow_id = secrets.token_hex(16)
        now = _now()
        redirect_uri = settings.public_base_url.rstrip("/") + "/account/plugins/jira/oauth/callback"
        expires = now + timedelta(minutes=settings.fdex_jira_oauth_flow_minutes)
        state_hash = __import__("hashlib").sha256(state.encode("utf-8")).hexdigest()
        with self.db() as conn:
            conn.execute(
                "UPDATE jira_oauth_flows SET status='expired',error='superseded' WHERE owner_id=? AND status='pending'",
                (clean_owner,),
            )
            conn.execute(
                """INSERT INTO jira_oauth_flows(id,owner_id,state_hash,redirect_uri,status,created_at,expires_at)
                   VALUES(?,?,?,?, 'pending',?,?)""",
                (flow_id, clean_owner, state_hash, redirect_uri, _iso(now), _iso(expires)),
            )
        scopes = _scope_list(settings.fdex_jira_oauth_scope)
        if "offline_access" not in scopes:
            scopes.append("offline_access")
        params = {
            "audience": "api.atlassian.com",
            "client_id": settings.fdex_jira_oauth_client_id.strip(),
            "scope": " ".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return {"flow_id": flow_id, "authorize_url": f"{_AUTHORIZE_URL}?{urlencode(params)}"}

    def complete_flow(self, owner_id: str, *, state: str, code: str) -> dict[str, Any]:
        settings = fresh_settings()
        if not settings.jira_oauth_ready:
            raise JiraPluginError("FDEX Jira OAuth 尚未配置")
        clean_owner = _owner(owner_id)
        clean_state = str(state or "").strip()
        clean_code = str(code or "").strip()
        if not clean_state or not clean_code:
            raise JiraPluginError("Jira OAuth 返回参数不完整")
        state_hash = __import__("hashlib").sha256(clean_state.encode("utf-8")).hexdigest()
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jira_oauth_flows WHERE owner_id=? AND state_hash=?", (clean_owner, state_hash)
            ).fetchone()
            if row is None:
                raise JiraPluginError("Jira OAuth 状态无效，请重新连接")
            if str(row["status"]) != "pending":
                raise JiraPluginError("该 Jira OAuth 流程已经使用或失效")
            expires = _parse_time(str(row["expires_at"] or ""))
            if expires is None or expires <= _now():
                conn.execute("UPDATE jira_oauth_flows SET status='expired',error='expired' WHERE id=?", (row["id"],))
                raise JiraPluginError("Jira OAuth 已过期，请重新连接")
            redirect_uri = str(row["redirect_uri"] or "")

        token = _token_request(
            {
                "grant_type": "authorization_code",
                "client_id": settings.fdex_jira_oauth_client_id.strip(),
                "client_secret": settings.fdex_jira_oauth_client_secret.strip(),
                "code": clean_code,
                "redirect_uri": redirect_uri,
            },
            context="Jira OAuth 换取令牌",
        )
        access = str(token.get("access_token") or "")
        refresh = str(token.get("refresh_token") or "")
        resources = _accessible_resources_with_token(access)
        saved = self.save_connection(
            clean_owner,
            access_token=access,
            refresh_token=refresh,
            expires_in=int(token.get("expires_in") or 0),
            scope=_scope_text(token.get("scope")),
            resources=resources,
        )
        with self.db() as conn:
            conn.execute(
                """UPDATE jira_oauth_flows SET status='authorized',completed_at=?,error=''
                   WHERE owner_id=? AND state_hash=? AND status='pending'""",
                (_iso(_now()), clean_owner, state_hash),
            )
        return saved

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        clean_owner = _owner(owner_id)
        with self.db() as conn:
            connections = int(conn.execute("SELECT COUNT(*) FROM jira_connections WHERE owner_id=?", (clean_owner,)).fetchone()[0])
            sites = int(conn.execute("SELECT COUNT(*) FROM jira_sites WHERE owner_id=?", (clean_owner,)).fetchone()[0])
            flows = int(conn.execute("SELECT COUNT(*) FROM jira_oauth_flows WHERE owner_id=?", (clean_owner,)).fetchone()[0])
            conn.execute("DELETE FROM jira_sites WHERE owner_id=?", (clean_owner,))
            conn.execute("DELETE FROM jira_connections WHERE owner_id=?", (clean_owner,))
            conn.execute("DELETE FROM jira_oauth_flows WHERE owner_id=?", (clean_owner,))
        return {"connections": connections, "sites": sites, "oauth_flows": flows}


@lru_cache(maxsize=1)
def jira_store() -> JiraStore:
    store = JiraStore()
    store.init()
    return store


def _decode_json(response: httpx.Response, *, context: str) -> Any:
    if response.is_redirect:
        raise JiraPluginError("Atlassian API 返回了未允许的重定向")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise JiraPluginError(f"{context}返回数据过大")
    if response.status_code == 204:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise JiraPluginError(f"{context}返回了无效 JSON") from exc
    if response.status_code < 200 or response.status_code >= 300:
        detail = ""
        if isinstance(payload, dict):
            errors = payload.get("errorMessages")
            if isinstance(errors, list):
                detail = "；".join(str(item)[:240] for item in errors[:3])
            if not detail:
                detail = str(payload.get("message") or payload.get("error_description") or payload.get("error") or "")[:700]
        raise JiraPluginError(f"{context}失败（HTTP {response.status_code}）" + (f"：{detail}" if detail else ""))
    return payload


def _token_request(payload: dict[str, str], *, context: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                _TOKEN_URL,
                json={key: value for key, value in payload.items() if value},
                headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "FDEX-Plugin-Runtime/1"},
            )
    except httpx.HTTPError as exc:
        raise JiraPluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    decoded = _decode_json(response, context=context)
    if not isinstance(decoded, dict):
        raise JiraPluginError(f"{context}返回格式无效")
    return decoded


def _accessible_resources_with_token(access_token: str) -> list[dict[str, Any]]:
    token = str(access_token or "").strip()
    if len(token) < 16:
        raise JiraPluginError("Atlassian access_token 无效")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                _ACCESSIBLE_RESOURCES_URL,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "FDEX-Plugin-Runtime/1"},
            )
    except httpx.HTTPError as exc:
        raise JiraPluginError(f"Jira 可访问站点检查失败：{type(exc).__name__}") from exc
    decoded = _decode_json(response, context="Jira 可访问站点检查")
    if not isinstance(decoded, list):
        raise JiraPluginError("Jira accessible-resources 返回格式无效")
    return [item for item in decoded if isinstance(item, dict)]


def _normalize_accessible_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in resources:
        cloud = str(item.get("id") or "").strip()
        if not _CLOUD_ID_RE.fullmatch(cloud) or cloud in seen:
            continue
        scopes = _scope_list(item.get("scopes"))
        # accessible-resources can include non-Jira containers. Jira resources expose jira scopes.
        if scopes and not any("jira" in scope for scope in scopes):
            continue
        url = str(item.get("url") or "").strip()
        if url and not (url.startswith("https://") and url.endswith(".atlassian.net")):
            continue
        result.append(
            {
                "cloud_id": cloud,
                "name": str(item.get("name") or "Jira Cloud")[:320],
                "url": url[:1200],
                "scopes": scopes[:100],
                "avatar_url": str(item.get("avatarUrl") or "")[:1500],
            }
        )
        seen.add(cloud)
    return result


def _access_token(owner_id: str) -> str:
    row = jira_store().get(owner_id, secret=True)
    if row is None:
        raise JiraPluginError("Jira 插件尚未连接")
    expiry = _parse_time(str(row.get("token_expires_at") or ""))
    if expiry is not None and expiry > _now() + timedelta(minutes=5):
        return str(row.get("access_token") or "")
    refresh = str(row.get("refresh_token") or "")
    settings = fresh_settings()
    token = _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": settings.fdex_jira_oauth_client_id.strip(),
            "client_secret": settings.fdex_jira_oauth_client_secret.strip(),
            "refresh_token": refresh,
        },
        context="Jira OAuth 刷新令牌",
    )
    access = str(token.get("access_token") or "")
    rotated = str(token.get("refresh_token") or "")
    jira_store().update_tokens(
        owner_id,
        access_token=access,
        refresh_token=rotated,
        expires_in=int(token.get("expires_in") or 0),
        scope=_scope_text(token.get("scope")),
    )
    return access


def jira_connection_status(owner_id: str) -> dict[str, Any]:
    if not fresh_settings().jira_oauth_ready:
        return {
            "connected": False,
            "connection_count": 0,
            "account_label": "",
            "connectable": False,
            "state": "configuration_required",
            "configuration_required": True,
        }
    row = jira_store().get(owner_id)
    if row is None:
        return {"connected": False, "connection_count": 0, "account_label": "", "connectable": True, "state": "available"}
    sites = jira_store().list_sites(owner_id)
    label = sites[0]["name"] if len(sites) == 1 else (f"{len(sites)} 个 Jira 站点" if sites else "Jira")
    return {
        "connected": True,
        "connection_count": len(sites) or 1,
        "account_label": label[:240],
        "connectable": True,
        "state": "connected",
        "site_count": len(sites),
        "last_checked_at": str(row.get("last_checked_at") or ""),
    }


def start_jira_oauth(owner_id: str) -> dict[str, str]:
    return jira_store().start_flow(owner_id)


def complete_jira_oauth(owner_id: str, *, state: str, code: str) -> dict[str, Any]:
    return jira_store().complete_flow(owner_id, state=state, code=code)


def disconnect_jira(owner_id: str) -> int:
    return int(jira_store().delete_owner(owner_id).get("connections") or 0)


def list_jira_sites(owner_id: str, *, refresh: bool = True) -> dict[str, Any]:
    if refresh:
        resources = _accessible_resources_with_token(_access_token(owner_id))
        sites = jira_store().replace_sites(owner_id, resources)
    else:
        sites = jira_store().list_sites(owner_id)
    return {"plugin_id": "jira", "sites": sites, "count": len(sites)}


def _resolve_site(owner_id: str, cloud_id: str = "") -> dict[str, Any]:
    sites = jira_store().list_sites(owner_id)
    if not sites:
        sites = list_jira_sites(owner_id, refresh=True)["sites"]
    if cloud_id:
        wanted = _cloud_id(cloud_id)
        for site in sites:
            if site["cloud_id"] == wanted:
                return site
        # Grants can add/remove sites without reconnecting; refresh once before failing closed.
        sites = list_jira_sites(owner_id, refresh=True)["sites"]
        for site in sites:
            if site["cloud_id"] == wanted:
                return site
        raise JiraPluginError("指定 Jira cloud_id 当前不在该账号的 OAuth 授权资源中")
    if len(sites) == 1:
        return sites[0]
    if not sites:
        raise JiraPluginError("当前 Jira OAuth 授权没有可访问站点")
    raise ValueError("当前 Jira OAuth 授权包含多个站点，请明确指定 cloud_id 后再执行站点内操作")


def _request(
    owner_id: str,
    cloud_id: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str = "Jira API",
) -> Any:
    site = _resolve_site(owner_id, cloud_id)
    clean_path = "/" + str(path or "").strip().lstrip("/")
    if ".." in clean_path or "\\" in clean_path or not clean_path.startswith("/rest/api/3/"):
        raise ValueError("Jira REST API 路径无效")
    url = f"{_API_ROOT}/{quote(site['cloud_id'], safe='')}{clean_path}"
    try:
        with httpx.Client(timeout=25.0, follow_redirects=False, trust_env=False) as client:
            response = client.request(
                method.upper(),
                url,
                params=params,
                json=json_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_access_token(owner_id)}",
                    "User-Agent": "FDEX-Plugin-Runtime/1",
                },
            )
    except httpx.HTTPError as exc:
        raise JiraPluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    return _decode_json(response, context=context)


def text_to_adf(text: str) -> dict[str, Any]:
    clean = _bounded_text(text, "文本", limit=_MAX_TEXT)
    paragraphs: list[dict[str, Any]] = []
    for line in clean.split("\n"):
        paragraph: dict[str, Any] = {"type": "paragraph", "content": []}
        if line:
            paragraph["content"] = [{"type": "text", "text": line}]
        paragraphs.append(paragraph)
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"version": 1, "type": "doc", "content": paragraphs}


def adf_to_text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    parts: list[str] = []
    total = 0
    block_types = {"paragraph", "heading", "blockquote", "listItem", "codeBlock", "panel"}

    def emit(text: str) -> None:
        nonlocal total
        if not text or total >= limit:
            return
        piece = text[: max(0, limit - total)]
        parts.append(piece)
        total += len(piece)

    def walk(node: Any) -> None:
        if total >= limit:
            return
        if isinstance(node, str):
            emit(node)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        node_type = str(node.get("type") or "")
        if node_type == "text":
            emit(str(node.get("text") or ""))
        elif node_type == "hardBreak":
            emit("\n")
        else:
            walk(node.get("content"))
        if node_type in block_types and parts and not parts[-1].endswith("\n"):
            emit("\n")

    walk(value)
    return "".join(parts).rstrip("\n")[:limit]


def _user_summary(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    return {
        "account_id": str(item.get("accountId") or "")[:240],
        "display_name": str(item.get("displayName") or "")[:320],
        "active": bool(item.get("active", True)),
    }


def _issue_summary(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    priority = fields.get("priority") if isinstance(fields.get("priority"), dict) else {}
    return {
        "id": str(item.get("id") or "")[:160],
        "key": str(item.get("key") or "")[:160],
        "summary": str(fields.get("summary") or "")[:2000],
        "description": adf_to_text(fields.get("description")),
        "status": {"id": str(status.get("id") or "")[:160], "name": str(status.get("name") or "")[:240]},
        "project": {"id": str(project.get("id") or "")[:160], "key": str(project.get("key") or "")[:120], "name": str(project.get("name") or "")[:320]},
        "issue_type": {"id": str(issue_type.get("id") or "")[:160], "name": str(issue_type.get("name") or "")[:240], "subtask": bool(issue_type.get("subtask"))},
        "assignee": _user_summary(fields.get("assignee")),
        "priority": {"id": str(priority.get("id") or "")[:160], "name": str(priority.get("name") or "")[:240]},
        "created_at": str(fields.get("created") or "")[:100],
        "updated_at": str(fields.get("updated") or "")[:100],
        "due_date": str(fields.get("duedate") or "")[:100],
    }


def _project_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or "")[:160],
        "key": str(item.get("key") or "")[:120],
        "name": str(item.get("name") or "")[:320],
        "project_type": str(item.get("projectTypeKey") or "")[:120],
        "simplified": bool(item.get("simplified")),
        "style": str(item.get("style") or "")[:120],
    }


def list_jira_projects(
    owner_id: str,
    *,
    cloud_id: str = "",
    query: str = "",
    start_at: int = 0,
    max_results: int = 50,
) -> dict[str, Any]:
    start = _bounded_int(start_at, "start_at", minimum=0, maximum=1000000)
    maximum = _bounded_int(max_results, "max_results", minimum=1, maximum=100)
    clean_query = _bounded_text(query, "query", limit=500).strip()
    payload = _request(
        owner_id,
        cloud_id,
        "GET",
        "/rest/api/3/project/search",
        params={"startAt": start, "maxResults": maximum, **({"query": clean_query} if clean_query else {})},
        context="Jira 项目列表",
    )
    if not isinstance(payload, dict):
        raise JiraPluginError("Jira 项目列表返回格式无效")
    values = payload.get("values") if isinstance(payload.get("values"), list) else []
    projects = [_project_summary(item) for item in values if isinstance(item, dict)]
    return {
        "plugin_id": "jira",
        "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"],
        "projects": projects,
        "count": len(projects),
        "start_at": int(payload.get("startAt") or start),
        "max_results": int(payload.get("maxResults") or maximum),
        "total": int(payload.get("total") or len(projects)),
        "is_last": bool(payload.get("isLast", True)),
    }


def search_jira_issues(
    owner_id: str,
    jql: str = "ORDER BY updated DESC",
    *,
    cloud_id: str = "",
    max_results: int = 50,
    next_page_token: str = "",
) -> dict[str, Any]:
    clean_jql = _bounded_text(jql, "jql", required=True, limit=_MAX_JQL).strip()
    maximum = _bounded_int(max_results, "max_results", minimum=1, maximum=100)
    token = _bounded_text(next_page_token, "next_page_token", limit=1000).strip()
    body: dict[str, Any] = {
        "jql": clean_jql,
        "maxResults": maximum,
        "fields": ["summary", "description", "status", "project", "issuetype", "assignee", "priority", "created", "updated", "duedate"],
    }
    if token:
        body["nextPageToken"] = token
    payload = _request(
        owner_id,
        cloud_id,
        "POST",
        "/rest/api/3/search/jql",
        json_body=body,
        context="Jira Issue 搜索",
    )
    if not isinstance(payload, dict):
        raise JiraPluginError("Jira Issue 搜索返回格式无效")
    rows = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    issues = [_issue_summary(item) for item in rows if isinstance(item, dict)]
    return {
        "plugin_id": "jira",
        "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"],
        "jql": clean_jql,
        "issues": issues,
        "count": len(issues),
        "is_last": bool(payload.get("isLast", not bool(payload.get("nextPageToken")))),
        "next_page_token": str(payload.get("nextPageToken") or "")[:1000],
    }


def read_jira_issue(owner_id: str, issue_id_or_key: str, *, cloud_id: str = "") -> dict[str, Any]:
    issue_ref = _ref(issue_id_or_key, "issue_id_or_key")
    fields = "summary,description,status,project,issuetype,assignee,priority,created,updated,duedate"
    payload = _request(
        owner_id,
        cloud_id,
        "GET",
        f"/rest/api/3/issue/{quote(issue_ref, safe='')}",
        params={"fields": fields},
        context="Jira Issue 读取",
    )
    if not isinstance(payload, dict):
        raise JiraPluginError("Jira Issue 读取返回格式无效")
    comments_payload = _request(
        owner_id,
        cloud_id,
        "GET",
        f"/rest/api/3/issue/{quote(issue_ref, safe='')}/comment",
        params={"startAt": 0, "maxResults": 50, "orderBy": "created"},
        context="Jira Issue 评论读取",
    )
    comments: list[dict[str, Any]] = []
    comments_total = 0
    if isinstance(comments_payload, dict):
        comments_total = int(comments_payload.get("total") or 0)
        for item in comments_payload.get("comments") if isinstance(comments_payload.get("comments"), list) else []:
            if not isinstance(item, dict):
                continue
            comments.append(
                {
                    "id": str(item.get("id") or "")[:160],
                    "body": adf_to_text(item.get("body")),
                    "author": _user_summary(item.get("author")),
                    "created_at": str(item.get("created") or "")[:100],
                    "updated_at": str(item.get("updated") or "")[:100],
                }
            )
    return {
        "plugin_id": "jira",
        "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"],
        "issue": _issue_summary(payload),
        "comments": comments,
        "comments_truncated": comments_total > len(comments),
    }


def list_jira_transitions(owner_id: str, issue_id_or_key: str, *, cloud_id: str = "") -> dict[str, Any]:
    issue_ref = _ref(issue_id_or_key, "issue_id_or_key")
    payload = _request(
        owner_id,
        cloud_id,
        "GET",
        f"/rest/api/3/issue/{quote(issue_ref, safe='')}/transitions",
        context="Jira Issue 状态流转列表",
    )
    rows = payload.get("transitions") if isinstance(payload, dict) and isinstance(payload.get("transitions"), list) else []
    transitions: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        target = item.get("to") if isinstance(item.get("to"), dict) else {}
        transitions.append(
            {
                "id": str(item.get("id") or "")[:160],
                "name": str(item.get("name") or "")[:240],
                "to": {"id": str(target.get("id") or "")[:160], "name": str(target.get("name") or "")[:240]},
            }
        )
    return {"plugin_id": "jira", "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"], "issue_id_or_key": issue_ref, "transitions": transitions, "count": len(transitions)}


def create_jira_issue(
    owner_id: str,
    summary: str,
    *,
    cloud_id: str = "",
    project_key: str = "",
    project_id: str = "",
    issue_type_name: str = "",
    issue_type_id: str = "",
    description: str = "",
    assignee_account_id: str = "",
    priority_name: str = "",
    priority_id: str = "",
) -> dict[str, Any]:
    fields: dict[str, Any] = {"summary": _bounded_text(summary, "summary", required=True, limit=2000).strip()}
    if project_id:
        fields["project"] = {"id": _ref(project_id, "project_id")}
    elif project_key:
        fields["project"] = {"key": _ref(project_key, "project_key")}
    else:
        raise ValueError("project_key / project_id 至少需要一个")
    if issue_type_id:
        fields["issuetype"] = {"id": _ref(issue_type_id, "issue_type_id")}
    elif issue_type_name:
        fields["issuetype"] = {"name": _bounded_text(issue_type_name, "issue_type_name", required=True, limit=240).strip()}
    else:
        raise ValueError("issue_type_name / issue_type_id 至少需要一个")
    if description:
        fields["description"] = text_to_adf(description)
    if assignee_account_id:
        fields["assignee"] = {"accountId": _bounded_text(assignee_account_id, "assignee_account_id", required=True, limit=240).strip()}
    if priority_id:
        fields["priority"] = {"id": _ref(priority_id, "priority_id")}
    elif priority_name:
        fields["priority"] = {"name": _bounded_text(priority_name, "priority_name", required=True, limit=240).strip()}
    payload = _request(owner_id, cloud_id, "POST", "/rest/api/3/issue", json_body={"fields": fields}, context="Jira Issue 创建")
    if not isinstance(payload, dict):
        raise JiraPluginError("Jira Issue 创建返回格式无效")
    return {
        "plugin_id": "jira",
        "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"],
        "action": "created",
        "issue": {"id": str(payload.get("id") or "")[:160], "key": str(payload.get("key") or "")[:160], "self": str(payload.get("self") or "")[:1200]},
    }


def update_jira_issue(
    owner_id: str,
    issue_id_or_key: str,
    *,
    cloud_id: str = "",
    summary: str = "",
    description: str | None = None,
    assignee_account_id: str = "",
    priority_name: str = "",
    priority_id: str = "",
) -> dict[str, Any]:
    issue_ref = _ref(issue_id_or_key, "issue_id_or_key")
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = _bounded_text(summary, "summary", required=True, limit=2000).strip()
    if description is not None:
        fields["description"] = text_to_adf(description)
    if assignee_account_id:
        fields["assignee"] = {"accountId": _bounded_text(assignee_account_id, "assignee_account_id", required=True, limit=240).strip()}
    if priority_id:
        fields["priority"] = {"id": _ref(priority_id, "priority_id")}
    elif priority_name:
        fields["priority"] = {"name": _bounded_text(priority_name, "priority_name", required=True, limit=240).strip()}
    if not fields:
        raise ValueError("至少提供一个要更新的 Jira Issue 字段")
    _request(
        owner_id,
        cloud_id,
        "PUT",
        f"/rest/api/3/issue/{quote(issue_ref, safe='')}",
        json_body={"fields": fields},
        context="Jira Issue 更新",
    )
    return {"plugin_id": "jira", "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"], "action": "updated", "issue_id_or_key": issue_ref, "updated_fields": sorted(fields)}


def transition_jira_issue(owner_id: str, issue_id_or_key: str, transition_id: str, *, cloud_id: str = "") -> dict[str, Any]:
    issue_ref = _ref(issue_id_or_key, "issue_id_or_key")
    transition = _ref(transition_id, "transition_id")
    _request(
        owner_id,
        cloud_id,
        "POST",
        f"/rest/api/3/issue/{quote(issue_ref, safe='')}/transitions",
        json_body={"transition": {"id": transition}},
        context="Jira Issue 状态流转",
    )
    return {"plugin_id": "jira", "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"], "action": "transitioned", "issue_id_or_key": issue_ref, "transition_id": transition}


def add_jira_comment(owner_id: str, issue_id_or_key: str, body: str, *, cloud_id: str = "") -> dict[str, Any]:
    issue_ref = _ref(issue_id_or_key, "issue_id_or_key")
    clean_body = _bounded_text(body, "body", required=True).strip()
    payload = _request(
        owner_id,
        cloud_id,
        "POST",
        f"/rest/api/3/issue/{quote(issue_ref, safe='')}/comment",
        json_body={"body": text_to_adf(clean_body)},
        context="Jira 评论创建",
    )
    if not isinstance(payload, dict):
        raise JiraPluginError("Jira 评论创建返回格式无效")
    return {
        "plugin_id": "jira",
        "cloud_id": _resolve_site(owner_id, cloud_id)["cloud_id"],
        "action": "commented",
        "issue_id_or_key": issue_ref,
        "comment": {
            "id": str(payload.get("id") or "")[:160],
            "body": adf_to_text(payload.get("body")),
            "author": _user_summary(payload.get("author")),
            "created_at": str(payload.get("created") or "")[:100],
        },
    }
