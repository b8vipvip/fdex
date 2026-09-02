from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app.config import SERVER_DIR

DB_PATH = SERVER_DIR / "data" / "client-runtime-logs.sqlite3"
_ALLOWED_LEVELS = {"debug", "info", "warn", "error"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)\s*[:=]\s*)[^\s,;]+"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def redact_text(value: Any, limit: int = 4000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text[:limit]


def _clean_details(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:50]:
            key = redact_text(raw_key, 100)
            if any(secret in key.casefold() for secret in ("password", "passwd", "token", "secret", "authorization", "cookie", "api_key", "apikey")):
                result[key] = "[REDACTED]"
            else:
                result[key] = _clean_details(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_clean_details(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact_text(value, 1200)


def _serialize_details(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= 8000:
        return serialized
    return json.dumps(
        {"_truncated": True, "preview": redact_text(serialized, 7400)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class ClientRuntimeLogStore:
    """Small durable diagnostics store for authenticated FDEX clients.

    The server owns account/session attribution; the client can only submit diagnostic fields.
    Retention is intentionally bounded so diagnostics cannot become an unbounded telemetry sink.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._init_lock = threading.Lock()
        self._initialized = False

    def db(self) -> sqlite3.Connection:
        self.init()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

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
                    CREATE TABLE IF NOT EXISTS client_runtime_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id TEXT NOT NULL,
                        session_id TEXT NOT NULL DEFAULT '',
                        device_name TEXT NOT NULL DEFAULT '',
                        platform TEXT NOT NULL DEFAULT 'android',
                        app_version TEXT NOT NULL DEFAULT '',
                        git_sha TEXT NOT NULL DEFAULT '',
                        os_version TEXT NOT NULL DEFAULT '',
                        level TEXT NOT NULL,
                        component TEXT NOT NULL,
                        event TEXT NOT NULL,
                        message TEXT NOT NULL DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        client_time TEXT NOT NULL DEFAULT '',
                        received_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_client_runtime_logs_received
                        ON client_runtime_logs(received_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_client_runtime_logs_owner
                        ON client_runtime_logs(owner_id, received_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_client_runtime_logs_level
                        ON client_runtime_logs(level, received_at DESC, id DESC);
                    """
                )
            self._initialized = True

    def append_batch(
        self,
        *,
        owner_id: str,
        session_id: str,
        device_name: str,
        platform: str,
        app_version: str,
        git_sha: str,
        os_version: str,
        entries: Iterable[dict[str, Any]],
    ) -> int:
        rows: list[tuple[str, ...]] = []
        received_at = _now()
        for item in list(entries)[:100]:
            if not isinstance(item, dict):
                continue
            level = str(item.get("level") or "info").strip().lower()
            if level not in _ALLOWED_LEVELS:
                level = "info"
            component = redact_text(item.get("component"), 120) or "client"
            event = redact_text(item.get("event"), 160) or "event"
            message = redact_text(item.get("message"), 2000)
            details = _clean_details(item.get("details") if isinstance(item.get("details"), (dict, list)) else {})
            details_json = _serialize_details(details)
            client_time = redact_text(item.get("time") or item.get("client_time"), 80)
            rows.append(
                (
                    redact_text(owner_id, 100),
                    redact_text(session_id, 100),
                    redact_text(device_name, 160),
                    redact_text(platform, 40) or "android",
                    redact_text(app_version, 80),
                    redact_text(git_sha, 80),
                    redact_text(os_version, 120),
                    level,
                    component,
                    event,
                    message,
                    details_json,
                    client_time,
                    received_at,
                )
            )
        if not rows:
            return 0
        with self.db() as conn:
            conn.executemany(
                """INSERT INTO client_runtime_logs(
                       owner_id,session_id,device_name,platform,app_version,git_sha,os_version,
                       level,component,event,message,details_json,client_time,received_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            self._prune(conn)
        return len(rows)

    def _prune(self, conn: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM client_runtime_logs WHERE received_at < ?", (cutoff,))
        conn.execute(
            """DELETE FROM client_runtime_logs
               WHERE id NOT IN (SELECT id FROM client_runtime_logs ORDER BY id DESC LIMIT 50000)"""
        )

    def list(
        self,
        *,
        owner_id: str = "",
        level: str = "",
        component: str = "",
        query: str = "",
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id.strip():
            clauses.append("owner_id=?")
            params.append(owner_id.strip())
        if level.strip().lower() in _ALLOWED_LEVELS:
            clauses.append("level=?")
            params.append(level.strip().lower())
        if component.strip():
            clauses.append("component=?")
            params.append(component.strip()[:120])
        if query.strip():
            clauses.append("(message LIKE ? OR event LIKE ? OR component LIKE ? OR device_name LIKE ?)")
            needle = f"%{query.strip()[:120]}%"
            params.extend([needle, needle, needle, needle])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 5000)))
        with self.db() as conn:
            rows = conn.execute(
                f"SELECT * FROM client_runtime_logs{where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(str(item.pop("details_json", "{}") or "{}"))
            except json.JSONDecodeError:
                item["details"] = {}
            result.append(item)
        return result

    def components(self, limit: int = 100) -> list[str]:
        with self.db() as conn:
            rows = conn.execute(
                "SELECT component,MAX(id) latest FROM client_runtime_logs GROUP BY component ORDER BY latest DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [str(row["component"]) for row in rows if str(row["component"])]

    def owners(self, limit: int = 200) -> list[str]:
        with self.db() as conn:
            rows = conn.execute(
                "SELECT owner_id,MAX(id) latest FROM client_runtime_logs GROUP BY owner_id ORDER BY latest DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [str(row["owner_id"]) for row in rows if str(row["owner_id"])]


_store: ClientRuntimeLogStore | None = None


def client_runtime_log_store() -> ClientRuntimeLogStore:
    global _store
    if _store is None:
        _store = ClientRuntimeLogStore()
        _store.init()
    return _store
