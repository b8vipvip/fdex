from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

import httpx

from app.agent_projects import AgentProjectStore, agent_project_store
from app.config import fresh_settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class GitHubWebOAuthError(RuntimeError):
    pass


class GitHubWebOAuthStore:
    """Short-lived owner-bound browser OAuth state.

    Only a SHA-256 state digest is persisted. The PKCE verifier is encrypted with the same
    server-side Fernet key used by the account-scoped GitHub connection store. Browser cookies
    never contain GitHub access or refresh tokens.
    """

    def __init__(self, path: Path | None = None, project_store: AgentProjectStore | None = None) -> None:
        settings = fresh_settings()
        data = Path(settings.app_dir).expanduser().resolve() / "server" / "data"
        self.path = (path or data / "github-web-oauth.db").resolve()
        self.projects = project_store or agent_project_store()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
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
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS github_web_oauth_flows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    state_hash TEXT NOT NULL UNIQUE,
                    verifier_cipher TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    connection_id INTEGER,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_github_web_oauth_owner
                    ON github_web_oauth_flows(owner_id, created_at DESC);
                """
            )
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def callback_url() -> str:
        settings = fresh_settings()
        return settings.public_base_url.rstrip("/") + "/account/github/callback"

    def start(self, owner_id: str) -> dict[str, str]:
        settings = fresh_settings()
        if not settings.github_web_oauth_ready:
            raise GitHubWebOAuthError("FDEX GitHub Web OAuth 尚未配置")
        owner_id = (owner_id or "").strip()
        if not owner_id.startswith("usr_"):
            raise GitHubWebOAuthError("无效的 FDEX 用户")
        self.init()
        state = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        flow_id = secrets.token_hex(16)
        now = _now()
        redirect_uri = self.callback_url()
        expires = now + timedelta(minutes=settings.fdex_github_web_oauth_flow_minutes)
        with self.db() as conn:
            conn.execute(
                "UPDATE github_web_oauth_flows SET status='expired',error='superseded' WHERE owner_id=? AND status='pending'",
                (owner_id,),
            )
            conn.execute(
                """INSERT INTO github_web_oauth_flows(
                       id,owner_id,state_hash,verifier_cipher,redirect_uri,status,created_at,expires_at
                   ) VALUES(?,?,?,?,?,'pending',?,?)""",
                (
                    flow_id,
                    owner_id,
                    _hash(state),
                    self.projects.encrypt(verifier),
                    redirect_uri,
                    _iso(now),
                    _iso(expires),
                ),
            )
            conn.execute(
                """DELETE FROM github_web_oauth_flows WHERE owner_id=? AND id NOT IN (
                       SELECT id FROM github_web_oauth_flows WHERE owner_id=? ORDER BY created_at DESC LIMIT 20
                   )""",
                (owner_id, owner_id),
            )
        params = {
            "client_id": settings.fdex_github_web_oauth_client_id.strip(),
            "redirect_uri": redirect_uri,
            "scope": " ".join(settings.fdex_github_web_oauth_scope.split()),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return {"flow_id": flow_id, "authorize_url": f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"}

    def complete(self, owner_id: str, *, state: str, code: str) -> dict[str, Any]:
        settings = fresh_settings()
        if not settings.github_web_oauth_ready:
            raise GitHubWebOAuthError("FDEX GitHub Web OAuth 尚未配置")
        owner_id = (owner_id or "").strip()
        state_hash = _hash((state or "").strip())
        code = (code or "").strip()
        if not state or not code:
            raise GitHubWebOAuthError("GitHub 授权返回参数不完整")
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM github_web_oauth_flows WHERE owner_id=? AND state_hash=?",
                (owner_id, state_hash),
            ).fetchone()
            if row is None:
                raise GitHubWebOAuthError("GitHub 授权状态无效，请重新连接")
            if str(row["status"]) != "pending":
                raise GitHubWebOAuthError("该 GitHub 授权流程已经使用或失效")
            expires = _parse(str(row["expires_at"] or ""))
            if expires is None or expires <= _now():
                conn.execute(
                    "UPDATE github_web_oauth_flows SET status='expired',error='expired' WHERE id=?",
                    (row["id"],),
                )
                raise GitHubWebOAuthError("GitHub 授权已过期，请重新连接")
            verifier = self.projects.decrypt(str(row["verifier_cipher"] or ""))
            redirect_uri = str(row["redirect_uri"] or self.callback_url())

        token_result = self._exchange(
            {
                "client_id": settings.fdex_github_web_oauth_client_id.strip(),
                "client_secret": settings.fdex_github_web_oauth_client_secret.strip(),
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        )
        if token_result.get("error"):
            detail = str(token_result.get("error_description") or token_result["error"])[:500]
            self._fail(owner_id, state_hash, detail)
            raise GitHubWebOAuthError(detail)
        token = str(token_result.get("access_token") or "").strip()
        if not token:
            self._fail(owner_id, state_hash, "missing_access_token")
            raise GitHubWebOAuthError("GitHub 没有返回访问授权")
        profile = self.projects._github_json(token, GITHUB_USER_URL)
        connection = self.projects._save_oauth_connection(
            owner_id,
            profile,
            token_result,
            client_id=settings.fdex_github_web_oauth_client_id.strip(),
        )
        with self.db() as conn:
            conn.execute(
                """UPDATE github_web_oauth_flows
                   SET status='authorized',connection_id=?,verifier_cipher='',completed_at=?,error=''
                   WHERE owner_id=? AND state_hash=? AND status='pending'""",
                (connection["id"], _iso(_now()), owner_id, state_hash),
            )
        return connection

    def _fail(self, owner_id: str, state_hash: str, error: str) -> None:
        with self.db() as conn:
            conn.execute(
                """UPDATE github_web_oauth_flows SET status='error',verifier_cipher='',error=?
                   WHERE owner_id=? AND state_hash=?""",
                ((error or "oauth_error")[:500], owner_id, state_hash),
            )

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with self.db() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM github_web_oauth_flows WHERE owner_id=?", (owner_id,)).fetchone()[0])
            conn.execute("DELETE FROM github_web_oauth_flows WHERE owner_id=?", (owner_id,))
        return count

    @staticmethod
    def _exchange(form: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.post(
                    GITHUB_TOKEN_URL,
                    data=form,
                    headers={"Accept": "application/json", "User-Agent": "fdex-user-web"},
                )
        except httpx.HTTPError as exc:
            raise GitHubWebOAuthError(f"GitHub OAuth 请求失败：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise GitHubWebOAuthError(f"GitHub OAuth HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubWebOAuthError("GitHub OAuth 返回了无效数据") from exc
        if not isinstance(payload, dict):
            raise GitHubWebOAuthError("GitHub OAuth 返回格式错误")
        return payload
