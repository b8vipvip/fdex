from __future__ import annotations

import base64
import hashlib
import hmac
import json
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


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


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


def _validate_password(password: str) -> str:
    value = password or ""
    if len(value) < 8 or len(value) > 256:
        raise ValueError("密码长度必须为 8-256 位")
    return value


class AuthRateLimitError(ValueError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"登录失败次数过多，请 {self.retry_after_seconds} 秒后再试")


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
                CREATE TABLE IF NOT EXISTS password_reset_codes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    used_at TEXT NOT NULL DEFAULT '',
                    request_ip TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS idx_fdex_reset_user ON password_reset_codes(user_id, used_at, created_at);
                CREATE TABLE IF NOT EXISTS login_failures (
                    failure_key TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    client_ip TEXT NOT NULL DEFAULT '',
                    first_failed_at TEXT NOT NULL,
                    last_failed_at TEXT NOT NULL,
                    failures INTEGER NOT NULL DEFAULT 0,
                    blocked_until TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS auth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    event TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0,
                    risk TEXT NOT NULL DEFAULT '',
                    client_ip TEXT NOT NULL DEFAULT '',
                    device_name TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fdex_auth_events_user ON auth_events(user_id, created_at DESC, id DESC);
                """
            )
            self._ensure_column(conn, "user_sessions", "client_ip", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "user_sessions", "user_agent", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "user_sessions", "last_seen_at", "TEXT NOT NULL DEFAULT ''")
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
        existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

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

    @staticmethod
    def _clean_device(value: str) -> str:
        return (value or "Android").strip()[:120]

    @staticmethod
    def _clean_ip(value: str) -> str:
        return (value or "").strip()[:64]

    @staticmethod
    def _clean_user_agent(value: str) -> str:
        return (value or "").strip()[:300]

    def _event(
        self,
        event: str,
        *,
        user_id: str = "",
        email: str = "",
        success: bool,
        risk: str = "",
        client_ip: str = "",
        device_name: str = "",
        user_agent: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        self.init()
        payload = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))[:2000]
        with self.db() as conn:
            conn.execute(
                "INSERT INTO auth_events(user_id,email,event,success,risk,client_ip,device_name,user_agent,details,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    user_id[:80], email[:254], event[:80], 1 if success else 0, risk[:80],
                    self._clean_ip(client_ip), self._clean_device(device_name), self._clean_user_agent(user_agent), payload, _iso(_now()),
                ),
            )

    def register(
        self,
        *,
        name: str,
        email: str,
        password: str,
        company_name: str = "",
        device_name: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, object]:
        self.init()
        email = self.normalize_email(email)
        name = (name or "").strip()[:100]
        if not name:
            raise ValueError("请输入姓名")
        _validate_password(password)
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
        session = self._issue_session(user_id, device_name=device_name, client_ip=client_ip, user_agent=user_agent)
        self._event("register", user_id=user_id, email=email, success=True, client_ip=client_ip, device_name=device_name, user_agent=user_agent)
        return session

    def _failure_key(self, email: str, client_ip: str) -> str:
        return _hash_token(f"{email}|{self._clean_ip(client_ip)}")

    def login_retry_after(self, email: str, client_ip: str) -> int:
        self.init()
        email = self.normalize_email(email)
        with self.db() as conn:
            row = conn.execute("SELECT blocked_until FROM login_failures WHERE failure_key=?", (self._failure_key(email, client_ip),)).fetchone()
        if row is None:
            return 0
        blocked = _parse_iso(str(row["blocked_until"] or ""))
        if blocked is None:
            return 0
        seconds = int((blocked - _now()).total_seconds())
        return max(0, seconds)

    def _record_login_failure(self, email: str, client_ip: str) -> int:
        settings = fresh_settings()
        now = _now()
        key = self._failure_key(email, client_ip)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM login_failures WHERE failure_key=?", (key,)).fetchone()
            failures = 0
            first = now
            if row is not None:
                first_value = _parse_iso(str(row["first_failed_at"] or ""))
                if first_value is not None and first_value >= now - timedelta(minutes=settings.fdex_auth_login_window_minutes):
                    first = first_value
                    failures = int(row["failures"] or 0)
            failures += 1
            blocked_until = ""
            if failures >= settings.fdex_auth_login_max_failures:
                blocked_until = _iso(now + timedelta(minutes=settings.fdex_auth_login_block_minutes))
            conn.execute(
                "INSERT OR REPLACE INTO login_failures(failure_key,email,client_ip,first_failed_at,last_failed_at,failures,blocked_until) VALUES(?,?,?,?,?,?,?)",
                (key, email, self._clean_ip(client_ip), _iso(first), _iso(now), failures, blocked_until),
            )
        return self.login_retry_after(email, client_ip)

    def _clear_login_failures(self, email: str, client_ip: str) -> None:
        with self.db() as conn:
            conn.execute("DELETE FROM login_failures WHERE failure_key=?", (self._failure_key(email, client_ip),))

    def login(
        self,
        *,
        email: str,
        password: str,
        device_name: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, object]:
        self.init()
        email = self.normalize_email(email)
        retry_after = self.login_retry_after(email, client_ip)
        if retry_after > 0:
            self._event("login_rate_limited", email=email, success=False, risk="rate_limited", client_ip=client_ip, device_name=device_name, user_agent=user_agent)
            raise AuthRateLimitError(retry_after)

        with self.db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row is None or not bool(row["enabled"]) or not _password_verify(str(row["password_hash"]), password):
            retry_after = self._record_login_failure(email, client_ip)
            self._event(
                "login_failed", user_id=str(row["id"]) if row is not None else "", email=email, success=False,
                risk="repeated_failures" if retry_after > 0 else "", client_ip=client_ip, device_name=device_name, user_agent=user_agent,
            )
            if retry_after > 0:
                raise AuthRateLimitError(retry_after)
            raise ValueError("邮箱或密码错误")

        user_id = str(row["id"])
        clean_device = self._clean_device(device_name)
        clean_ip = self._clean_ip(client_ip)
        with self.db() as conn:
            history = conn.execute(
                "SELECT device_name,client_ip FROM user_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                (user_id,),
            ).fetchall()
        risks: list[str] = []
        if history and clean_device and not any(str(item["device_name"] or "") == clean_device for item in history):
            risks.append("new_device")
        if history and clean_ip and not any(str(item["client_ip"] or "") == clean_ip for item in history):
            risks.append("new_ip")

        self._clear_login_failures(email, client_ip)
        now = _now()
        with self.db() as conn:
            conn.execute("UPDATE users SET last_login_at=?,updated_at=? WHERE id=?", (_iso(now), _iso(now), user_id))
        session = self._issue_session(user_id, device_name=clean_device, client_ip=clean_ip, user_agent=user_agent)
        self._event(
            "login_success", user_id=user_id, email=email, success=True, risk=",".join(risks),
            client_ip=clean_ip, device_name=clean_device, user_agent=user_agent,
            details={"anomaly": bool(risks)},
        )
        return session

    def _issue_session(
        self,
        user_id: str,
        *,
        device_name: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, object]:
        settings = fresh_settings()
        now = _now()
        access_exp = now + timedelta(minutes=settings.fdex_auth_access_minutes)
        refresh_exp = now + timedelta(days=settings.fdex_auth_refresh_days)
        session_id = "ses_" + uuid.uuid4().hex[:24]
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(64)
        with self.db() as conn:
            conn.execute(
                "INSERT INTO user_sessions(id,user_id,access_hash,refresh_hash,device_name,created_at,updated_at,access_expires_at,refresh_expires_at,revoked_at,client_ip,user_agent,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id, user_id, _hash_token(access), _hash_token(refresh), self._clean_device(device_name),
                    _iso(now), _iso(now), _iso(access_exp), _iso(refresh_exp), "", self._clean_ip(client_ip),
                    self._clean_user_agent(user_agent), _iso(now),
                ),
            )
        return {
            "user": self.get_user(user_id), "access_token": access, "refresh_token": refresh,
            "access_expires_at": _iso(access_exp), "refresh_expires_at": _iso(refresh_exp), "session_id": session_id,
        }

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

    def refresh(self, refresh_token: str, *, client_ip: str = "", user_agent: str = "") -> dict[str, object]:
        clean = (refresh_token or "").strip()
        now_dt = _now()
        now = _iso(now_dt)
        with self.db() as conn:
            row = conn.execute(
                "SELECT s.*,u.enabled,u.email FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.refresh_hash=? AND s.revoked_at='' AND s.refresh_expires_at>?",
                (_hash_token(clean), now),
            ).fetchone()
        if row is None or not bool(row["enabled"]):
            raise ValueError("登录状态已失效，请重新登录")
        settings = fresh_settings()
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(64)
        access_exp = now_dt + timedelta(minutes=settings.fdex_auth_access_minutes)
        refresh_exp = now_dt + timedelta(days=settings.fdex_auth_refresh_days)
        clean_ip = self._clean_ip(client_ip) or str(row["client_ip"] or "")
        clean_agent = self._clean_user_agent(user_agent) or str(row["user_agent"] or "")
        with self.db() as conn:
            conn.execute(
                "UPDATE user_sessions SET access_hash=?,refresh_hash=?,updated_at=?,access_expires_at=?,refresh_expires_at=?,client_ip=?,user_agent=?,last_seen_at=? WHERE id=?",
                (_hash_token(access), _hash_token(refresh), now, _iso(access_exp), _iso(refresh_exp), clean_ip, clean_agent, now, row["id"]),
            )
        return {
            "user": self.get_user(str(row["user_id"])), "access_token": access, "refresh_token": refresh,
            "access_expires_at": _iso(access_exp), "refresh_expires_at": _iso(refresh_exp), "session_id": row["id"],
        }

    def revoke_access(self, access_token: str) -> None:
        now = _iso(_now())
        with self.db() as conn:
            conn.execute("UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE access_hash=? AND revoked_at=''", (now, now, _hash_token((access_token or "").strip())))

    def revoke_user_sessions(self, user_id: str, *, except_session_id: str = "") -> int:
        self.init()
        self.get_user(user_id)
        now = _iso(_now())
        sql = "UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE user_id=? AND revoked_at=''"
        args: list[object] = [now, now, user_id]
        if except_session_id:
            sql += " AND id<>?"
            args.append(except_session_id)
        with self.db() as conn:
            cursor = conn.execute(sql, tuple(args))
            return int(cursor.rowcount or 0)

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        self.init()
        now = _iso(_now())
        with self.db() as conn:
            cursor = conn.execute(
                "UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE id=? AND user_id=? AND revoked_at=''",
                (now, now, session_id, user_id),
            )
        if cursor.rowcount:
            user = self.get_user(user_id)
            self._event("session_revoked", user_id=user_id, email=str(user["email"]), success=True, details={"session_id": session_id})
            return True
        return False

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
                "SELECT id,device_name,client_ip,user_agent,created_at,updated_at,last_seen_at,access_expires_at,refresh_expires_at,revoked_at FROM user_sessions WHERE user_id=? ORDER BY updated_at DESC,id DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]), "device_name": str(row["device_name"] or ""), "client_ip": str(row["client_ip"] or ""),
                "user_agent": str(row["user_agent"] or ""), "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""), "last_seen_at": str(row["last_seen_at"] or row["updated_at"] or ""),
                "access_expires_at": str(row["access_expires_at"] or ""), "refresh_expires_at": str(row["refresh_expires_at"] or ""),
                "revoked_at": str(row["revoked_at"] or ""),
                "active": not bool(row["revoked_at"]) and str(row["refresh_expires_at"] or "") > now,
            }
            for row in rows
        ]

    def verify_password(self, user_id: str, password: str) -> bool:
        self.init()
        with self.db() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id=? AND enabled=1", (user_id,)).fetchone()
        return bool(row is not None and _password_verify(str(row["password_hash"]), password))

    def change_password(self, user_id: str, current_password: str, new_password: str, *, current_session_id: str = "") -> int:
        self.init()
        _validate_password(new_password)
        if not self.verify_password(user_id, current_password):
            raise ValueError("当前密码错误")
        if hmac.compare_digest(current_password, new_password):
            raise ValueError("新密码不能与当前密码相同")
        now = _iso(_now())
        with self.db() as conn:
            conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (_password_hash(new_password), now, user_id))
            conn.execute("UPDATE password_reset_codes SET used_at=? WHERE user_id=? AND used_at=''", (now, user_id))
        revoked = self.revoke_user_sessions(user_id, except_session_id=current_session_id)
        user = self.get_user(user_id)
        self._event(
            "password_changed", user_id=user_id, email=str(user["email"]), success=True,
            details={"other_sessions_revoked": revoked},
        )
        return revoked

    def create_password_reset_code(self, email: str, *, client_ip: str = "") -> tuple[dict[str, object], str] | None:
        self.init()
        email = self.normalize_email(email)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=? AND enabled=1", (email,)).fetchone()
        if row is None:
            self._event("password_reset_requested", email=email, success=True, risk="unknown_email", client_ip=client_ip)
            return None
        user_id = str(row["id"])
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = _now()
        expires = now + timedelta(minutes=fresh_settings().fdex_auth_reset_code_minutes)
        reset_id = "rst_" + uuid.uuid4().hex[:24]
        code_hash = _hash_token(f"{reset_id}:{code}")
        with self.db() as conn:
            conn.execute("UPDATE password_reset_codes SET used_at=? WHERE user_id=? AND used_at=''", (_iso(now), user_id))
            conn.execute(
                "INSERT INTO password_reset_codes(id,user_id,email,code_hash,created_at,expires_at,attempts,used_at,request_ip) VALUES(?,?,?,?,?,?,?,?,?)",
                (reset_id, user_id, email, code_hash, _iso(now), _iso(expires), 0, "", self._clean_ip(client_ip)),
            )
        self._event("password_reset_requested", user_id=user_id, email=email, success=True, client_ip=client_ip)
        return self._public_user(dict(row)), f"{reset_id}.{code}"

    def confirm_password_reset(self, email: str, code: str, new_password: str, *, client_ip: str = "") -> None:
        self.init()
        email = self.normalize_email(email)
        _validate_password(new_password)
        raw = (code or "").strip()
        reset_id, dot, digits = raw.partition(".")
        if not dot or not reset_id.startswith("rst_") or not digits.isdigit() or len(digits) != 6:
            raise ValueError("验证码错误或已过期")
        with self.db() as conn:
            row = conn.execute(
                "SELECT r.*,u.enabled FROM password_reset_codes r JOIN users u ON u.id=r.user_id WHERE r.id=? AND r.email=?",
                (reset_id, email),
            ).fetchone()
        if row is None or not bool(row["enabled"]) or bool(row["used_at"]):
            raise ValueError("验证码错误或已过期")
        expiry = _parse_iso(str(row["expires_at"] or ""))
        settings = fresh_settings()
        if expiry is None or expiry <= _now() or int(row["attempts"] or 0) >= settings.fdex_auth_reset_max_attempts:
            raise ValueError("验证码错误或已过期")
        if not hmac.compare_digest(str(row["code_hash"]), _hash_token(f"{reset_id}:{digits}")):
            with self.db() as conn:
                conn.execute("UPDATE password_reset_codes SET attempts=attempts+1 WHERE id=?", (reset_id,))
            self._event("password_reset_code_failed", user_id=str(row["user_id"]), email=email, success=False, risk="invalid_code", client_ip=client_ip)
            raise ValueError("验证码错误或已过期")
        now = _iso(_now())
        user_id = str(row["user_id"])
        with self.db() as conn:
            conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (_password_hash(new_password), now, user_id))
            conn.execute("UPDATE password_reset_codes SET used_at=? WHERE id=?", (now, reset_id))
            conn.execute("UPDATE user_sessions SET revoked_at=?,updated_at=? WHERE user_id=? AND revoked_at=''", (now, now, user_id))
            conn.execute("DELETE FROM login_failures WHERE email=?", (email,))
        self._event("password_reset_completed", user_id=user_id, email=email, success=True, client_ip=client_ip)

    def security_events(self, user_id: str, limit: int = 30) -> list[dict[str, object]]:
        self.init()
        self.get_user(user_id)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT event,success,risk,client_ip,device_name,user_agent,details,created_at FROM auth_events WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            {
                "event": str(row["event"]), "success": bool(row["success"]), "risk": str(row["risk"] or ""),
                "client_ip": str(row["client_ip"] or ""), "device_name": str(row["device_name"] or ""),
                "user_agent": str(row["user_agent"] or ""), "details": str(row["details"] or ""), "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def delete_account(self, user_id: str, password: str) -> dict[str, object]:
        self.init()
        if not self.verify_password(user_id, password):
            raise ValueError("密码错误，无法注销账号")
        user = self.get_user(user_id)
        email = str(user["email"])
        with self.db() as conn:
            conn.execute("DELETE FROM password_reset_codes WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM auth_events WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM login_failures WHERE email=?", (email,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return user

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
            "id": str(row["id"]), "email": str(row["email"]), "name": str(row["name"]),
            "company_name": str(row.get("company_name") or ""), "enabled": bool(row["enabled"]),
            "created_at": str(row["created_at"]), "last_login_at": str(row.get("last_login_at") or ""),
        }
        if session_id:
            data["session_id"] = session_id
        return data


@lru_cache(maxsize=1)
def central_auth_store() -> CentralAuthStore:
    store = CentralAuthStore()
    store.init()
    return store
