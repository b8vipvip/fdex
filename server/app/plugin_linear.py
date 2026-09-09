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
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
_TOKEN_URL = "https://api.linear.app/oauth/token"
_REVOKE_URL = "https://api.linear.app/oauth/revoke"
_GRAPHQL_URL = "https://api.linear.app/graphql"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,160}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-linear.db"
KEY_PATH = _DATA_DIR / "plugin-linear.key"
_MAX_GRAPHQL_BYTES = 2 * 1024 * 1024
_MAX_TEXT = 30000


class LinearPluginError(RuntimeError):
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


def _model_id(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not _MODEL_ID_RE.fullmatch(clean):
        raise ValueError(f"{label} 格式无效")
    return clean


def _bounded_text(value: str, label: str, *, required: bool = False, limit: int = _MAX_TEXT) -> str:
    clean = str(value or "")
    if required and not clean.strip():
        raise ValueError(f"{label} 不能为空")
    if "\x00" in clean or len(clean) > limit:
        raise ValueError(f"{label} 过长或包含无效字符")
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


class LinearStore:
    """Owner-scoped encrypted OAuth credentials plus one-time browser PKCE state."""

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
                CREATE TABLE IF NOT EXISTS linear_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL UNIQUE,
                    access_cipher TEXT NOT NULL,
                    refresh_cipher TEXT NOT NULL,
                    token_expires_at TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    viewer_id TEXT NOT NULL DEFAULT '',
                    viewer_name TEXT NOT NULL DEFAULT '',
                    viewer_email TEXT NOT NULL DEFAULT '',
                    organization_id TEXT NOT NULL DEFAULT '',
                    organization_name TEXT NOT NULL DEFAULT '',
                    organization_key TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_linear_owner ON linear_connections(owner_id);
                CREATE TABLE IF NOT EXISTS linear_oauth_flows (
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
                CREATE INDEX IF NOT EXISTS idx_linear_oauth_owner
                    ON linear_oauth_flows(owner_id, created_at DESC);
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
            raise LinearPluginError("Linear 插件凭据解密失败，请重新连接") from exc

    def get(self, owner_id: str, *, secret: bool = False) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM linear_connections WHERE owner_id=? LIMIT 1", (_owner(owner_id),)).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = {
            "id": int(row["id"]),
            "owner_id": str(row["owner_id"]),
            "scope": str(row["scope"]),
            "viewer_id": str(row["viewer_id"]),
            "viewer_name": str(row["viewer_name"]),
            "viewer_email": str(row["viewer_email"]),
            "organization_id": str(row["organization_id"]),
            "organization_name": str(row["organization_name"]),
            "organization_key": str(row["organization_key"]),
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
        identity: dict[str, Any],
    ) -> dict[str, Any]:
        access = str(access_token or "").strip()
        refresh = str(refresh_token or "").strip()
        if len(access) < 16 or len(access) > 8192:
            raise LinearPluginError("Linear 没有返回有效 access_token")
        if len(refresh) < 16 or len(refresh) > 8192:
            raise LinearPluginError("Linear 没有返回 refresh_token，请重新授权")
        data = identity.get("data") if isinstance(identity.get("data"), dict) else {}
        viewer = data.get("viewer") if isinstance(data.get("viewer"), dict) else {}
        org = data.get("organization") if isinstance(data.get("organization"), dict) else {}
        now = _now()
        expiry = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with self.db() as conn:
            conn.execute(
                """INSERT INTO linear_connections(
                       owner_id,access_cipher,refresh_cipher,token_expires_at,scope,viewer_id,viewer_name,
                       viewer_email,organization_id,organization_name,organization_key,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       access_cipher=excluded.access_cipher,
                       refresh_cipher=excluded.refresh_cipher,
                       token_expires_at=excluded.token_expires_at,
                       scope=excluded.scope,
                       viewer_id=excluded.viewer_id,
                       viewer_name=excluded.viewer_name,
                       viewer_email=excluded.viewer_email,
                       organization_id=excluded.organization_id,
                       organization_name=excluded.organization_name,
                       organization_key=excluded.organization_key,
                       last_checked_at=excluded.last_checked_at,
                       updated_at=excluded.updated_at""",
                (
                    _owner(owner_id), self._encrypt(access), self._encrypt(refresh), _iso(expiry), str(scope or "")[:2000],
                    str(viewer.get("id") or "")[:160], str(viewer.get("name") or "")[:320],
                    str(viewer.get("email") or "")[:320], str(org.get("id") or "")[:160],
                    str(org.get("name") or "")[:320], str(org.get("urlKey") or "")[:240],
                    _iso(now), _iso(now), _iso(now),
                ),
            )
        saved = self.get(owner_id)
        if saved is None:
            raise LinearPluginError("Linear 插件连接保存失败")
        return saved

    def update_tokens(self, owner_id: str, *, access_token: str, refresh_token: str, expires_in: int, scope: str = "") -> None:
        access = str(access_token or "").strip()
        refresh = str(refresh_token or "").strip()
        if len(access) < 16:
            raise LinearPluginError("Linear 刷新 access_token 失败")
        if len(refresh) < 16:
            raise LinearPluginError("Linear 刷新 refresh_token 失败")
        now = _now()
        expiry = now + timedelta(seconds=max(60, int(expires_in or 0)))
        with self.db() as conn:
            conn.execute(
                """UPDATE linear_connections
                   SET access_cipher=?,refresh_cipher=?,token_expires_at=?,scope=CASE WHEN ?<>'' THEN ? ELSE scope END,
                       last_checked_at=?,updated_at=? WHERE owner_id=?""",
                (self._encrypt(access), self._encrypt(refresh), _iso(expiry), scope, scope[:2000], _iso(now), _iso(now), _owner(owner_id)),
            )

    def start_flow(self, owner_id: str) -> dict[str, str]:
        settings = fresh_settings()
        if not settings.linear_oauth_ready:
            raise LinearPluginError("FDEX Linear OAuth 尚未配置")
        clean_owner = _owner(owner_id)
        self.init()
        state = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        flow_id = secrets.token_hex(16)
        now = _now()
        redirect_uri = settings.public_base_url.rstrip("/") + "/account/plugins/linear/oauth/callback"
        expires = now + timedelta(minutes=settings.fdex_linear_oauth_flow_minutes)
        with self.db() as conn:
            conn.execute(
                "UPDATE linear_oauth_flows SET status='expired',error='superseded',verifier_cipher='' WHERE owner_id=? AND status='pending'",
                (clean_owner,),
            )
            conn.execute(
                """INSERT INTO linear_oauth_flows(id,owner_id,state_hash,verifier_cipher,redirect_uri,status,created_at,expires_at)
                   VALUES(?,?,?,?,?,'pending',?,?)""",
                (flow_id, clean_owner, _hash(state), self._encrypt(verifier), redirect_uri, _iso(now), _iso(expires)),
            )
        params = {
            "client_id": settings.fdex_linear_oauth_client_id.strip(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(part.strip() for part in settings.fdex_linear_oauth_scope.replace(" ", ",").split(",") if part.strip()),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return {"flow_id": flow_id, "authorize_url": f"{_AUTHORIZE_URL}?{urlencode(params)}"}

    def complete_flow(self, owner_id: str, *, state: str, code: str) -> dict[str, Any]:
        settings = fresh_settings()
        if not settings.linear_oauth_ready:
            raise LinearPluginError("FDEX Linear OAuth 尚未配置")
        clean_owner = _owner(owner_id)
        state_hash = _hash(str(state or "").strip())
        clean_code = str(code or "").strip()
        if not state or not clean_code:
            raise LinearPluginError("Linear OAuth 返回参数不完整")
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM linear_oauth_flows WHERE owner_id=? AND state_hash=?", (clean_owner, state_hash)
            ).fetchone()
            if row is None:
                raise LinearPluginError("Linear OAuth 状态无效，请重新连接")
            if str(row["status"]) != "pending":
                raise LinearPluginError("该 Linear OAuth 流程已经使用或失效")
            expires = _parse_time(str(row["expires_at"] or ""))
            if expires is None or expires <= _now():
                conn.execute("UPDATE linear_oauth_flows SET status='expired',error='expired',verifier_cipher='' WHERE id=?", (row["id"],))
                raise LinearPluginError("Linear OAuth 已过期，请重新连接")
            verifier = self._decrypt(str(row["verifier_cipher"] or ""))
            redirect_uri = str(row["redirect_uri"] or "")

        token = _oauth_token_request(
            {
                "code": clean_code,
                "redirect_uri": redirect_uri,
                "client_id": settings.fdex_linear_oauth_client_id.strip(),
                "client_secret": settings.fdex_linear_oauth_client_secret.strip(),
                "code_verifier": verifier,
                "grant_type": "authorization_code",
            },
            context="Linear OAuth 换取令牌",
        )
        access = str(token.get("access_token") or "")
        refresh = str(token.get("refresh_token") or "")
        identity = _graphql_with_token(
            access,
            "query FdexLinearIdentity { viewer { id name email } organization { id name urlKey } }",
            {},
            context="Linear 身份验证",
        )
        saved = self.save_connection(
            clean_owner,
            access_token=access,
            refresh_token=refresh,
            expires_in=int(token.get("expires_in") or 0),
            scope=_scope_text(token.get("scope")),
            identity=identity,
        )
        with self.db() as conn:
            conn.execute(
                """UPDATE linear_oauth_flows SET status='authorized',verifier_cipher='',completed_at=?,error=''
                   WHERE owner_id=? AND state_hash=? AND status='pending'""",
                (_iso(_now()), clean_owner, state_hash),
            )
        return saved

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        clean = _owner(owner_id)
        with self.db() as conn:
            connections = int(conn.execute("SELECT COUNT(*) FROM linear_connections WHERE owner_id=?", (clean,)).fetchone()[0])
            flows = int(conn.execute("SELECT COUNT(*) FROM linear_oauth_flows WHERE owner_id=?", (clean,)).fetchone()[0])
            conn.execute("DELETE FROM linear_connections WHERE owner_id=?", (clean,))
            conn.execute("DELETE FROM linear_oauth_flows WHERE owner_id=?", (clean,))
        return {"connections": connections, "oauth_flows": flows}


@lru_cache(maxsize=1)
def linear_store() -> LinearStore:
    store = LinearStore()
    store.init()
    return store


def _scope_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if str(item).strip())
    return str(value or "")[:2000]


def _decode_json(response: httpx.Response, *, context: str) -> dict[str, Any]:
    if response.is_redirect:
        raise LinearPluginError("Linear API 返回了未允许的重定向")
    if len(response.content) > _MAX_GRAPHQL_BYTES:
        raise LinearPluginError(f"{context}返回数据过大")
    try:
        payload = response.json()
    except ValueError as exc:
        raise LinearPluginError(f"{context}返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise LinearPluginError(f"{context}返回格式无效")
    if response.status_code < 200 or response.status_code >= 300:
        detail = str(payload.get("error_description") or payload.get("error") or payload.get("message") or "")[:700]
        raise LinearPluginError(f"{context}失败（HTTP {response.status_code}）" + (f"：{detail}" if detail else ""))
    return payload


def _oauth_token_request(form: dict[str, str], *, context: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                _TOKEN_URL,
                data={key: value for key, value in form.items() if value},
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "FDEX-Plugin-Runtime/1"},
            )
    except httpx.HTTPError as exc:
        raise LinearPluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    return _decode_json(response, context=context)


def _graphql_with_token(access_token: str, query: str, variables: dict[str, Any], *, context: str) -> dict[str, Any]:
    token = str(access_token or "").strip()
    if len(token) < 16:
        raise LinearPluginError("Linear access_token 无效")
    try:
        with httpx.Client(timeout=25.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                _GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "FDEX-Plugin-Runtime/1"},
            )
    except httpx.HTTPError as exc:
        raise LinearPluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    payload = _decode_json(response, context=context)
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        messages = [str(item.get("message") or "")[:300] for item in errors[:3] if isinstance(item, dict)]
        raise LinearPluginError(f"{context}失败：" + "；".join(item for item in messages if item))
    if not isinstance(payload.get("data"), dict):
        raise LinearPluginError(f"{context}没有返回 GraphQL data")
    return payload


