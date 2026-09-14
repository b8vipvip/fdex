from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import SERVER_DIR

DB_PATH = SERVER_DIR / "data" / "provider-request-records.sqlite3"
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
    """Durable outbound AI Provider request diagnostics.

    One row represents one real HTTP request from FDEX to a configured AI Provider. The store keeps
    only routing/transport metadata needed for troubleshooting: Provider, model, protocol, target,
    HTTP status, latency, correlation Request ID and compact diagnostic events. Prompt/response bodies
    are deliberately never persisted and obvious secrets are redacted before disk writes.
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
                    CREATE TABLE IF NOT EXISTS provider_request_records (
                        record_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL,
                        ended_at TEXT NOT NULL DEFAULT '',
                        provider_id INTEGER,
                        provider TEXT NOT NULL DEFAULT '',
                        model TEXT NOT NULL DEFAULT '',
                        protocol TEXT NOT NULL DEFAULT '',
                        target TEXT NOT NULL DEFAULT '',
                        method TEXT NOT NULL DEFAULT 'POST',
                        mode TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        status_code INTEGER,
                        elapsed_ms INTEGER,
                        outcome TEXT NOT NULL DEFAULT 'running',
                        event_count INTEGER NOT NULL DEFAULT 0,
                        last_event TEXT NOT NULL DEFAULT '',
                        error_type TEXT NOT NULL DEFAULT '',
                        error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS provider_request_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        record_id TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        level TEXT NOT NULL,
                        event TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_provider_request_started
                        ON provider_request_records(started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_provider_request_provider
                        ON provider_request_records(provider, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_provider_request_status
                        ON provider_request_records(outcome, status_code, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_provider_request_events_record
                        ON provider_request_events(record_id, id ASC);
                    """
                )
            self._initialized = True

    def db(self) -> sqlite3.Connection:
        self.init()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def begin(
        self,
        *,
        request_id: str = "",
        provider_id: int | None = None,
        provider: str,
        model: str = "",
        protocol: str = "",
        target: str = "",
        method: str = "POST",
        mode: str = "",
        source: str = "",
    ) -> str:
        record_id = uuid.uuid4().hex
        occurred_at = _now()
        safe = {
            "record_id": record_id,
            "request_id": _redact_text(request_id, 80),
            "provider_id": _optional_int(provider_id),
            "provider": _redact_text(provider, 180),
            "model": _redact_text(model, 180),
            "protocol": _redact_text(protocol, 80),
            "target": _redact_text(target, 500),
            "method": _redact_text(method, 16).upper() or "POST",
            "mode": _redact_text(mode, 40),
            "source": _redact_text(source, 80),
        }
        with self.db() as conn:
            conn.execute(
                """INSERT INTO provider_request_records(
                       record_id,request_id,started_at,ended_at,provider_id,provider,model,protocol,
                       target,method,mode,source,status_code,elapsed_ms,outcome,event_count,last_event,
                       error_type,error
                   ) VALUES(?,?,?,'',?,?,?,?,?,?,?,?,NULL,NULL,'running',1,'provider_request_begin','','')""",
                (
                    record_id,
                    safe["request_id"],
                    occurred_at,
                    safe["provider_id"],
                    safe["provider"],
                    safe["model"],
                    safe["protocol"],
                    safe["target"],
                    safe["method"],
                    safe["mode"],
                    safe["source"],
                ),
            )
            conn.execute(
                """INSERT INTO provider_request_events(record_id,occurred_at,level,event,payload_json)
                   VALUES(?,?,?,?,?)""",
                (
                    record_id,
                    occurred_at,
                    "info",
                    "provider_request_begin",
                    json.dumps(_clean_payload(safe), ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self._prune(conn)
        return record_id

    def add_event(self, record_id: str, event: str, *, level: str = "info", **fields: Any) -> None:
        normalized = _redact_text(record_id, 80)
        if not normalized:
            return
        occurred_at = _now()
        safe_event = _redact_text(event, 160) or "event"
        safe_level = _redact_text(level, 20).lower() or "info"
        payload = _clean_payload(fields)
        if not isinstance(payload, dict):
            payload = {}
        with self.db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM provider_request_records WHERE record_id=?",
                (normalized,),
            ).fetchone()
            if exists is None:
                return
            conn.execute(
                """INSERT INTO provider_request_events(record_id,occurred_at,level,event,payload_json)
                   VALUES(?,?,?,?,?)""",
                (
                    normalized,
                    occurred_at,
                    safe_level,
                    safe_event,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.execute(
                """UPDATE provider_request_records
                   SET event_count=event_count+1,last_event=? WHERE record_id=?""",
                (safe_event, normalized),
            )

    def finish(
        self,
        record_id: str,
        *,
        status_code: int | None = None,
        elapsed_ms: int | None = None,
        outcome: str,
        error_type: str = "",
        error: str = "",
        content_type: str = "",
    ) -> None:
        normalized = _redact_text(record_id, 80)
        if not normalized:
            return
        safe_outcome = _redact_text(outcome, 20).lower()
        if safe_outcome not in {"success", "error"}:
            safe_outcome = "error"
        occurred_at = _now()
        payload = {
            "status_code": _optional_int(status_code),
            "elapsed_ms": _optional_int(elapsed_ms),
            "outcome": safe_outcome,
            "error_type": _redact_text(error_type, 160),
            "error": _redact_text(error, 2000),
            "content_type": _redact_text(content_type, 180),
        }
        event = "provider_request_success" if safe_outcome == "success" else "provider_request_error"
        level = "info" if safe_outcome == "success" else "warning"
        with self.db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM provider_request_records WHERE record_id=?",
                (normalized,),
            ).fetchone()
            if exists is None:
                return
            conn.execute(
                """INSERT INTO provider_request_events(record_id,occurred_at,level,event,payload_json)
                   VALUES(?,?,?,?,?)""",
                (
                    normalized,
                    occurred_at,
                    level,
                    event,
                    json.dumps(_clean_payload(payload), ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.execute(
                """UPDATE provider_request_records
                   SET ended_at=?,status_code=?,elapsed_ms=?,outcome=?,event_count=event_count+1,
                       last_event=?,error_type=?,error=?
                   WHERE record_id=?""",
                (
                    occurred_at,
                    payload["status_code"],
                    payload["elapsed_ms"],
                    safe_outcome,
                    event,
                    payload["error_type"],
                    payload["error"],
                    normalized,
                ),
            )

    def _prune(self, conn: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)).isoformat(timespec="milliseconds")
        stale_ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT record_id FROM provider_request_records WHERE started_at < ?",
                (cutoff,),
            ).fetchall()
        ]
        overflow_ids = [
            str(row[0])
            for row in conn.execute(
                """SELECT record_id FROM provider_request_records
                   ORDER BY started_at DESC LIMIT -1 OFFSET ?""",
                (_MAX_RECORDS,),
            ).fetchall()
        ]
        remove_ids = list(dict.fromkeys(stale_ids + overflow_ids))
        if not remove_ids:
            return
        conn.executemany("DELETE FROM provider_request_events WHERE record_id=?", [(item,) for item in remove_ids])
        conn.executemany("DELETE FROM provider_request_records WHERE record_id=?", [(item,) for item in remove_ids])

    def list(
        self,
        *,
        provider: str = "",
        status: str = "",
        query: str = "",
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        requested_provider = _redact_text(provider, 180)
        if requested_provider:
            clauses.append("provider=?")
            params.append(requested_provider)

        status_filter = _redact_text(status, 20).lower()
        if status_filter == "success":
            clauses.append("outcome='success'")
        elif status_filter == "error":
            clauses.append("outcome='error'")
        elif status_filter == "running":
            clauses.append("outcome='running'")

        needle_text = _redact_text(query, 120)
        if needle_text:
            clauses.append(
                "(record_id LIKE ? OR request_id LIKE ? OR provider LIKE ? OR model LIKE ? OR "
                "protocol LIKE ? OR target LIKE ? OR source LIKE ? OR error_type LIKE ? OR error LIKE ?)"
            )
            needle = f"%{needle_text}%"
            params.extend([needle] * 9)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 2000)))
        with self.db() as conn:
            rows = conn.execute(
                f"SELECT * FROM provider_request_records{where} ORDER BY started_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def providers(self) -> list[str]:
        with self.db() as conn:
            rows = conn.execute(
                """SELECT provider,MAX(started_at) latest
                   FROM provider_request_records WHERE provider<>''
                   GROUP BY provider ORDER BY latest DESC"""
            ).fetchall()
        return [str(row["provider"]) for row in rows]

    def get(self, record_id: str) -> dict[str, Any] | None:
        normalized = _redact_text(record_id, 80)
        if not normalized:
            return None
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM provider_request_records WHERE record_id=?",
                (normalized,),
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                """SELECT occurred_at,level,event,payload_json
                   FROM provider_request_events WHERE record_id=? ORDER BY id ASC""",
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
