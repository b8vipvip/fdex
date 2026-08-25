from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.account_operations import account_operation_status_by_hash
from app.config import fresh_settings


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _owner_hash(user_id: str) -> str:
    clean = (user_id or "").strip()
    if not clean.startswith("usr_") or len(clean) < 12:
        raise ValueError("invalid FDEX user id")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _scope_key(value: str) -> str:
    clean = (value or "").strip()
    if len(clean) < 24 or len(clean) > 128:
        raise ValueError("invalid FDEX memory scope")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in clean):
        raise ValueError("invalid FDEX memory scope")
    return clean


class MemoryScopeRegistry:
    """Maps server-bound remote-memory scopes back to the canonical FDEX account.

    The bound scope itself is already a one-way SHA-256 namespace. The registry stores only
    SHA-256(user_id) plus that opaque scope, never email, chat text, tokens, embeddings or
    GitHub secrets. This makes account-wide export/erasure possible even when one account
    has several Android devices with independently generated local memory scope tokens.
    """

    def __init__(self, path: Path | None = None) -> None:
        memory_dir = Path(fresh_settings().fdex_memory_data_dir).expanduser().resolve()
        self.path = (path or memory_dir / "memory-scope-owners.sqlite3").resolve()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_scope_owners (
                    scope_key TEXT PRIMARY KEY,
                    owner_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_scope_owner ON memory_scope_owners(owner_hash,last_seen_at DESC)"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def register(self, user_id: str, scope_key: str) -> str:
        self.init()
        owner_hash = _owner_hash(user_id)
        scope = _scope_key(scope_key)
        now = _now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT owner_hash FROM memory_scope_owners WHERE scope_key=?",
                (scope,),
            ).fetchone()
            if existing is not None and str(existing["owner_hash"]) != owner_hash:
                raise RuntimeError("FDEX memory scope ownership conflict")
            conn.execute(
                """
                INSERT INTO memory_scope_owners(scope_key,owner_hash,first_seen_at,last_seen_at)
                VALUES(?,?,?,?)
                ON CONFLICT(scope_key) DO UPDATE SET last_seen_at=excluded.last_seen_at
                """,
                (scope, owner_hash, now, now),
            )
        return scope

    def scopes_for_user(self, user_id: str) -> list[str]:
        self.init()
        owner_hash = _owner_hash(user_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT scope_key FROM memory_scope_owners WHERE owner_hash=? ORDER BY first_seen_at,scope_key",
                (owner_hash,),
            ).fetchall()
        return [str(row["scope_key"]) for row in rows]

    def owner_hash_for_scope(self, scope_key: str) -> str:
        self.init()
        scope = _scope_key(scope_key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_hash FROM memory_scope_owners WHERE scope_key=?",
                (scope,),
            ).fetchone()
        return str(row["owner_hash"]) if row is not None else ""

    def write_blocked(self, scope_key: str) -> bool:
        owner_hash = self.owner_hash_for_scope(scope_key)
        return bool(owner_hash and account_operation_status_by_hash(owner_hash).busy)

    def scope_count(self, user_id: str) -> int:
        return len(self.scopes_for_user(user_id))


@lru_cache(maxsize=1)
def memory_scope_registry() -> MemoryScopeRegistry:
    registry = MemoryScopeRegistry()
    registry.init()
    return registry