def _access_token(owner_id: str) -> str:
    row = linear_store().get(owner_id, secret=True)
    if row is None:
        raise LinearPluginError("Linear 插件尚未连接")
    expiry = _parse_time(str(row.get("token_expires_at") or ""))
    if expiry is not None and expiry > _now() + timedelta(minutes=5):
        return str(row.get("access_token") or "")
    refresh = str(row.get("refresh_token") or "")
    settings = fresh_settings()
    token = _oauth_token_request(
        {
            "refresh_token": refresh,
            "grant_type": "refresh_token",
            "client_id": settings.fdex_linear_oauth_client_id.strip(),
            "client_secret": settings.fdex_linear_oauth_client_secret.strip(),
        },
        context="Linear OAuth 刷新令牌",
    )
    access = str(token.get("access_token") or "")
    rotated = str(token.get("refresh_token") or "")
    linear_store().update_tokens(
        owner_id,
        access_token=access,
        refresh_token=rotated,
        expires_in=int(token.get("expires_in") or 0),
        scope=_scope_text(token.get("scope")),
    )
    return access


def _graphql(owner_id: str, query: str, variables: dict[str, Any], *, context: str) -> dict[str, Any]:
    return _graphql_with_token(_access_token(owner_id), query, variables, context=context)


def linear_connection_status(owner_id: str) -> dict[str, Any]:
    if not fresh_settings().linear_oauth_ready:
        return {
            "connected": False, "connection_count": 0, "account_label": "", "connectable": False,
            "state": "configuration_required", "configuration_required": True,
        }
    row = linear_store().get(owner_id)
    if row is None:
        return {"connected": False, "connection_count": 0, "account_label": "", "connectable": True, "state": "available"}
    label = str(row.get("organization_name") or row.get("viewer_name") or "Linear")[:240]
    return {
        "connected": True,
        "connection_count": 1,
        "account_label": label,
        "connectable": True,
        "state": "connected",
        "last_checked_at": str(row.get("last_checked_at") or ""),
    }


