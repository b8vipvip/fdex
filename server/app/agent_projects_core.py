from __future__ import annotations

import base64
import fcntl
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.account_operations import (
    AccountOperationBusy,
    account_deleted_by_hash,
    account_hash,
    account_operation,
)
from app.config import fresh_settings

_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")
_SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,180}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "agent-projects.db"
KEY_PATH = _DATA_DIR / "agent-projects.key"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_time(value: str) -> datetime | None:
    clean = (value or "").strip()
    if not clean:
        return None
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _after(seconds: int, *, now: datetime | None = None) -> str:
    return ((now or _utcnow()) + timedelta(seconds=max(0, int(seconds)))).isoformat(timespec="seconds")


def _safe_scope(value: str) -> str:
    clean = (value or "").strip()
    if not _SAFE_SCOPE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("owner scope is invalid")
    return clean


def _safe_repo(value: str) -> str:
    clean = (value or "").strip().removesuffix(".git")
    if clean.startswith("https://github.com/"):
        clean = clean.removeprefix("https://github.com/")
    if not _SAFE_REPO.fullmatch(clean):
        raise ValueError("GitHub repository must use owner/name")
    owner, repo = clean.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."}:
        raise ValueError("GitHub repository is invalid")
    return clean


def _safe_branch(value: str) -> str:
    clean = (value or "main").strip()
    if not _SAFE_BRANCH.fullmatch(clean) or ".." in clean or clean.startswith("/") or clean.endswith("/"):
        raise ValueError("base branch is invalid")
    return clean


@contextmanager
def _owner_write_guard(owner_id: str, operation: str) -> Iterator[None]:
    """Fence stale account-scoped writes against export/erasure/account deletion."""
    if not owner_id.startswith("usr_"):
        yield
        return
    try:
        with account_operation(owner_id, operation):
            if account_deleted_by_hash(account_hash(owner_id)):
                raise ValueError("FDEX account has been deleted")
            yield
    except AccountOperationBusy as exc:
        raise ValueError("FDEX account data operation is in progress") from exc


