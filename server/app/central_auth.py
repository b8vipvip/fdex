from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from app.config import fresh_settings

_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "fdex-accounts.db"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        + base64.urlsafe_b64encode(salt).decode("ascii")
        + "$"
        + base64.urlsafe_b64encode(derived).decode("ascii")
    )


def _password_verify(record: str, password: str) -> bool:
    try:
        kind, n, r, p, salt64, hash64 = record.split("$", 5)
        if kind != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


class CentralAuthStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path.resolve()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    company_name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    access_hash TEXT NOT NULL UNIQUE,
                    refresh_hash TEXT NOT NULL UNIQUE,
                    device_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    access_expires_at TEXT NOT NULL,
                    refresh_expires_at TEXT NOT NULL,
                    revoked_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS idx_fdex_sessions_user ON user_sessions(user_id, revoked_at, updated_at);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def normalize_email(email: str) -> str:
        value = (email or "").strip().lower()
        if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
            raise ValueError("请输入正确邮箱")
        return value

    def register(self, *, name: str, email: str, password: str, company_name: str = "", device_name: str = "") -> dict[str, object]:
        self.init()
        email = self.normalize_email(email)
        name = (name or "").strip()[:100]
        if not name:
            raise ValueError("请输入姓名")
        if len(password) < 8 or len(password) > 256:
            raise ValueError("密码长度必须为 8-256 位")
        now = _now()
        user_id = "usr_" + uuid.uuid4().hex[:24]
        try:
            with self.db() as conn:
                conn.execute(
                    "INSERT INTO users(id,email,password_hash,name,company_name,enabled,created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id, email, _password_hash(password), name, (company_name or "").strip()[:120], 1, _iso(now), _iso(now), _iso(now)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该邮箱已经注册") from exc
        return self._issue_session(user_id, device_name=device_name)

    def login(self, *, email: str, password: str, device_name: str = "") -> dict[str, object]:
        self.init()
        email = self.normalize_email(email)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row is None or not bool(row["enabled"]) or not _password_verify(str(row["password_hash"]), password):
            raise ValueError("邮箱或密码错误")
        now = _now()
        with self.db() as conn:
            conn.execute("UPDATE users SET last_login_at=?,updated_at=? WHERE id=?", (_iso(now), _iso(now), row["id"]))
        return self._issue_session(str(row["id"]), device_name=device_name)

    def _issue_session(self, user_id: str, *, device_name: str = "") -> dict[str, object]:
        settings = fresh_settings()
        now = _now()
        access_exp = now + timedelta(minutes=settings.fdex_auth_access_minutes)
        refresh_exp = now + timedelta(days=settings.fdex_auth_refresh_days)
        session_id = "ses_" + uuid.uuid4().hex[:24]
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(64)
        with self.db() as conn:
            conn.execute(
                "INSERT INTO user_sessions(id,user_id,access_hash,refresh_hash,device_name,created_at,updated_at,access_expires_at,refresh_expires_at,revoked_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (session_id, user_id, _hash_token(access), _hash_token(refresh), (device_name or "Android").strip()[:120], _iso(now), _iso(now), _iso(access_exp), _iso(refresh_exp), ""),
            )
        return {"user": self.get_user(user_id), "access_token": access, "refresh_token": refresh, "access_expires_at": _iso(access_exp), "refresh_expires_at": _iso(refresh_exp), "session_id": session_id}

    def authenticate_access(self, token: str) -> dict[str, object] | None:
        clean = (token or "").strip()
        if len(clean) < 32:
            return None
        now = _iso(_now())
        with self.db() as conn:
            row = conn.execute(
                "SELECT s.id AS session_id,u.* FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.access_hash=? AND s.revoked_at='' AND s.access_expires_at>? AND u.enabled=1",
                (_hash_token(clean), now),
            ).fetchone()
        return self._public_user(dict(row), session_id=str(row["session_id"])) if row else None

    def refresh(self, refresh_token: str) -> dict[str, object]:
        clean = (refresh_token or "").strip()
        now_dt = _now()
        now = _iso(now_dt)
        with self.db() as conn:
            row = conn.execute(
                "SELECT s.*,u.enabled FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.refresh_hash=? AND s.revoked_at='' AND s.refresh_expires_at>?",
                (_hash_token(clean), now),
            ).fetchone()
        if row is None or not bool(row["enabled"]):
            raise ValueError("登录状态已失效，请重新登录")
        settings = fresh_settings()
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(64)
        access_exp = now_dt + timedelta(minutes=settings.fdex_auth_access_minutes)
        refresh_exp = now_dt + timedelta(days=settings.fdex_auth_refresh_days)
        with self.db() as conn:
            conn.execute(
                "UPDATE user_sessions SET access_hash=?,refresh_hash=?,updated_at=?,access_expires_at=?,refresh_expires_at=? WHERE id=?",
                (_hash_token(access), _hash_token(refresh), now, _iso(access_exp), _iso(refresh_exp), row["id"]),
            )
        return {"user": self.get_user(str(row["user_id"])), "access_token": access, "refresh_token": refresh, "access_expires_at": _iso(access_exp), "refresh_expires_at": _iso(refresh_exp), "session_id": row["id"]}

    def revoke_access(self, access_token: str) -> None:
        now = _iso(_now())
        with self.db() as conn:
            conn.execute("UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE access_hash=? AND revoked_at=''", (now, now, _hash_token((access_token or "").strip())))

    def revoke_user_sessions(self, user_id: str) -> int:
        self.init()
        self.get_user(user_id)
        now = _iso(_now())
        with self.db() as conn:
            cursor = conn.execute(
                "UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE user_id=? AND revoked_at=''",
                (now, now, user_id),
            )
            return int(cursor.rowcount or 0)

    def set_user_enabled(self, user_id: str, enabled: bool) -> dict[str, object]:
        self.init()
        now = _iso(_now())
        with self.db() as conn:
            cursor = conn.execute(
                "UPDATE users SET enabled=?,updated_at=? WHERE id=?",
                (1 if enabled else 0, now, user_id),
            )
            if not cursor.rowcount:
                raise KeyError("FDEX user not found")
            if not enabled:
                conn.execute(
                    "UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE user_id=? AND revoked_at=''",
                    (now, now, user_id),
                )
        return self.get_user(user_id)

    def list_sessions(self, user_id: str) -> list[dict[str, object]]:
        self.init()
        self.get_user(user_id)
        now = _iso(_now())
        with self.db() as conn:
            rows = conn.execute(
                "SELECT id,device_name,created_at,updated_at,access_expires_at,refresh_expires_at,revoked_at FROM user_sessions WHERE user_id=? ORDER BY updated_at DESC,id DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "device_name": str(row["device_name"] or ""),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "access_expires_at": str(row["access_expires_at"] or ""),
                "refresh_expires_at": str(row["refresh_expires_at"] or ""),
                "revoked_at": str(row["revoked_at"] or ""),
                "active": not bool(row["revoked_at"]) and str(row["refresh_expires_at"] or "") > now,
            }
            for row in rows
        ]

    def list_users(self) -> list[dict[str, object]]:
        self.init()
        now = _iso(_now())
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT
                    u.*,
                    COUNT(s.id) AS session_count,
                    COALESCE(SUM(CASE WHEN s.revoked_at='' AND s.refresh_expires_at>? THEN 1 ELSE 0 END),0) AS active_session_count,
                    COALESCE(MAX(s.updated_at),'') AS latest_session_at
                FROM users u
                LEFT JOIN user_sessions s ON s.user_id=u.id
                GROUP BY u.id
                ORDER BY u.created_at DESC,u.id DESC
                """,
                (now,),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = self._public_user(dict(row))
            item["session_count"] = int(row["session_count"] or 0)
            item["active_session_count"] = int(row["active_session_count"] or 0)
            item["latest_session_at"] = str(row["latest_session_at"] or "")
            result.append(item)
        return result

    def user_stats(self) -> dict[str, int]:
        users = self.list_users()
        return {
            "total": len(users),
            "enabled": sum(1 for user in users if bool(user.get("enabled"))),
            "disabled": sum(1 for user in users if not bool(user.get("enabled"))),
            "active_sessions": sum(int(user.get("active_session_count") or 0) for user in users),
        }

    def get_user(self, user_id: str) -> dict[str, object]:
        self.init()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError("FDEX user not found")
        return self._public_user(dict(row))

    @staticmethod
    def _public_user(row: dict[str, object], *, session_id: str = "") -> dict[str, object]:
        data = {
            "id": str(row["id"]),
            "email": str(row["email"]),
            "name": str(row["name"]),
            "company_name": str(row.get("company_name") or ""),
            "enabled": bool(row["enabled"]),
            "created_at": str(row["created_at"]),
            "last_login_at": str(row.get("last_login_at") or ""),
        }
        if session_id:
            data["session_id"] = session_id
        return data


@lru_cache(maxsize=1)
def central_auth_store() -> CentralAuthStore:
    store = CentralAuthStore()
    store.init()
    return store
