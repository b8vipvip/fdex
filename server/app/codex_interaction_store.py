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


def _bounded_json(value: Any, label: str) -> str:
    """Serialize protocol data without silently changing its shape.

    Phase 7.22 raw notification history may be truncated because it is an observational stream.
    Interactive requests/responses are different: truncating them can turn a valid Codex response
    into a different JSON object. They therefore fail closed when the bounded bridge limit is hit.
    """
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(raw.encode("utf-8")) > _MAX_JSON:
        raise ValueError(f"{label} exceeds the 1 MiB FDEX interaction limit")
    return raw


def _parse(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(raw or ""))
    except json.JSONDecodeError:
        return fallback


def _rpc_key(value: int | str) -> str:
    # JSON-RPC allows both numeric and string request ids. Preserve the type so id=1 and id="1"
    # cannot collide inside one app-server Host session.
    if isinstance(value, bool):
        raise ValueError("Codex JSON-RPC request id cannot be boolean")
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, str):
        if not value or len(value) > 480:
            raise ValueError("Codex JSON-RPC string request id is invalid")
        return f"s:{value}"
    raise ValueError("Codex JSON-RPC request id must be an integer or string")


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

        if not self.key_path.exists():
            # Publish a fully-written temporary file with an atomic hard-link. Unlike O_EXCL on
            # the final path, another worker can never observe a zero-byte key between create()
            # and write(). The winner's inode becomes canonical; every loser discards its temp.
            generated = Fernet.generate_key()
            temp_path = self.key_path.with_name(
                f".{self.key_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, generated + b"\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                try:
                    os.link(temp_path, self.key_path)
                except FileExistsError:
                    pass
            finally:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

        key = self.key_path.read_bytes().strip()
        try:
            cipher = Fernet(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("Codex interaction encryption key is invalid") from exc
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = cipher
        return cipher

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
        request_json = _bounded_json(params, "Codex interaction request")
        interaction_id = uuid.uuid4().hex
        rpc_text = _rpc_key(rpc_id)
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
                    request_json,
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
        response_json = _bounded_json(response, "Codex interaction response")
        summary_json = _bounded_json(summary or {}, "Codex interaction response summary")
        cipher = self._cipher().encrypt(response_json.encode("utf-8")).decode("ascii")
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
                (cipher, summary_json, now, now, owner_id, interaction_id),
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

    def interrupt_orphans(self, owner_id: str) -> int:
        """Terminalize interactions whose owning Codex Thread is no longer running.

        A hard-killed Uvicorn worker cannot execute the scope-finally cleanup. Phase 7.21 repairs
        stale Thread state when another worker next acquires its kernel lease. Once that happens,
        this database-only reconciliation makes the old browser interaction terminal as well.
        """
        self.init()
        now = _now()
        reason = "Codex Host is no longer active for this interaction"
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE codex_interactions
                SET state='interrupted',response_cipher='',error=CASE WHEN error='' THEN ? ELSE error END,
                    updated_at=?
                WHERE owner_id=? AND state IN ('pending','answered') AND (
                    thread_id='' OR NOT EXISTS (
                        SELECT 1 FROM codex_threads t
                        WHERE t.owner_id=codex_interactions.owner_id
                          AND t.thread_id=codex_interactions.thread_id
                          AND t.status IN ('running','compacting')
                    )
                )
                """,
                (reason, now, owner_id),
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