class AgentProjectStore:
    def __init__(self, db_path: Path = DB_PATH, key_path: Path = KEY_PATH) -> None:
        self.db_path = db_path.resolve()
        self.key_path = key_path.resolve()
        self._fernet: Fernet | None = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self._cipher()
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS github_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    login TEXT NOT NULL DEFAULT '',
                    token_cipher TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_github_owner ON github_connections(owner_id,id);
                CREATE TABLE IF NOT EXISTS github_device_flows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    client_id TEXT NOT NULL DEFAULT '',
                    device_code_cipher TEXT NOT NULL DEFAULT '',
                    user_code TEXT NOT NULL DEFAULT '',
                    verification_uri TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    interval_seconds INTEGER NOT NULL DEFAULT 5,
                    next_poll_at TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    connection_id INTEGER,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_github_flow_owner ON github_device_flows(owner_id,created_at);
                CREATE TABLE IF NOT EXISTS agent_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL,
                    base_branch TEXT NOT NULL DEFAULT 'main',
                    connection_id INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    allow_push INTEGER NOT NULL DEFAULT 0,
                    allow_pr INTEGER NOT NULL DEFAULT 0,
                    allow_network INTEGER NOT NULL DEFAULT 0,
                    sandbox_memory_mb INTEGER NOT NULL DEFAULT 2048,
                    sandbox_cpu_percent INTEGER NOT NULL DEFAULT 150,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, repo_full_name),
                    FOREIGN KEY(connection_id) REFERENCES github_connections(id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_project_owner ON agent_projects(owner_id,enabled,id);
                """
            )
            self._ensure_connection_columns(conn)
            self._ensure_device_flow_columns(conn)
            self._ensure_project_columns(conn)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _ensure_connection_columns(conn: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(github_connections)").fetchall()}
        additions = {
            "auth_type": "TEXT NOT NULL DEFAULT 'pat'",
            "github_user_id": "TEXT NOT NULL DEFAULT ''",
            "oauth_client_id": "TEXT NOT NULL DEFAULT ''",
            "refresh_token_cipher": "TEXT NOT NULL DEFAULT ''",
            "token_expires_at": "TEXT NOT NULL DEFAULT ''",
            "refresh_expires_at": "TEXT NOT NULL DEFAULT ''",
            "scope": "TEXT NOT NULL DEFAULT ''",
            "needs_reconnect": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE github_connections ADD COLUMN {name} {ddl}")

    @staticmethod
    def _ensure_device_flow_columns(conn: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(github_device_flows)").fetchall()}
        if "client_id" not in existing:
            conn.execute("ALTER TABLE github_device_flows ADD COLUMN client_id TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _ensure_project_columns(conn: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(agent_projects)").fetchall()}
        additions = {
            "allow_network": "INTEGER NOT NULL DEFAULT 0",
            "sandbox_memory_mb": "INTEGER NOT NULL DEFAULT 2048",
            "sandbox_cpu_percent": "INTEGER NOT NULL DEFAULT 150",
        }
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE agent_projects ADD COLUMN {name} {ddl}")

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
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

    @contextmanager
    def owner_db(self, owner_id: str, operation: str) -> Iterator[sqlite3.Connection]:
        with _owner_write_guard(owner_id, operation):
            with self.db() as conn:
                yield conn

    def _cipher(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.key_path.with_suffix(".tmp")
            tmp.write_bytes(key + b"\n")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.key_path)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("GitHub connector secret cannot be decrypted") from exc

    def list_connections(self, owner_id: str) -> list[dict[str, Any]]:
        self.init()
        owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM github_connections WHERE owner_id=? ORDER BY id", (owner_id,)).fetchall()
        return [self._connection_row(row) for row in rows]

    def _connection_row(self, row: sqlite3.Row, *, secret: bool = False) -> dict[str, Any]:
        data = dict(row)
        cipher = str(data.pop("token_cipher", "") or "")
        refresh_cipher = str(data.pop("refresh_token_cipher", "") or "")
        oauth_client_id = str(data.pop("oauth_client_id", "") or "")
        data["token_configured"] = bool(cipher)
        data["refresh_configured"] = bool(refresh_cipher)
        data["needs_reconnect"] = bool(data.get("needs_reconnect", 0))
        if secret:
            data["token"] = self.decrypt(cipher)
            data["refresh_token"] = self.decrypt(refresh_cipher)
            data["oauth_client_id"] = oauth_client_id
        return data

    def get_connection(self, owner_id: str, connection_id: int, *, secret: bool = False) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM github_connections WHERE id=? AND owner_id=?", (connection_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("GitHub connection not found")
        return self._connection_row(row, secret=secret)

    def save_connection(self, owner_id: str, name: str, token: str, connection_id: int | None = None) -> dict[str, Any]:
        """Save a legacy manually supplied PAT.

        Android uses Device OAuth, but the admin-only PAT path remains available for
        migrations and emergency recovery. Both modes share the same encrypted store.
        """
        self.init()
        owner_id = _safe_scope(owner_id)
        name = (name or "GitHub").strip()[:80]
        token = (token or "").strip()
        if connection_id:
            old = self.get_connection(owner_id, connection_id, secret=True)
            token = token or str(old.get("token") or "")
        if not token:
            raise ValueError("GitHub token is required")
        profile = self._github_json(token, "https://api.github.com/user")
        login = str(profile.get("login") or "").strip()
        if not login:
            raise ValueError("GitHub token validation failed")
        github_user_id = str(profile.get("id") or "").strip()
        now = _now()
        with self.owner_db(owner_id, "github_pat_write") as conn:
            if connection_id:
                conn.execute(
                    """UPDATE github_connections
                       SET name=?,login=?,github_user_id=?,token_cipher=?,auth_type='pat',
                           oauth_client_id='',refresh_token_cipher='',token_expires_at='',refresh_expires_at='',
                           scope='',needs_reconnect=0,updated_at=?
                       WHERE id=? AND owner_id=?""",
                    (name, login, github_user_id, self.encrypt(token), now, connection_id, owner_id),
                )
                cid = connection_id
            else:
                cur = conn.execute(
                    """INSERT INTO github_connections(
                           owner_id,name,login,github_user_id,token_cipher,auth_type,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'pat',?,?)""",
                    (owner_id, name, login, github_user_id, self.encrypt(token), now, now),
                )
                cid = int(cur.lastrowid)
        return self.get_connection(owner_id, cid)

    def start_device_flow(self, owner_id: str) -> dict[str, Any]:
        """Start one owner-scoped GitHub Device Flow without exposing the device code."""
        self.init()
        owner_id = _safe_scope(owner_id)
        settings = fresh_settings()
        client_id = settings.fdex_github_oauth_client_id.strip()
        if not client_id:
            raise RuntimeError("GitHub Device OAuth is not configured")
        now = _utcnow()
        with self.db() as conn:
            active = conn.execute(
                """SELECT * FROM github_device_flows
                   WHERE owner_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1""",
                (owner_id,),
            ).fetchone()
            if active is not None:
                expires_at = _as_time(str(active["expires_at"] or ""))
                if expires_at and expires_at > now and str(active["client_id"] or "") == client_id:
                    return self._device_flow_row(active)
                conn.execute(
                    """UPDATE github_device_flows
                       SET status='expired',error='superseded',device_code_cipher='',updated_at=?
                       WHERE id=? AND owner_id=?""",
                    (now.isoformat(timespec="seconds"), active["id"], owner_id),
                )
        form = {"client_id": client_id}
        scope = " ".join(settings.fdex_github_oauth_scope.split())
        if scope:
            form["scope"] = scope
        result = self._oauth_post(GITHUB_DEVICE_CODE_URL, form)
        device_code = str(result.get("device_code") or "").strip()
        user_code = str(result.get("user_code") or "").strip()
        verification_uri = str(result.get("verification_uri") or "").strip()
        if not device_code or not user_code or not verification_uri.startswith("https://github.com/"):
            raise RuntimeError("GitHub returned an invalid Device OAuth challenge")
        expires_in = max(60, min(self._integer(result.get("expires_in"), 900), 1800))
        interval = max(5, min(self._integer(result.get("interval"), 5), 60))
        flow_id = uuid.uuid4().hex
        now = _utcnow()
        stamp = now.isoformat(timespec="seconds")
        with self.owner_db(owner_id, "github_oauth_start") as conn:
            conn.execute(
                """UPDATE github_device_flows
                   SET status='expired',error='superseded',device_code_cipher='',updated_at=?
                   WHERE owner_id=? AND status='pending'""",
                (stamp, owner_id),
            )
            conn.execute(
                """INSERT INTO github_device_flows(
                       id,owner_id,client_id,device_code_cipher,user_code,verification_uri,status,
                       interval_seconds,next_poll_at,expires_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,'pending',?,?,?,?,?)""",
                (
                    flow_id,
                    owner_id,
                    client_id,
                    self.encrypt(device_code),
                    user_code,
                    verification_uri,
                    interval,
                    _after(interval, now=now),
                    _after(expires_in, now=now),
                    stamp,
                    stamp,
                ),
            )
            conn.execute(
                """DELETE FROM github_device_flows
                   WHERE owner_id=? AND id NOT IN (
                       SELECT id FROM github_device_flows WHERE owner_id=? ORDER BY created_at DESC LIMIT 20
                   )""",
                (owner_id, owner_id),
            )
        return self.get_device_flow(owner_id, flow_id)

    def get_device_flow(self, owner_id: str, flow_id: str) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        clean_id = (flow_id or "").strip()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM github_device_flows WHERE id=? AND owner_id=?",
                (clean_id, owner_id),
            ).fetchone()
        if row is None:
            raise KeyError("GitHub Device OAuth flow not found")
        return self._device_flow_row(row)

    def _device_flow_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data.pop("device_code_cipher", None)
        data.pop("client_id", None)
        due = _as_time(str(data.get("next_poll_at") or ""))
        now = _utcnow()
        data["retry_after_seconds"] = max(0, int(((due - now).total_seconds() if due else 0) + 0.999))
        connection_id = data.get("connection_id")
        data["connection"] = None
        if connection_id:
            try:
                data["connection"] = self.get_connection(str(data["owner_id"]), int(connection_id))
            except KeyError:
                pass
        data.pop("owner_id", None)
        data.pop("connection_id", None)
        return data

    def poll_device_flow(self, owner_id: str, flow_id: str) -> dict[str, Any]:
        """Poll GitHub at its required cadence; multiple FDEX workers cannot double-poll."""
        self.init()
        owner_id = _safe_scope(owner_id)
        now = _utcnow()
        stamp = now.isoformat(timespec="seconds")
        clean_flow_id = (flow_id or "").strip()
        with self.db() as conn:
            existing = conn.execute(
                "SELECT * FROM github_device_flows WHERE id=? AND owner_id=?",
                (clean_flow_id, owner_id),
            ).fetchone()
        if existing is None:
            raise KeyError("GitHub Device OAuth flow not found")
        if str(existing["status"]) != "pending":
            return self._device_flow_row(existing)

        terminal_row: sqlite3.Row | None = None
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM github_device_flows WHERE id=? AND owner_id=?",
                (clean_flow_id, owner_id),
            ).fetchone()
            if row is None:
                raise KeyError("GitHub Device OAuth flow not found")
            if str(row["status"]) == "pending":
                expires_at = _as_time(str(row["expires_at"]))
                if expires_at is None or expires_at <= now:
                    conn.execute(
                        "UPDATE github_device_flows SET status='expired',device_code_cipher='',error='expired_token',updated_at=? WHERE id=?",
                        (stamp, clean_flow_id),
                    )
                    updated = conn.execute("SELECT * FROM github_device_flows WHERE id=?", (clean_flow_id,)).fetchone()
                    return self._device_flow_row(updated)
                next_poll = _as_time(str(row["next_poll_at"]))
                if next_poll and next_poll > now:
                    return self._device_flow_row(row)
                interval = max(5, int(row["interval_seconds"] or 5))
                conn.execute(
                    "UPDATE github_device_flows SET next_poll_at=?,updated_at=? WHERE id=?",
                    (_after(interval, now=now), stamp, clean_flow_id),
                )
                device_code = self.decrypt(str(row["device_code_cipher"]))
                flow_client_id = str(row["client_id"] or "").strip()
            else:
                terminal_row = row
        if terminal_row is not None:
            return self._device_flow_row(terminal_row)

        client_id = flow_client_id or fresh_settings().fdex_github_oauth_client_id.strip()
        if not client_id:
            raise RuntimeError("GitHub Device OAuth is not configured")
        result = self._oauth_post(
            GITHUB_ACCESS_TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        error = str(result.get("error") or "").strip()
        if error == "authorization_pending":
            return self.get_device_flow(owner_id, clean_flow_id)
        if error == "slow_down":
            with self.db() as conn:
                conn.execute(
                    """UPDATE github_device_flows
                       SET interval_seconds=MIN(interval_seconds + 5,300),
                           next_poll_at=?,error='',updated_at=? WHERE id=? AND owner_id=?""",
                    (_after(min(interval + 5, 300)), _now(), clean_flow_id, owner_id),
                )
            return self.get_device_flow(owner_id, clean_flow_id)
        if error:
            terminal = "expired" if error == "expired_token" else "denied" if error == "access_denied" else "error"
            detail = str(result.get("error_description") or error).strip()[:500]
            with self.db() as conn:
                conn.execute(
                    """UPDATE github_device_flows
                       SET status=?,device_code_cipher='',error=?,updated_at=?
                       WHERE id=? AND owner_id=?""",
                    (terminal, detail, _now(), clean_flow_id, owner_id),
                )
            return self.get_device_flow(owner_id, clean_flow_id)

        access_token = str(result.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("GitHub Device OAuth returned no access token")
        profile = self._github_json(access_token, f"{GITHUB_API_URL}/user")
        connection = self._save_oauth_connection(owner_id, profile, result, client_id=client_id)
        with self.db() as conn:
            conn.execute(
                """UPDATE github_device_flows
                   SET status='authorized',connection_id=?,device_code_cipher='',error='',updated_at=?
                   WHERE id=? AND owner_id=?""",
                (connection["id"], _now(), clean_flow_id, owner_id),
            )
        return self.get_device_flow(owner_id, clean_flow_id)

    def _save_oauth_connection(
        self,
        owner_id: str,
        profile: dict[str, Any],
        token_result: dict[str, Any],
        *,
        client_id: str = "",
    ) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        login = str(profile.get("login") or "").strip()
        github_user_id = str(profile.get("id") or "").strip()
        token = str(token_result.get("access_token") or "").strip()
        if not login or not github_user_id or not token:
            raise RuntimeError("GitHub OAuth identity validation failed")
        refresh_token = str(token_result.get("refresh_token") or "").strip()
        expires_in = self._integer(token_result.get("expires_in"), 0)
        refresh_expires_in = self._integer(token_result.get("refresh_token_expires_in"), 0)
        now = _utcnow()
        stamp = now.isoformat(timespec="seconds")
        token_expires_at = _after(expires_in, now=now) if expires_in > 0 else ""
        refresh_expires_at = _after(refresh_expires_in, now=now) if refresh_expires_in > 0 else ""
        scope = str(token_result.get("scope") or "").strip()
        oauth_client_id = client_id.strip() or fresh_settings().fdex_github_oauth_client_id.strip()
        with self.owner_db(owner_id, "github_oauth_write") as conn:
            existing = conn.execute(
                """SELECT id FROM github_connections
                   WHERE owner_id=? AND auth_type='oauth' AND github_user_id=? ORDER BY id LIMIT 1""",
                (owner_id, github_user_id),
            ).fetchone()
            if existing:
                cid = int(existing["id"])
                conn.execute(
                    """UPDATE github_connections
                       SET name=?,login=?,token_cipher=?,refresh_token_cipher=?,token_expires_at=?,
                           refresh_expires_at=?,scope=?,oauth_client_id=?,needs_reconnect=0,updated_at=?
                       WHERE id=? AND owner_id=?""",
                    (
                        f"GitHub · {login}"[:80], login, self.encrypt(token), self.encrypt(refresh_token),
                        token_expires_at, refresh_expires_at, scope, oauth_client_id, stamp, cid, owner_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO github_connections(
                           owner_id,name,login,github_user_id,token_cipher,refresh_token_cipher,
                           token_expires_at,refresh_expires_at,scope,oauth_client_id,auth_type,
                           needs_reconnect,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,'oauth',0,?,?)""",
                    (
                        owner_id, f"GitHub · {login}"[:80], login, github_user_id,
                        self.encrypt(token), self.encrypt(refresh_token), token_expires_at,
                        refresh_expires_at, scope, oauth_client_id, stamp, stamp,
                    ),
                )
                cid = int(cur.lastrowid)
        return self.get_connection(owner_id, cid)

    def delete_connection(self, owner_id: str, connection_id: int) -> None:
        self.get_connection(owner_id, connection_id)
        with self.db() as conn:
            used = conn.execute(
                "SELECT COUNT(*) FROM agent_projects WHERE owner_id=? AND connection_id=?",
                (owner_id, connection_id),
            ).fetchone()[0]
            if used:
                raise ValueError("GitHub connection is still used by a project")
            conn.execute(
                "UPDATE github_device_flows SET connection_id=NULL WHERE owner_id=? AND connection_id=?",
                (owner_id, connection_id),
            )
            conn.execute("DELETE FROM github_connections WHERE id=? AND owner_id=?", (connection_id, owner_id))

    def list_projects(self, owner_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        self.init()
        owner_id = _safe_scope(owner_id)
        sql = "SELECT * FROM agent_projects WHERE owner_id=?" + (" AND enabled=1" if enabled_only else "") + " ORDER BY name,id"
        with self.db() as conn:
            rows = conn.execute(sql, (owner_id,)).fetchall()
        return [self._project_row(row) for row in rows]

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("enabled", "allow_push", "allow_pr", "allow_network"):
            data[key] = bool(data[key])
        return data

    def get_project(self, owner_id: str, project_id: int) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM agent_projects WHERE id=? AND owner_id=?", (project_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("Agent project not found")
        return self._project_row(row)

    def save_project(
        self,
        owner_id: str,
        *,
        name: str,
        repo_full_name: str,
        base_branch: str = "main",
        connection_id: int | None = None,
        allow_push: bool = False,
        allow_pr: bool = False,
        allow_network: bool = False,
        sandbox_memory_mb: int = 2048,
        sandbox_cpu_percent: int = 150,
        enabled: bool = True,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        repo = _safe_repo(repo_full_name)
        branch = _safe_branch(base_branch)
        name = (name or repo.split("/")[-1]).strip()[:100]
        if not name:
            raise ValueError("project name is required")
        if connection_id is not None:
            self.get_connection(owner_id, connection_id)
        allow_push = bool(allow_push or allow_pr)
        memory_mb = max(128, min(16384, int(sandbox_memory_mb or 2048)))
        cpu_percent = max(10, min(800, int(sandbox_cpu_percent or 150)))
        now = _now()
        with self.owner_db(owner_id, "agent_project_write") as conn:
            if project_id:
                conn.execute(
                    "UPDATE agent_projects SET name=?,repo_full_name=?,base_branch=?,connection_id=?,enabled=?,allow_push=?,allow_pr=?,allow_network=?,sandbox_memory_mb=?,sandbox_cpu_percent=?,updated_at=? WHERE id=? AND owner_id=?",
                    (name, repo, branch, connection_id, int(enabled), int(allow_push), int(allow_pr), int(allow_network), memory_mb, cpu_percent, now, project_id, owner_id),
                )
                pid = project_id
            else:
                cur = conn.execute(
                    "INSERT INTO agent_projects(owner_id,name,repo_full_name,base_branch,connection_id,enabled,allow_push,allow_pr,allow_network,sandbox_memory_mb,sandbox_cpu_percent,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (owner_id, name, repo, branch, connection_id, int(enabled), int(allow_push), int(allow_pr), int(allow_network), memory_mb, cpu_percent, now, now),
                )
                pid = int(cur.lastrowid)
        return self.get_project(owner_id, pid)

    def delete_project(self, owner_id: str, project_id: int) -> None:
        self.get_project(owner_id, project_id)
        with self.db() as conn:
            conn.execute("DELETE FROM agent_projects WHERE id=? AND owner_id=?", (project_id, owner_id))

    def owner_root(self, owner_id: str) -> Path:
        owner_id = _safe_scope(owner_id)
        root = Path(fresh_settings().fdex_agent_sandbox_root).expanduser().resolve()
        owner_root = (root / "owners" / owner_id).resolve()
        if root not in owner_root.parents:
            raise ValueError("owner sandbox escaped sandbox root")
        owner_root.mkdir(parents=True, exist_ok=True)
        return owner_root

    def project_paths(self, owner_id: str, project_id: int) -> tuple[Path, Path]:
        owner_root = self.owner_root(owner_id)
        project_root = (owner_root / "projects" / str(int(project_id))).resolve()
        if owner_root not in project_root.parents:
            raise ValueError("project sandbox escaped owner sandbox")
        return (project_root / "repository").resolve(), (project_root / "worktrees").resolve()

    def prepare_repository(self, owner_id: str, project_id: int) -> tuple[dict[str, Any], Path, Path]:
        project = self.get_project(owner_id, project_id)
        if not project["enabled"]:
            raise ValueError("Agent project is disabled")
        repo_path, worktrees = self.project_paths(owner_id, project_id)
        worktrees.mkdir(parents=True, exist_ok=True)
        env = self._git_env(owner_id, project.get("connection_id"))
        clone_url = f"https://github.com/{project['repo_full_name']}.git"
        if not (repo_path / ".git").exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            self._git(("git", "clone", "--no-tags", clone_url, str(repo_path)), cwd=repo_path.parent, env=env, timeout=300)
        else:
            self._git(("git", "fetch", "origin", "--prune"), cwd=repo_path, env=env, timeout=180)
        self._git(("git", "rev-parse", "--verify", f"origin/{project['base_branch']}"), cwd=repo_path, env=env, timeout=30)
        return project, repo_path, worktrees

    def push_branch(self, owner_id: str, project_id: int, repo_path: Path, branch: str) -> str:
        project = self.get_project(owner_id, project_id)
        if not project["allow_push"]:
            raise ValueError("Git push is disabled for this project")
        if not branch.startswith("fdex-agent/"):
            raise ValueError("only generated fdex-agent branches may be pushed")
        env = self._git_env(owner_id, project.get("connection_id"), required=True)
        return self._git(("git", "push", "-u", "origin", branch), cwd=repo_path, env=env, timeout=180)

    def create_pr(self, owner_id: str, project_id: int, *, head: str, title: str, body: str = "") -> str:
        project = self.get_project(owner_id, project_id)
        if not project["allow_pr"]:
            raise ValueError("Pull request creation is disabled for this project")
        if not head.startswith("fdex-agent/"):
            raise ValueError("only generated fdex-agent branches may create pull requests")
        connection_id = project.get("connection_id")
        if not connection_id:
            raise ValueError("GitHub connection is required")
        token = self.connection_token(owner_id, int(connection_id))
        payload = {
            "title": (title or "FDEX Agent changes")[:240],
            "head": head,
            "base": project["base_branch"],
            "body": body[:60000],
        }
        result = self._github_json(
            token,
            f"https://api.github.com/repos/{project['repo_full_name']}/pulls",
            method="POST",
            payload=payload,
        )
        url = str(result.get("html_url") or "")
        if not url:
            raise RuntimeError("GitHub did not return a pull request URL")
        return url

    def list_repositories(
        self,
        owner_id: str,
        connection_id: int,
        *,
        page: int = 1,
        per_page: int = 100,
        query: str = "",
    ) -> list[dict[str, Any]]:
        """List only repositories visible to this owner's authenticated GitHub identity."""
        owner_id = _safe_scope(owner_id)
        page = max(1, min(int(page), 1000))
        per_page = max(1, min(int(per_page), 100))
        token = self.connection_token(owner_id, int(connection_id))
        params = urlencode(
            {
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "direction": "desc",
                "page": page,
                "per_page": per_page,
            }
        )
        result = self._github_api(token, f"{GITHUB_API_URL}/user/repos?{params}")
        if not isinstance(result, list):
            raise ValueError("unexpected GitHub repository response")
        needle = (query or "").strip().casefold()[:100]
        repositories: list[dict[str, Any]] = []
        for raw in result:
            if not isinstance(raw, dict):
                continue
            full_name = str(raw.get("full_name") or "").strip()
            try:
                full_name = _safe_repo(full_name)
            except ValueError:
                continue
            description = str(raw.get("description") or "").strip()[:500]
            if needle and needle not in full_name.casefold() and needle not in description.casefold():
                continue
            permissions = raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
            can_push = bool(permissions.get("push") or permissions.get("maintain") or permissions.get("admin"))
            repositories.append(
                {
                    "id": int(raw.get("id") or 0),
                    "name": str(raw.get("name") or full_name.rsplit("/", 1)[-1])[:100],
                    "full_name": full_name,
                    "private": bool(raw.get("private")),
                    "default_branch": _safe_branch(str(raw.get("default_branch") or "main")),
                    "can_push": can_push,
                    "archived": bool(raw.get("archived")),
                    "description": description,
                    "updated_at": str(raw.get("updated_at") or "")[:40],
                }
            )
        return repositories

    def connection_token(self, owner_id: str, connection_id: int) -> str:
        """Return a usable token, rotating an expiring Device OAuth token when needed."""
        owner_id = _safe_scope(owner_id)
        connection = self.get_connection(owner_id, connection_id, secret=True)
        if connection.get("needs_reconnect"):
            raise ValueError("GitHub authorization needs to be reconnected")
        token = str(connection.get("token") or "")
        if not token:
            raise ValueError("GitHub authorization has no access token")
        if str(connection.get("auth_type") or "pat") != "oauth":
            return token
        expires_at = _as_time(str(connection.get("token_expires_at") or ""))
        skew = fresh_settings().fdex_github_oauth_refresh_skew_seconds
        if expires_at is None or expires_at > _utcnow() + timedelta(seconds=skew):
            return token
        return self._refresh_oauth_token(owner_id, connection_id)

    def _refresh_oauth_token(self, owner_id: str, connection_id: int) -> str:
        lock_root = self.db_path.parent / "github-oauth-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / f"connection-{int(connection_id)}.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            connection = self.get_connection(owner_id, connection_id, secret=True)
            expires_at = _as_time(str(connection.get("token_expires_at") or ""))
            skew = fresh_settings().fdex_github_oauth_refresh_skew_seconds
            if expires_at is None or expires_at > _utcnow() + timedelta(seconds=skew):
                return str(connection.get("token") or "")
            refresh_token = str(connection.get("refresh_token") or "")
            refresh_expires_at = _as_time(str(connection.get("refresh_expires_at") or ""))
            if not refresh_token or (refresh_expires_at and refresh_expires_at <= _utcnow()):
                self._mark_reconnect(owner_id, connection_id)
                raise ValueError("GitHub authorization expired; reconnect GitHub")
            client_id = str(connection.get("oauth_client_id") or "").strip() or fresh_settings().fdex_github_oauth_client_id.strip()
            if not client_id:
                raise RuntimeError("GitHub Device OAuth is not configured")
            result = self._oauth_post(
                GITHUB_ACCESS_TOKEN_URL,
                {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token},
            )
            if result.get("error"):
                self._mark_reconnect(owner_id, connection_id)
                raise ValueError(str(result.get("error_description") or result["error"])[:500])
            token = str(result.get("access_token") or "").strip()
            if not token:
                raise RuntimeError("GitHub OAuth refresh returned no access token")
            profile = self._github_json(token, f"{GITHUB_API_URL}/user")
            if str(profile.get("id") or "") != str(connection.get("github_user_id") or ""):
                self._mark_reconnect(owner_id, connection_id)
                raise ValueError("GitHub OAuth identity changed; reconnect GitHub")
            replacement_refresh = str(result.get("refresh_token") or refresh_token).strip()
            expires_in = self._integer(result.get("expires_in"), 0)
            refresh_expires_in = self._integer(result.get("refresh_token_expires_in"), 0)
            now = _utcnow()
            with self.owner_db(owner_id, "github_oauth_refresh") as conn:
                conn.execute(
                    """UPDATE github_connections
                       SET token_cipher=?,refresh_token_cipher=?,token_expires_at=?,refresh_expires_at=?,
                           scope=?,login=?,needs_reconnect=0,updated_at=?
                       WHERE id=? AND owner_id=?""",
                    (
                        self.encrypt(token),
                        self.encrypt(replacement_refresh),
                        _after(expires_in, now=now) if expires_in > 0 else "",
                        _after(refresh_expires_in, now=now) if refresh_expires_in > 0 else str(connection.get("refresh_expires_at") or ""),
                        str(result.get("scope") or connection.get("scope") or ""),
                        str(profile.get("login") or connection.get("login") or ""),
                        now.isoformat(timespec="seconds"),
                        connection_id,
                        owner_id,
                    ),
                )
            return token

    def _mark_reconnect(self, owner_id: str, connection_id: int) -> None:
        with self.db() as conn:
            conn.execute(
                "UPDATE github_connections SET needs_reconnect=1,updated_at=? WHERE id=? AND owner_id=?",
                (_now(), connection_id, owner_id),
            )

    def _git_env(self, owner_id: str, connection_id: Any, *, required: bool = False) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if connection_id:
            token = self.connection_token(owner_id, int(connection_id))
            basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
                }
            )
        elif required:
            raise ValueError("GitHub connection is required")
        return env

    @staticmethod
    def _git(args: tuple[str, ...], *, cwd: Path, env: dict[str, str], timeout: int) -> str:
        result = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False)
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise RuntimeError(output[-4000:] or f"git exited with {result.returncode}")
        return output[-20000:]

    @staticmethod
    def _integer(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _oauth_post(url: str, form: dict[str, str]) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "fdex-agent"}
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.post(url, headers=headers, data=form)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"GitHub OAuth request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub OAuth HTTP {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("GitHub OAuth returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("unexpected GitHub OAuth response")
        return data

    @staticmethod
    def _github_api(token: str, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fdex-agent",
        }
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                response = client.request(method, url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ValueError(f"GitHub API request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ValueError(f"GitHub API HTTP {response.status_code}: {response.text[:500]}")
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError("GitHub API returned invalid JSON") from exc

    @classmethod
    def _github_json(cls, token: str, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = cls._github_api(token, url, method=method, payload=payload)
        if not isinstance(data, dict):
            raise ValueError("unexpected GitHub API response")
        return data


@lru_cache(maxsize=1)
def agent_project_store() -> AgentProjectStore:
    store = AgentProjectStore()
    store.init()
    return store
