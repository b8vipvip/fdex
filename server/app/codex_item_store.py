from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Iterator

from app.codex_host_store import codex_host_store

_MAX_EVENT_BYTES = 1024 * 1024
_MAX_ITEM_BYTES = 2 * 1024 * 1024
_MAX_METHOD = 240
_MAX_PROTOCOL_ID = 240


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _text(value: Any, limit: int = _MAX_PROTOCOL_ID) -> str:
    clean = str(value or "").strip()
    if any(ord(ch) < 32 for ch in clean):
        return ""
    return clean[:limit]


def _json_bounded(value: Any, limit: int) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    encoded = raw.encode("utf-8")
    if len(encoded) <= limit:
        return raw
    # Never write malformed/truncated JSON. Keep an explicit bounded envelope so the UI can
    # explain that the Host retained only a preview instead of silently pretending the item was
    # complete. This protects SQLite from unbounded command output while preserving protocol
    # metadata and the original byte size.
    preview_limit = min(200_000, max(4_096, limit // 4))
    envelope = {
        "fdex_truncated": True,
        "original_bytes": len(encoded),
        "preview": raw[:preview_limit],
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return fallback


class CodexItemStore:
    """Schema-light durable notification bus for official Codex ThreadItem traffic.

    The raw app-server method+params stream is the compatibility boundary. FDEX derives a
    queryable current-item projection for the UI, but keeps the original bounded event so new
    official Item variants remain visible before FDEX learns a dedicated renderer.
    """

    def __init__(self) -> None:
        self.path = codex_host_store().path
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
            codex_host_store().init()
            with self.db() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS codex_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL DEFAULT '',
                        turn_id TEXT NOT NULL DEFAULT '',
                        item_id TEXT NOT NULL DEFAULT '',
                        method TEXT NOT NULL,
                        params_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_codex_events_owner_task_seq
                        ON codex_events(owner_id, task_id, seq);
                    CREATE INDEX IF NOT EXISTS idx_codex_events_owner_thread_seq
                        ON codex_events(owner_id, thread_id, seq);

                    CREATE TABLE IF NOT EXISTS codex_items (
                        owner_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        item_type TEXT NOT NULL DEFAULT 'unknown',
                        status TEXT NOT NULL DEFAULT 'inProgress',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        started_at_ms INTEGER,
                        completed_at_ms INTEGER,
                        first_event_seq INTEGER NOT NULL,
                        last_event_seq INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(owner_id, thread_id, turn_id, item_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_codex_items_owner_task_updated
                        ON codex_items(owner_id, task_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_codex_items_owner_thread_turn
                        ON codex_items(owner_id, thread_id, turn_id, first_event_seq);
                    """
                )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._initialized = True

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["params"] = _parse(data.pop("params_json"), {})
        return data

    @staticmethod
    def _item_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = _parse(data.pop("payload_json"), {})
        return data

    def record_notification(
        self,
        *,
        owner_id: str,
        task_id: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.init()
        method = _text(method, _MAX_METHOD) or "unknown"
        normalized = params if isinstance(params, dict) else {}
        thread_id = _text(normalized.get("threadId"))
        turn_id = _text(normalized.get("turnId"))
        item = normalized.get("item")
        item_dict = item if isinstance(item, dict) else {}
        item_id = _text(normalized.get("itemId") or item_dict.get("id"))
        now = _now()
        event_json = _json_bounded(normalized, _MAX_EVENT_BYTES)

        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO codex_events(
                    owner_id,task_id,thread_id,turn_id,item_id,method,params_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (owner_id, task_id, thread_id, turn_id, item_id, method, event_json, now),
            )
            seq = int(cursor.lastrowid)

            if method in {"item/started", "item/completed"} and item_id and thread_id and turn_id:
                item_type = _text(item_dict.get("type"), 120) or "unknown"
                default_status = "inProgress" if method == "item/started" else "completed"
                status = _text(item_dict.get("status"), 120) or default_status
                payload_json = _json_bounded(item_dict, _MAX_ITEM_BYTES)
                started_at_ms = normalized.get("startedAtMs") if method == "item/started" else None
                completed_at_ms = normalized.get("completedAtMs") if method == "item/completed" else None
                conn.execute(
                    """
                    INSERT INTO codex_items(
                        owner_id,task_id,thread_id,turn_id,item_id,item_type,status,payload_json,
                        started_at_ms,completed_at_ms,first_event_seq,last_event_seq,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(owner_id,thread_id,turn_id,item_id) DO UPDATE SET
                        task_id=excluded.task_id,
                        item_type=CASE WHEN excluded.item_type<>'unknown' THEN excluded.item_type ELSE codex_items.item_type END,
                        status=excluded.status,
                        payload_json=excluded.payload_json,
                        started_at_ms=COALESCE(codex_items.started_at_ms,excluded.started_at_ms),
                        completed_at_ms=COALESCE(excluded.completed_at_ms,codex_items.completed_at_ms),
                        last_event_seq=excluded.last_event_seq,
                        updated_at=excluded.updated_at
                    """,
                    (
                        owner_id,
                        task_id,
                        thread_id,
                        turn_id,
                        item_id,
                        item_type,
                        status,
                        payload_json,
                        int(started_at_ms) if isinstance(started_at_ms, int) else None,
                        int(completed_at_ms) if isinstance(completed_at_ms, int) else None,
                        seq,
                        seq,
                        now,
                        now,
                    ),
                )

            if method == "turn/completed" and thread_id:
                turn = normalized.get("turn")
                turn_dict = turn if isinstance(turn, dict) else {}
                completed_turn_id = _text(turn_dict.get("id") or turn_id)
                turn_status = _text(turn_dict.get("status"), 120) or "completed"
                if completed_turn_id and turn_status != "inProgress":
                    # The official stream can lose item/completed during interruption. Preserve
                    # that distinction instead of falsely marking the Item completed.
                    conn.execute(
                        """
                        UPDATE codex_items
                        SET status='orphaned',last_event_seq=?,updated_at=?
                        WHERE owner_id=? AND thread_id=? AND turn_id=? AND status='inProgress'
                        """,
                        (seq, now, owner_id, thread_id, completed_turn_id),
                    )

            row = conn.execute("SELECT * FROM codex_events WHERE seq=?", (seq,)).fetchone()
        assert row is not None
        return self._event_row(row)

    def list_events(
        self,
        owner_id: str,
        task_id: str,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self.init()
        after_seq = max(0, int(after_seq))
        limit = max(1, min(int(limit), 500))
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codex_events
                WHERE owner_id=? AND task_id=? AND seq>?
                ORDER BY seq ASC LIMIT ?
                """,
                (owner_id, task_id, after_seq, limit),
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def list_items(self, owner_id: str, task_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        self.init()
        limit = max(1, min(int(limit), 500))
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codex_items WHERE owner_id=? AND task_id=?
                ORDER BY first_event_seq ASC LIMIT ?
                """,
                (owner_id, task_id, limit),
            ).fetchall()
        return [self._item_row(row) for row in rows]

    def latest_seq(self, owner_id: str, task_id: str) -> int:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM codex_events WHERE owner_id=? AND task_id=?",
                (owner_id, task_id),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            events = int(conn.execute("SELECT COUNT(*) FROM codex_events WHERE owner_id=?", (owner_id,)).fetchone()[0])
            items = int(conn.execute("SELECT COUNT(*) FROM codex_items WHERE owner_id=?", (owner_id,)).fetchone()[0])
            conn.execute("DELETE FROM codex_events WHERE owner_id=?", (owner_id,))
            conn.execute("DELETE FROM codex_items WHERE owner_id=?", (owner_id,))
        return {"events": events, "items": items}


@lru_cache(maxsize=1)
def codex_item_store() -> CodexItemStore:
    return CodexItemStore()
