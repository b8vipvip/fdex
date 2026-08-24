from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from app.config import fresh_settings

_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "agent-accounts.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AgentAccountStore:
    """Server-side identity boundary for Coding Agent accounts.

    The current Android application does not yet have a general server login system, so
    Phase 6 introduces a scoped Agent credential. The global FDEX_AGENT_ACCESS_TOKEN is
    used only as an enrollment/bootstrap secret. Normal Agent, GitHub and project calls
    use an account-specific opaque token after enrollment.
    """

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
                CREATE TABLE IF NOT EXISTS agent_accounts (
                    owner_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_account_enabled
                    ON agent_accounts(enabled, owner_id);
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
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def enroll(self, label: str = "") -> tuple[dict[str, object], str]:
        self.init()
        owner_id = "acct_" + uuid.uuid4().hex[:24]
        token = secrets.token_urlsafe(48)
        now = _now()
        with self.db() as conn:
            conn.execute(
                "INSERT INTO agent_accounts(owner_id,label,token_hash,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (owner_id, (label or "FDEX account").strip()[:100], _token_hash(token), 1, now, now),
            )
        return self.get(owner_id), token

    def authenticate(self, token: str) -> dict[str, object] | None:
        self.init()
        clean = (token or "").strip()
        if len(clean) < 32:
            return None
        digest = _token_hash(clean)
        with self.db() as conn:
            row = conn.execute(
                "SELECT owner_id,label,enabled,created_at,updated_at FROM agent_accounts WHERE token_hash=?",
                (digest,),
            ).fetchone()
        if row is None or not bool(row["enabled"]):
            return None
        return dict(row)

    def get(self, owner_id: str) -> dict[str, object]:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT owner_id,label,enabled,created_at,updated_at FROM agent_accounts WHERE owner_id=?",
                ((owner_id or "").strip(),),
            ).fetchone()
        if row is None:
            raise KeyError("Agent account not found")
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        return data


@lru_cache(maxsize=1)
def agent_account_store() -> AgentAccountStore:
    store = AgentAccountStore()
    store.init()
    return store