def start_linear_oauth(owner_id: str) -> dict[str, str]:
    return linear_store().start_flow(owner_id)


def complete_linear_oauth(owner_id: str, *, state: str, code: str) -> dict[str, Any]:
    return linear_store().complete_flow(owner_id, state=state, code=code)


def disconnect_linear(owner_id: str) -> int:
    row = linear_store().get(owner_id, secret=True)
    if row is None:
        return 0
    token = str(row.get("access_token") or "")
    if token:
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as client:
                client.post(_REVOKE_URL, data={"token": token}, headers={"User-Agent": "FDEX-Plugin-Runtime/1"})
        except httpx.HTTPError:
            pass
    return int(linear_store().delete_owner(owner_id).get("connections") or 0)


def _issue_summary(item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    team = item.get("team") if isinstance(item.get("team"), dict) else {}
    assignee = item.get("assignee") if isinstance(item.get("assignee"), dict) else {}
    project = item.get("project") if isinstance(item.get("project"), dict) else {}
    return {
        "id": str(item.get("id") or "")[:160],
        "identifier": str(item.get("identifier") or "")[:80],
        "title": str(item.get("title") or "")[:2000],
        "description": str(item.get("description") or "")[:30000],
        "url": str(item.get("url") or "")[:1200],
        "priority": item.get("priority"),
        "created_at": str(item.get("createdAt") or "")[:80],
        "updated_at": str(item.get("updatedAt") or "")[:80],
        "due_date": str(item.get("dueDate") or "")[:80],
        "state": {"id": str(state.get("id") or "")[:160], "name": str(state.get("name") or "")[:240], "type": str(state.get("type") or "")[:80]},
        "team": {"id": str(team.get("id") or "")[:160], "key": str(team.get("key") or "")[:80], "name": str(team.get("name") or "")[:240]},
        "assignee": {"id": str(assignee.get("id") or "")[:160], "name": str(assignee.get("name") or "")[:240]},
        "project": {"id": str(project.get("id") or "")[:160], "name": str(project.get("name") or "")[:240]},
    }


def list_linear_teams(owner_id: str, *, page_size: int = 50, after: str = "") -> dict[str, Any]:
    size = _page_size(page_size)
    payload = _graphql(
        owner_id,
        """query FdexLinearTeams($first:Int!,$after:String){
          teams(first:$first,after:$after){nodes{id key name} pageInfo{hasNextPage endCursor}}
        }""",
        {"first": size, "after": after or None},
        context="Linear 团队列表",
    )
    conn = payload["data"].get("teams") if isinstance(payload["data"].get("teams"), dict) else {}
    nodes = conn.get("nodes") if isinstance(conn.get("nodes"), list) else []
    teams = [{"id": str(x.get("id") or "")[:160], "key": str(x.get("key") or "")[:80], "name": str(x.get("name") or "")[:240]} for x in nodes if isinstance(x, dict)]
    page = conn.get("pageInfo") if isinstance(conn.get("pageInfo"), dict) else {}
    return {"plugin_id": "linear", "teams": teams, "count": len(teams), "has_more": bool(page.get("hasNextPage")), "next_cursor": str(page.get("endCursor") or "")[:500]}


def list_linear_workflow_states(owner_id: str, *, page_size: int = 100, after: str = "") -> dict[str, Any]:
    size = _page_size(page_size)
    payload = _graphql(
        owner_id,
        """query FdexLinearStates($first:Int!,$after:String){
          workflowStates(first:$first,after:$after){nodes{id name type team{id key name}} pageInfo{hasNextPage endCursor}}
        }""",
        {"first": size, "after": after or None},
        context="Linear 工作流状态列表",
    )
    conn = payload["data"].get("workflowStates") if isinstance(payload["data"].get("workflowStates"), dict) else {}
    states: list[dict[str, Any]] = []
    for item in conn.get("nodes") if isinstance(conn.get("nodes"), list) else []:
        if not isinstance(item, dict):
            continue
        team = item.get("team") if isinstance(item.get("team"), dict) else {}
        states.append({
            "id": str(item.get("id") or "")[:160], "name": str(item.get("name") or "")[:240], "type": str(item.get("type") or "")[:80],
            "team": {"id": str(team.get("id") or "")[:160], "key": str(team.get("key") or "")[:80], "name": str(team.get("name") or "")[:240]},
        })
    page = conn.get("pageInfo") if isinstance(conn.get("pageInfo"), dict) else {}
    return {"plugin_id": "linear", "states": states, "count": len(states), "has_more": bool(page.get("hasNextPage")), "next_cursor": str(page.get("endCursor") or "")[:500]}


def search_linear_issues(owner_id: str, query: str = "", *, page_size: int = 50, after: str = "") -> dict[str, Any]:
    size = _page_size(page_size)
    clean_query = _bounded_text(query, "query", limit=500).strip()
    filters: dict[str, Any] | None = None
    if clean_query:
        filters = {"or": [{"title": {"containsIgnoreCase": clean_query}}, {"description": {"containsIgnoreCase": clean_query}}]}
    payload = _graphql(
        owner_id,
        """query FdexLinearIssues($first:Int!,$after:String,$filter:IssueFilter){
          issues(first:$first,after:$after,filter:$filter,orderBy:updatedAt){
            nodes{id identifier title description url priority createdAt updatedAt dueDate state{id name type} team{id key name} assignee{id name} project{id name}}
            pageInfo{hasNextPage endCursor}
          }
        }""",
        {"first": size, "after": after or None, "filter": filters},
        context="Linear Issue 搜索",
    )
    conn = payload["data"].get("issues") if isinstance(payload["data"].get("issues"), dict) else {}
    issues = [_issue_summary(item) for item in conn.get("nodes") if isinstance(item, dict)] if isinstance(conn.get("nodes"), list) else []
    page = conn.get("pageInfo") if isinstance(conn.get("pageInfo"), dict) else {}
    return {"plugin_id": "linear", "query": clean_query, "issues": issues, "count": len(issues), "has_more": bool(page.get("hasNextPage")), "next_cursor": str(page.get("endCursor") or "")[:500]}


def read_linear_issue(owner_id: str, issue_id: str) -> dict[str, Any]:
    clean_id = _model_id(issue_id, "issue_id")
    payload = _graphql(
        owner_id,
        """query FdexLinearIssue($id:String!){
          issue(id:$id){id identifier title description url priority createdAt updatedAt dueDate
            state{id name type} team{id key name} assignee{id name} project{id name}
            comments(first:50){nodes{id body createdAt updatedAt user{id name}} pageInfo{hasNextPage endCursor}}
          }
        }""",
        {"id": clean_id},
        context="Linear Issue 读取",
    )
    issue = payload["data"].get("issue")
    if not isinstance(issue, dict):
        raise LinearPluginError("Linear Issue 不存在或当前授权不可访问")
    result = _issue_summary(issue)
    comments_conn = issue.get("comments") if isinstance(issue.get("comments"), dict) else {}
    comments: list[dict[str, Any]] = []
    for item in comments_conn.get("nodes") if isinstance(comments_conn.get("nodes"), list) else []:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        comments.append({
            "id": str(item.get("id") or "")[:160], "body": str(item.get("body") or "")[:30000],
            "created_at": str(item.get("createdAt") or "")[:80], "updated_at": str(item.get("updatedAt") or "")[:80],
            "user": {"id": str(user.get("id") or "")[:160], "name": str(user.get("name") or "")[:240]},
        })
    return {"plugin_id": "linear", "issue": result, "comments": comments, "comments_truncated": bool((comments_conn.get("pageInfo") or {}).get("hasNextPage")) if isinstance(comments_conn.get("pageInfo"), dict) else False}


def create_linear_issue(
    owner_id: str,
    team_id: str,
    title: str,
    *,
    description: str = "",
    state_id: str = "",
    assignee_id: str = "",
    project_id: str = "",
    priority: int | None = None,
) -> dict[str, Any]:
    input_data: dict[str, Any] = {"teamId": _model_id(team_id, "team_id"), "title": _bounded_text(title, "title", required=True, limit=2000).strip()}
    if description:
        input_data["description"] = _bounded_text(description, "description")
    for key, value, label in (("stateId", state_id, "state_id"), ("assigneeId", assignee_id, "assignee_id"), ("projectId", project_id, "project_id")):
        if value:
            input_data[key] = _model_id(value, label)
    if priority is not None:
        if isinstance(priority, bool) or int(priority) not in range(0, 5):
            raise ValueError("priority 必须是 0-4")
        input_data["priority"] = int(priority)
    payload = _graphql(
        owner_id,
        """mutation FdexLinearIssueCreate($input:IssueCreateInput!){
          issueCreate(input:$input){success issue{id identifier title description url priority createdAt updatedAt dueDate state{id name type} team{id key name} assignee{id name} project{id name}}}
        }""",
        {"input": input_data},
        context="Linear Issue 创建",
    )
    result = payload["data"].get("issueCreate") if isinstance(payload["data"].get("issueCreate"), dict) else {}
    issue = result.get("issue") if isinstance(result.get("issue"), dict) else None
    if not bool(result.get("success")) or issue is None:
        raise LinearPluginError("Linear Issue 创建未成功")
    return {"plugin_id": "linear", "action": "created", "issue": _issue_summary(issue)}


def update_linear_issue(
    owner_id: str,
    issue_id: str,
    *,
    title: str = "",
    description: str | None = None,
    state_id: str = "",
    assignee_id: str = "",
    project_id: str = "",
    priority: int | None = None,
) -> dict[str, Any]:
    input_data: dict[str, Any] = {}
    if title:
        input_data["title"] = _bounded_text(title, "title", required=True, limit=2000).strip()
    if description is not None:
        input_data["description"] = _bounded_text(description, "description")
    for key, value, label in (("stateId", state_id, "state_id"), ("assigneeId", assignee_id, "assignee_id"), ("projectId", project_id, "project_id")):
        if value:
            input_data[key] = _model_id(value, label)
    if priority is not None:
        if isinstance(priority, bool) or int(priority) not in range(0, 5):
            raise ValueError("priority 必须是 0-4")
        input_data["priority"] = int(priority)
    if not input_data:
        raise ValueError("至少提供一个要更新的 Issue 字段")
    payload = _graphql(
        owner_id,
        """mutation FdexLinearIssueUpdate($id:String!,$input:IssueUpdateInput!){
          issueUpdate(id:$id,input:$input){success issue{id identifier title description url priority createdAt updatedAt dueDate state{id name type} team{id key name} assignee{id name} project{id name}}}
        }""",
        {"id": _model_id(issue_id, "issue_id"), "input": input_data},
        context="Linear Issue 更新",
    )
    result = payload["data"].get("issueUpdate") if isinstance(payload["data"].get("issueUpdate"), dict) else {}
    issue = result.get("issue") if isinstance(result.get("issue"), dict) else None
    if not bool(result.get("success")) or issue is None:
        raise LinearPluginError("Linear Issue 更新未成功")
    return {"plugin_id": "linear", "action": "updated", "issue": _issue_summary(issue)}


def create_linear_comment(owner_id: str, issue_id: str, body: str) -> dict[str, Any]:
    clean_body = _bounded_text(body, "body", required=True).strip()
    payload = _graphql(
        owner_id,
        """mutation FdexLinearCommentCreate($input:CommentCreateInput!){
          commentCreate(input:$input){success comment{id body createdAt user{id name}}}
        }""",
        {"input": {"issueId": _model_id(issue_id, "issue_id"), "body": clean_body}},
        context="Linear 评论创建",
    )
    result = payload["data"].get("commentCreate") if isinstance(payload["data"].get("commentCreate"), dict) else {}
    comment = result.get("comment") if isinstance(result.get("comment"), dict) else None
    if not bool(result.get("success")) or comment is None:
        raise LinearPluginError("Linear 评论创建未成功")
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    return {
        "plugin_id": "linear", "action": "commented",
        "comment": {"id": str(comment.get("id") or "")[:160], "body": str(comment.get("body") or "")[:30000], "created_at": str(comment.get("createdAt") or "")[:80], "user": {"id": str(user.get("id") or "")[:160], "name": str(user.get("name") or "")[:240]}},
    }
