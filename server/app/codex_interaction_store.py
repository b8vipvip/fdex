from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken

from app.codex_host_store import codex_host_store

_SUPPORTED = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
}
_TERMINAL = {"responded", "declined", "cancelled", "interrupted", "failed", "expired"}
_MAX_JSON = 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(raw.encode("utf-8")) <= _MAX_JSON:
        return raw
    return json.dumps(
        {
            "fdex_truncated": True,
            "original_bytes": len(raw.encode("utf-8")),
            "preview": raw[:200_000],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(raw or ""))
    except json.JSONDecodeError:
        return fallback


class CodexInteractionStore:
    """Durable cross-worker broker for interactive official app-server requests.

    JSON-RPC request ids restart for every app-server process and approval callbacks may have an
    additional approvalId, so FDEX assigns its own interaction id and stores the host-session id,
    rpc id and protocol ids separately. User-input responses can contain secret answers; response
    payloads are therefore encrypted at rest and destroyed as soon as the owning stdio Host claims
    them. Only a redacted completion marker remains for audit/UI history.
    """

    def __init__(self, path: Path | None = None, key_path: Path | None = None) -> None:
        host_path = codex_host_store().path
        self.path = (path or host_path).resolve()
        self.key_path = (key_path or self.path.with_name("codex-interactions.key")).resolve()
        self._initialized = False
        self._init_lock = threading.Lock()
        self._fernet: Fernet | None = None

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, key + b"\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = Fernet(key)
        return self._fernet

    def init(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            codex_host_store().init()
            self._cipher()
            with self.db() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS codex_interactions (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        host_session_id TEXT NOT NULL,
                        rpc_id TEXT NOT NULL,
                        method TEXT NOT NULL,
                        thread_id TEXT NOT NULL DEFAULT '',
                        turn_id TEXT NOT NULL DEFAULT '',
                        item_id TEXT NOT NULL DEFAULT '',
                        approval_id TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL DEFAULT 'pending',
                        blocking INTEGER NOT NULL DEFAULT 1,
                        request_json TEXT NOT NULL DEFAULT '{}',
                        response_cipher TEXT NOT NULL DEFAULT '',
                        response_summary_json TEXT NOT NULL DEFAULT '{}',
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        responded_at TEXT NOT NULL DEFAULT '',
                        consumed_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_codex_interaction_host_rpc
                        ON codex_interactions(host_session_id,rpc_id);
                    CREATE INDEX IF NOT EXISTS idx_codex_interaction_owner_task_state
                        ON codex_interactions(owner_id,task_id,state,created_at);
                    CREATE INDEX IF NOT EXISTS idx_codex_interaction_owner_thread_state
                        ON codex_interactions(owner_id,thread_id,state,created_at);
                    """
                )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._initialized = True

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["request"] = _parse(data.pop("request_json"), {})
        data["response_summary"] = _parse(data.pop("response_summary_json"), {})
        data.pop("response_cipher", None)
        data["blocking"] = bool(data.get("blocking"))
        return data

    def create(
        self,
        *,
        owner_id: str,
        task_id: str,
        host_session_id: str,
        rpc_id: int | str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.init()
        if method not in _SUPPORTED:
            raise ValueError("unsupported Codex interaction method")
        interaction_id = uuid.uuid4().hex
        rpc_text = str(rpc_id)
        thread_id = str(params.get("threadId") or "")[:240]
        turn_id = str(params.get("turnId") or "")[:240]
        item_id = str(params.get("itemId") or "")[:240]
        approval_id = str(params.get("approvalId") or "")[:240]
        blocking = bool(params.get("isBlocking", True)) if method == "item/tool/requestUserInput" else True
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO codex_interactions(
                    id,owner_id,task_id,host_session_id,rpc_id,method,thread_id,turn_id,item_id,
                    approval_id,state,blocking,request_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)
                """,
                (
                    interaction_id,
                    owner_id,
                    task_id,
                    host_session_id,
                    rpc_text,
                    method,
                    thread_id,
                    turn_id,
                    item_id,
                    approval_id,
                    1 if blocking else 0,
                    _json(params),
                    now,
                    now,
                ),
            )
        result = self.get(owner_id, interaction_id)
        assert result is not None
        return result

    def get(self, owner_id: str, interaction_id: str) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_interactions WHERE owner_id=? AND id=?",
                (owner_id, interaction_id),
            ).fetchone()
        return self._row(row)

    def list_for_task(self, owner_id: str, task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self.init()
        limit = max(1, min(int(limit), 300))
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codex_interactions WHERE owner_id=? AND task_id=?
                ORDER BY created_at DESC LIMIT ?
                """,
                (owner_id, task_id, limit),
            ).fetchall()
        return [self._row(row) for row in rows if row is not None]  # type: ignore[list-item]

    def active_count(self, owner_id: str) -> int:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM codex_interactions WHERE owner_id=? AND state IN ('pending','answered')",
                (owner_id,),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def submit_response(
        self,
        *,
        owner_id: str,
        interaction_id: str,
        response: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.init()
        now = _now()
        cipher = self._cipher().encrypt(_json(response).encode("utf-8")).decode("ascii")
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM codex_interactions WHERE owner_id=? AND id=?",
                (owner_id, interaction_id),
            ).fetchone()
            if row is None:
                raise KeyError("Codex interaction not found")
            if str(row["state"]) != "pending":
                raise ValueError("Codex interaction is no longer pending")
            conn.execute(
                """
                UPDATE codex_interactions SET state='answered',response_cipher=?,response_summary_json=?,
                    responded_at=?,updated_at=? WHERE owner_id=? AND id=?
                """,
                (cipher, _json(summary or {}), now, now, owner_id, interaction_id),
            )
        result = self.get(owner_id, interaction_id)
        assert result is not None
        return result

    def claim_response(self, *, owner_id: str, interaction_id: str, host_session_id: str) -> dict[str, Any] | None:
        """Atomically consume one answer and destroy its plaintext-equivalent ciphertext."""
        self.init()
        now = _now()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT state,response_cipher FROM codex_interactions
                WHERE owner_id=? AND id=? AND host_session_id=?
                """,
                (owner_id, interaction_id, host_session_id),
            ).fetchone()
            if row is None or str(row["state"]) != "answered":
                return None
            token = str(row["response_cipher"] or "")
            try:
                raw = self._cipher().decrypt(token.encode("ascii"))
                response = json.loads(raw.decode("utf-8"))
            except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
                conn.execute(
                    """UPDATE codex_interactions SET state='failed',response_cipher='',error=?,updated_at=?
                       WHERE owner_id=? AND id=?""",
                    ("interaction response could not be decrypted", now, owner_id, interaction_id),
                )
                raise ValueError("Codex interaction response could not be decrypted") from exc
            conn.execute(
                """
                UPDATE codex_interactions SET state='responded',response_cipher='',consumed_at=?,updated_at=?
                WHERE owner_id=? AND id=? AND state='answered'
                """,
                (now, now, owner_id, interaction_id),
            )
        return response if isinstance(response, dict) else {}

    def terminalize(self, *, owner_id: str, interaction_id: str, state: str, error: str = "") -> None:
        self.init()
        if state not in _TERMINAL:
            raise ValueError("invalid Codex interaction terminal state")
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                UPDATE codex_interactions SET state=?,response_cipher='',error=?,responded_at=CASE
                    WHEN responded_at='' THEN ? ELSE responded_at END,updated_at=?
                WHERE owner_id=? AND id=? AND state IN ('pending','answered')
                """,
                (state, error[:10000], now, now, owner_id, interaction_id),
            )

    def interrupt_host(self, *, owner_id: str, host_session_id: str, reason: str) -> int:
        self.init()
        now = _now()
        with self.db() as conn:
            cursor = conn.execute(
                """
                UPDATE codex_interactions SET state='interrupted',response_cipher='',error=?,updated_at=?
                WHERE owner_id=? AND host_session_id=? AND state IN ('pending','answered')
                """,
                (reason[:10000], now, owner_id, host_session_id),
            )
        return int(cursor.rowcount or 0)

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with self.db() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM codex_interactions WHERE owner_id=?", (owner_id,)).fetchone()[0])
            conn.execute("DELETE FROM codex_interactions WHERE owner_id=?", (owner_id,))
        return count


@lru_cache(maxsize=1)
def codex_interaction_store() -> CodexInteractionStore:
    return CodexInteractionStore()
