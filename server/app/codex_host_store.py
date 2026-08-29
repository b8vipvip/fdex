from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

_OWNER = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_CONTROL_ACTIONS = {"steer", "compact"}
_CONTROL_STATES = {"pending", "processing", "succeeded", "failed", "rejected"}
_THREAD_STATUSES = {"ready", "running", "compacting", "idle", "failed", "interrupted"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("invalid Codex owner scope")
    return clean


def _task(value: str) -> str:
    clean = (value or "").strip().lower()
    if not _TASK_ID.fullmatch(clean):
        raise ValueError("invalid Codex task id")
    return clean


def _id(value: str, label: str) -> str:
    clean = (value or "").strip()
    if not clean or len(clean) > 240 or any(ord(ch) < 32 for ch in clean):
        raise ValueError(f"invalid Codex {label}")
    return clean


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse_json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return fallback
    return parsed


class CodexHostStore:
    """Durable owner-scoped state for FDEX's official Codex app-server host.

    Agent tasks are durable already, but a Codex Thread can outlive one FDEX task and a
    continuation/fork can create additional turns after a worker restart.  This database is
    therefore deliberately separate from ``agent_tasks`` and models Codex protocol identity
    directly: Thread, FDEX task binding, Turn, and cross-worker control commands.
    """

    def __init__(self, path: Path | None = None) -> None:
        data = Path(__file__).resolve().parents[1] / "data"
        self.path = (path or data / "codex-host.db").resolve()
        self._initialized = False
        self._init_lock = threading.Lock()

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

    def init(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            with self.db() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS codex_threads (
                        thread_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        project_id INTEGER,
                        root_task_id TEXT NOT NULL,
                        parent_thread_id TEXT NOT NULL DEFAULT '',
                        forked_from_turn_id TEXT NOT NULL DEFAULT '',
                        current_turn_id TEXT NOT NULL DEFAULT '',
                        last_completed_turn_id TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'ready',
                        runtime_version TEXT NOT NULL DEFAULT '',
                        provider_id INTEGER,
                        provider_name TEXT NOT NULL DEFAULT '',
                        model TEXT NOT NULL DEFAULT '',
                        worktree TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_codex_threads_owner_updated
                        ON codex_threads(owner_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_codex_threads_owner_root_task
                        ON codex_threads(owner_id, root_task_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS codex_task_threads (
                        task_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        relation TEXT NOT NULL DEFAULT 'start',
                        source_task_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(thread_id) REFERENCES codex_threads(thread_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_codex_task_threads_owner_thread
                        ON codex_task_threads(owner_id, thread_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS codex_turns (
                        thread_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        kind TEXT NOT NULL DEFAULT 'turn',
                        status TEXT NOT NULL DEFAULT 'inProgress',
                        input_preview TEXT NOT NULL DEFAULT '',
                        client_user_message_id TEXT NOT NULL DEFAULT '',
                        error TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        started_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(thread_id, turn_id),
                        FOREIGN KEY(thread_id) REFERENCES codex_threads(thread_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_codex_turns_owner_task
                        ON codex_turns(owner_id, task_id, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_codex_turns_thread_started
                        ON codex_turns(thread_id, started_at DESC);

                    CREATE TABLE IF NOT EXISTS codex_controls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        state TEXT NOT NULL DEFAULT 'pending',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        claimed_at TEXT NOT NULL DEFAULT '',
                        completed_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(thread_id) REFERENCES codex_threads(thread_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_codex_controls_pending
                        ON codex_controls(thread_id, state, id);
                    CREATE INDEX IF NOT EXISTS idx_codex_controls_owner_task
                        ON codex_controls(owner_id, task_id, id DESC);
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
        for key in ("metadata_json", "payload_json", "result_json"):
            if key in data:
                data[key.removesuffix("_json")] = _parse_json(data.pop(key), {})
        return data

    def upsert_thread(
        self,
        *,
        owner_id: str,
        task_id: str,
        thread_id: str,
        project_id: int | None,
        status: str = "ready",
        runtime_version: str = "",
        provider_id: int | None = None,
        provider_name: str = "",
        model: str = "",
        worktree: str = "",
        parent_thread_id: str = "",
        forked_from_turn_id: str = "",
        root_task_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.init()
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        thread_id = _id(thread_id, "thread id")
        root_task_id = _task(root_task_id or task_id)
        status = status if status in _THREAD_STATUSES else "ready"
        now = _now()
        values = {
            "thread_id": thread_id,
            "owner_id": owner_id,
            "project_id": project_id,
            "root_task_id": root_task_id,
            "parent_thread_id": parent_thread_id[:240],
            "forked_from_turn_id": forked_from_turn_id[:240],
            "status": status,
            "runtime_version": runtime_version[:100],
            "provider_id": provider_id,
            "provider_name": provider_name[:240],
            "model": model[:240],
            "worktree": worktree[:2000],
            "metadata_json": _json(metadata or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM codex_threads WHERE thread_id=?", (thread_id,)).fetchone()
            if existing is not None and str(existing["owner_id"]) != owner_id:
                raise ValueError("Codex thread owner mismatch")
            conn.execute(
                """
                INSERT INTO codex_threads(
                    thread_id,owner_id,project_id,root_task_id,parent_thread_id,forked_from_turn_id,
                    status,runtime_version,provider_id,provider_name,model,worktree,metadata_json,
                    created_at,updated_at
                ) VALUES(
                    :thread_id,:owner_id,:project_id,:root_task_id,:parent_thread_id,:forked_from_turn_id,
                    :status,:runtime_version,:provider_id,:provider_name,:model,:worktree,:metadata_json,
                    :created_at,:updated_at
                )
                ON CONFLICT(thread_id) DO UPDATE SET
                    project_id=COALESCE(excluded.project_id,codex_threads.project_id),
                    parent_thread_id=CASE WHEN excluded.parent_thread_id<>'' THEN excluded.parent_thread_id ELSE codex_threads.parent_thread_id END,
                    forked_from_turn_id=CASE WHEN excluded.forked_from_turn_id<>'' THEN excluded.forked_from_turn_id ELSE codex_threads.forked_from_turn_id END,
                    status=excluded.status,
                    runtime_version=CASE WHEN excluded.runtime_version<>'' THEN excluded.runtime_version ELSE codex_threads.runtime_version END,
                    provider_id=COALESCE(excluded.provider_id,codex_threads.provider_id),
                    provider_name=CASE WHEN excluded.provider_name<>'' THEN excluded.provider_name ELSE codex_threads.provider_name END,
                    model=CASE WHEN excluded.model<>'' THEN excluded.model ELSE codex_threads.model END,
                    worktree=CASE WHEN excluded.worktree<>'' THEN excluded.worktree ELSE codex_threads.worktree END,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                values,
            )
        result = self.get_thread(owner_id, thread_id)
        assert result is not None
        return result

    def bind_task(
        self,
        *,
        owner_id: str,
        task_id: str,
        thread_id: str,
        relation: str,
        source_task_id: str = "",
    ) -> dict[str, Any]:
        self.init()
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        thread_id = _id(thread_id, "thread id")
        if source_task_id:
            source_task_id = _task(source_task_id)
        relation = (relation or "start").strip().lower()[:40]
        if relation not in {"start", "resume", "fork", "forked"}:
            raise ValueError("invalid Codex task/thread relation")
        thread = self.get_thread(owner_id, thread_id)
        if thread is None:
            raise KeyError("Codex thread not found")
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO codex_task_threads(task_id,owner_id,thread_id,relation,source_task_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    owner_id=excluded.owner_id,thread_id=excluded.thread_id,relation=excluded.relation,
                    source_task_id=excluded.source_task_id,updated_at=excluded.updated_at
                """,
                (task_id, owner_id, thread_id, relation, source_task_id, now, now),
            )
        result = self.task_binding(owner_id, task_id)
        assert result is not None
        return result

    def get_thread(self, owner_id: str, thread_id: str) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_threads WHERE owner_id=? AND thread_id=?",
                (_owner(owner_id), _id(thread_id, "thread id")),
            ).fetchone()
        return self._row(row)

    def task_binding(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_task_threads WHERE owner_id=? AND task_id=?",
                (_owner(owner_id), _task(task_id)),
            ).fetchone()
        return self._row(row)

    def task_state(self, owner_id: str, task_id: str, *, turn_limit: int = 30, control_limit: int = 30) -> dict[str, Any] | None:
        binding = self.task_binding(owner_id, task_id)
        if binding is None:
            return None
        thread = self.get_thread(owner_id, str(binding["thread_id"]))
        if thread is None:
            return None
        return {
            "binding": binding,
            "thread": thread,
            "turns": self.list_turns(owner_id, str(binding["thread_id"]), limit=turn_limit),
            "controls": self.list_controls(owner_id, task_id, limit=control_limit),
        }

    def update_thread_state(
        self,
        owner_id: str,
        thread_id: str,
        *,
        status: str | None = None,
        current_turn_id: str | None = None,
        last_completed_turn_id: str | None = None,
        worktree: str | None = None,
    ) -> None:
        self.init()
        owner_id = _owner(owner_id)
        thread_id = _id(thread_id, "thread id")
        assignments: list[str] = ["updated_at=?"]
        values: list[Any] = [_now()]
        if status is not None:
            if status not in _THREAD_STATUSES:
                raise ValueError("invalid Codex thread status")
            assignments.append("status=?")
            values.append(status)
        if current_turn_id is not None:
            assignments.append("current_turn_id=?")
            values.append(current_turn_id[:240])
        if last_completed_turn_id is not None:
            assignments.append("last_completed_turn_id=?")
            values.append(last_completed_turn_id[:240])
        if worktree is not None:
            assignments.append("worktree=?")
            values.append(worktree[:2000])
        values.extend([owner_id, thread_id])
        with self.db() as conn:
            cursor = conn.execute(
                f"UPDATE codex_threads SET {','.join(assignments)} WHERE owner_id=? AND thread_id=?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError("Codex thread not found")

    def record_turn_started(
        self,
        *,
        owner_id: str,
        task_id: str,
        thread_id: str,
        turn_id: str,
        kind: str = "turn",
        input_preview: str = "",
        client_user_message_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.init()
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        thread_id = _id(thread_id, "thread id")
        turn_id = _id(turn_id, "turn id")
        now = _now()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            thread = conn.execute(
                "SELECT owner_id FROM codex_threads WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if thread is None or str(thread["owner_id"]) != owner_id:
                raise KeyError("Codex thread not found")
            conn.execute(
                """
                INSERT INTO codex_turns(
                    thread_id,turn_id,owner_id,task_id,kind,status,input_preview,
                    client_user_message_id,metadata_json,started_at,updated_at
                ) VALUES(?,?,?,?,?,'inProgress',?,?,?,?,?)
                ON CONFLICT(thread_id,turn_id) DO UPDATE SET
                    task_id=excluded.task_id,kind=excluded.kind,status='inProgress',
                    input_preview=excluded.input_preview,
                    client_user_message_id=excluded.client_user_message_id,
                    metadata_json=excluded.metadata_json,updated_at=excluded.updated_at
                """,
                (
                    thread_id,
                    turn_id,
                    owner_id,
                    task_id,
                    kind[:40],
                    input_preview[:2000],
                    client_user_message_id[:240],
                    _json(metadata or {}),
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE codex_threads SET current_turn_id=?,status='running',worktree=COALESCE(NULLIF(worktree,''),worktree),updated_at=? WHERE thread_id=?",
                (turn_id, now, thread_id),
            )
        result = self.get_turn(owner_id, thread_id, turn_id)
        assert result is not None
        return result

    def record_turn_completed(
        self,
        *,
        owner_id: str,
        thread_id: str,
        turn_id: str,
        status: str,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.init()
        owner_id = _owner(owner_id)
        thread_id = _id(thread_id, "thread id")
        turn_id = _id(turn_id, "turn id")
        now = _now()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE codex_turns SET status=?,error=?,metadata_json=?,completed_at=?,updated_at=?
                WHERE owner_id=? AND thread_id=? AND turn_id=?
                """,
                (status[:50], error[:10000], _json(metadata or {}), now, now, owner_id, thread_id, turn_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Codex turn not found")
            thread_status = "idle" if status == "completed" else ("interrupted" if status == "interrupted" else "failed")
            conn.execute(
                """
                UPDATE codex_threads
                SET current_turn_id='',last_completed_turn_id=?,status=?,updated_at=?
                WHERE owner_id=? AND thread_id=?
                """,
                (turn_id, thread_status, now, owner_id, thread_id),
            )

    def get_turn(self, owner_id: str, thread_id: str, turn_id: str) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_turns WHERE owner_id=? AND thread_id=? AND turn_id=?",
                (_owner(owner_id), _id(thread_id, "thread id"), _id(turn_id, "turn id")),
            ).fetchone()
        return self._row(row)

    def list_turns(self, owner_id: str, thread_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        self.init()
        limit = max(1, min(int(limit), 200))
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codex_turns WHERE owner_id=? AND thread_id=?
                ORDER BY started_at DESC,turn_id DESC LIMIT ?
                """,
                (_owner(owner_id), _id(thread_id, "thread id"), limit),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def enqueue_control(
        self,
        *,
        owner_id: str,
        task_id: str,
        thread_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.init()
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        thread_id = _id(thread_id, "thread id")
        action = (action or "").strip().lower()
        if action not in _CONTROL_ACTIONS:
            raise ValueError("unsupported Codex control action")
        thread = self.get_thread(owner_id, thread_id)
        if thread is None:
            raise KeyError("Codex thread not found")
        now = _now()
        with self.db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO codex_controls(
                    owner_id,task_id,thread_id,action,payload_json,state,created_at,updated_at
                ) VALUES(?,?,?,?,?,'pending',?,?)
                """,
                (owner_id, task_id, thread_id, action, _json(payload or {}), now, now),
            )
            control_id = int(cursor.lastrowid)
        result = self.get_control(owner_id, control_id)
        assert result is not None
        return result

    def claim_controls(
        self,
        *,
        owner_id: str,
        thread_id: str,
        actions: Iterable[str],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        self.init()
        owner_id = _owner(owner_id)
        thread_id = _id(thread_id, "thread id")
        selected = sorted({str(item).strip().lower() for item in actions if str(item).strip().lower() in _CONTROL_ACTIONS})
        if not selected:
            return []
        limit = max(1, min(int(limit), 50))
        placeholders = ",".join("?" for _ in selected)
        now = _now()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""
                SELECT * FROM codex_controls
                WHERE owner_id=? AND thread_id=? AND state='pending' AND action IN ({placeholders})
                ORDER BY id LIMIT ?
                """,
                [owner_id, thread_id, *selected, limit],
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                id_marks = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE codex_controls SET state='processing',claimed_at=?,updated_at=? WHERE id IN ({id_marks}) AND state='pending'",
                    [now, now, *ids],
                )
                rows = conn.execute(
                    f"SELECT * FROM codex_controls WHERE id IN ({id_marks}) ORDER BY id", ids
                ).fetchall()
        return [self._row(row) or {} for row in rows]

    def finish_control(
        self,
        *,
        owner_id: str,
        control_id: int,
        state: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        self.init()
        owner_id = _owner(owner_id)
        state = (state or "").strip().lower()
        if state not in _CONTROL_STATES - {"pending", "processing"}:
            raise ValueError("invalid Codex control terminal state")
        now = _now()
        with self.db() as conn:
            cursor = conn.execute(
                """
                UPDATE codex_controls
                SET state=?,result_json=?,error=?,completed_at=?,updated_at=?
                WHERE id=? AND owner_id=?
                """,
                (state, _json(result or {}), error[:10000], now, now, int(control_id), owner_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Codex control not found")

    def get_control(self, owner_id: str, control_id: int) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_controls WHERE owner_id=? AND id=?",
                (_owner(owner_id), int(control_id)),
            ).fetchone()
        return self._row(row)

    def list_controls(self, owner_id: str, task_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        self.init()
        limit = max(1, min(int(limit), 200))
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_controls WHERE owner_id=? AND task_id=? ORDER BY id DESC LIMIT ?",
                (_owner(owner_id), _task(task_id), limit),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def active_count(self, owner_id: str) -> int:
        self.init()
        owner_id = _owner(owner_id)
        with self.db() as conn:
            thread_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM codex_threads WHERE owner_id=? AND status IN ('running','compacting')",
                    (owner_id,),
                ).fetchone()[0]
            )
            control_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM codex_controls WHERE owner_id=? AND state IN ('pending','processing')",
                    (owner_id,),
                ).fetchone()[0]
            )
        return thread_count + control_count

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        owner_id = _owner(owner_id)
        if self.active_count(owner_id):
            raise ValueError("Codex Host still has active operations for this account")
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            counts = {
                "threads": int(conn.execute("SELECT COUNT(*) FROM codex_threads WHERE owner_id=?", (owner_id,)).fetchone()[0]),
                "task_bindings": int(conn.execute("SELECT COUNT(*) FROM codex_task_threads WHERE owner_id=?", (owner_id,)).fetchone()[0]),
                "turns": int(conn.execute("SELECT COUNT(*) FROM codex_turns WHERE owner_id=?", (owner_id,)).fetchone()[0]),
                "controls": int(conn.execute("SELECT COUNT(*) FROM codex_controls WHERE owner_id=?", (owner_id,)).fetchone()[0]),
            }
            # Thread cascades erase turns/bindings/controls; explicit owner predicates protect
            # against a future schema change that relaxes those foreign keys.
            conn.execute("DELETE FROM codex_controls WHERE owner_id=?", (owner_id,))
            conn.execute("DELETE FROM codex_turns WHERE owner_id=?", (owner_id,))
            conn.execute("DELETE FROM codex_task_threads WHERE owner_id=?", (owner_id,))
            conn.execute("DELETE FROM codex_threads WHERE owner_id=?", (owner_id,))
        return counts


@lru_cache(maxsize=1)
def codex_host_store() -> CodexHostStore:
    return CodexHostStore()
