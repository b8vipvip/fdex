from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import SERVER_DIR

DB_PATH = SERVER_DIR / "data" / "request-records.sqlite3"
_RETENTION_DAYS = 30
_MAX_RECORDS = 20000
_SECRET_KEYS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)\s*[:=]\s*)[^\s,;]+"
    ),
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _redact_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "").replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text[:limit]


def _clean_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:80]:
            key = _redact_text(raw_key, 100)
            if any(secret in key.casefold() for secret in _SECRET_KEYS):
                result[key] = "[REDACTED]"
            else:
                result[key] = _clean_payload(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_clean_payload(item, depth=depth + 1) for item in list(value)[:80]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class RequestRecordStore:
    """Durable, bounded server request diagnostics grouped by FDEX Request ID.

    The existing request trace emits compact structured events. This store persists those events so
    an administrator can inspect a request summary and export the complete event chain later. It is
    deliberately metadata-only: request/response bodies are not captured here, and obvious secrets
    are redacted again before data is written to disk.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._init_lock = threading.Lock()
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path, timeout=10) as conn:
                conn.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS request_records (
                        request_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        ended_at TEXT NOT NULL DEFAULT '',
                        method TEXT NOT NULL DEFAULT '',
                        path TEXT NOT NULL DEFAULT '',
                        status_code INTEGER,
                        elapsed_ms INTEGER,
                        client TEXT NOT NULL DEFAULT '',
                        mode TEXT NOT NULL DEFAULT '',
                        content_type TEXT NOT NULL DEFAULT '',
                        content_length TEXT NOT NULL DEFAULT '',
                        event_count INTEGER NOT NULL DEFAULT 0,
                        last_component TEXT NOT NULL DEFAULT '',
                        last_event TEXT NOT NULL DEFAULT '',
                        error_type TEXT NOT NULL DEFAULT '',
                        error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS request_record_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        level TEXT NOT NULL,
                        component TEXT NOT NULL,
                        event TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_request_records_started
                        ON request_records(started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_request_records_status
                        ON request_records(status_code, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_request_record_events_request
                        ON request_record_events(request_id, id ASC);
                    """
                )
            self._initialized = True

    def db(self) -> sqlite3.Connection:
        self.init()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def record_event(self, payload: dict[str, Any], *, level: str = "info") -> None:
        cleaned = _clean_payload(payload)
        if not isinstance(cleaned, dict):
            return
        request_id = _redact_text(cleaned.get("request_id"), 80)
        if not request_id:
            return
        event = _redact_text(cleaned.get("event"), 160) or "event"
        component = _redact_text(cleaned.get("component"), 120) or "server"
        safe_level = _redact_text(level, 20).lower() or "info"
        occurred_at = _now()
        payload_json = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))

        with self.db() as conn:
            if event == "http_request_begin":
                # A caller-supplied Request ID can theoretically be reused. Treat a new begin event
                # as a fresh request so an old event chain never contaminates a later export.
                conn.execute("DELETE FROM request_record_events WHERE request_id=?", (request_id,))
                conn.execute(
                    """INSERT INTO request_records(
                           request_id,started_at,ended_at,method,path,status_code,elapsed_ms,
                           client,mode,content_type,content_length,event_count,last_component,
                           last_event,error_type,error
                       ) VALUES(?,?,?,?,?,NULL,NULL,?,?,?,?,0,'','','','')
                       ON CONFLICT(request_id) DO UPDATE SET
                           started_at=excluded.started_at,
                           ended_at='',
                           method=excluded.method,
                           path=excluded.path,
                           status_code=NULL,
                           elapsed_ms=NULL,
                           client=excluded.client,
                           mode=excluded.mode,
                           content_type=excluded.content_type,
                           content_length=excluded.content_length,
                           event_count=0,
                           last_component='',
                           last_event='',
                           error_type='',
                           error=''""",
                    (
                        request_id,
                        occurred_at,
                        "",
                        _redact_text(cleaned.get("method"), 16),
                        _redact_text(cleaned.get("path"), 500),
                        _redact_text(cleaned.get("client"), 160),
                        _redact_text(cleaned.get("mode"), 80),
                        _redact_text(cleaned.get("content_type"), 160),
                        _redact_text(cleaned.get("content_length"), 40),
                    ),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO request_records(request_id,started_at) VALUES(?,?)",
                    (request_id, occurred_at),
                )

            conn.execute(
                """INSERT INTO request_record_events(
                       request_id,occurred_at,level,component,event,payload_json
                   ) VALUES(?,?,?,?,?,?)""",
                (request_id, occurred_at, safe_level, component, event, payload_json),
            )
            conn.execute(
                """UPDATE request_records
                   SET event_count=event_count+1,last_component=?,last_event=?
                   WHERE request_id=?""",
                (component, event, request_id),
            )

            if event == "http_request_end":
                conn.execute(
                    """UPDATE request_records
                       SET ended_at=?,status_code=?,elapsed_ms=?
                       WHERE request_id=?""",
                    (
                        occurred_at,
                        _optional_int(cleaned.get("status_code")),
                        _optional_int(cleaned.get("elapsed_ms")),
                        request_id,
                    ),
                )
            elif event == "http_request_exception":
                conn.execute(
                    """UPDATE request_records
                       SET ended_at=?,elapsed_ms=?,error_type=?,error=?
                       WHERE request_id=?""",
                    (
                        occurred_at,
                        _optional_int(cleaned.get("elapsed_ms")),
                        _redact_text(cleaned.get("error_type"), 160),
                        _redact_text(cleaned.get("error"), 2000),
                        request_id,
                    ),
                )

            if event == "http_request_begin":
                self._prune(conn)

    def _prune(self, conn: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)).isoformat(timespec="milliseconds")
        stale_ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT request_id FROM request_records WHERE started_at < ?",
                (cutoff,),
            ).fetchall()
        ]
        overflow_ids = [
            str(row[0])
            for row in conn.execute(
                """SELECT request_id FROM request_records
                   ORDER BY started_at DESC
                   LIMIT -1 OFFSET ?""",
                (_MAX_RECORDS,),
            ).fetchall()
        ]
        remove_ids = list(dict.fromkeys(stale_ids + overflow_ids))
        if not remove_ids:
            return
        conn.executemany("DELETE FROM request_record_events WHERE request_id=?", [(item,) for item in remove_ids])
        conn.executemany("DELETE FROM request_records WHERE request_id=?", [(item,) for item in remove_ids])

    def list(
        self,
        *,
        method: str = "",
        status: str = "",
        query: str = "",
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        requested_method = _redact_text(method, 16).upper()
        if requested_method:
            clauses.append("method=?")
            params.append(requested_method)

        status_filter = _redact_text(status, 20).lower()
        if status_filter == "success":
            clauses.append("ended_at<>'' AND status_code BETWEEN 200 AND 399 AND error_type='' ")
        elif status_filter == "error":
            clauses.append("(error_type<>'' OR status_code>=400)")
        elif status_filter == "running":
            clauses.append("ended_at='' ")

        needle_text = _redact_text(query, 120)
        if needle_text:
            clauses.append(
                "(request_id LIKE ? OR path LIKE ? OR client LIKE ? OR mode LIKE ? OR "
                "last_event LIKE ? OR error_type LIKE ? OR error LIKE ?)"
            )
            needle = f"%{needle_text}%"
            params.extend([needle] * 7)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 2000)))
        with self.db() as conn:
            rows = conn.execute(
                f"SELECT * FROM request_records{where} ORDER BY started_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def methods(self) -> list[str]:
        with self.db() as conn:
            rows = conn.execute(
                """SELECT method,MAX(started_at) latest
                   FROM request_records WHERE method<>''
                   GROUP BY method ORDER BY latest DESC"""
            ).fetchall()
        return [str(row["method"]) for row in rows]

    def get(self, request_id: str) -> dict[str, Any] | None:
        normalized = _redact_text(request_id, 80)
        if not normalized:
            return None
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM request_records WHERE request_id=?",
                (normalized,),
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                """SELECT occurred_at,level,component,event,payload_json
                   FROM request_record_events WHERE request_id=? ORDER BY id ASC""",
                (normalized,),
            ).fetchall()

        result = dict(row)
        events: list[dict[str, Any]] = []
        for event_row in event_rows:
            item = dict(event_row)
            try:
                item["payload"] = json.loads(str(item.pop("payload_json") or "{}"))
            except json.JSONDecodeError:
                item["payload"] = {}
            events.append(item)
        result["events"] = events
        return result


_store: RequestRecordStore | None = None


def request_record_store() -> RequestRecordStore:
    global _store
    if _store is None:
        _store = RequestRecordStore()
        _store.init()
    return _store
