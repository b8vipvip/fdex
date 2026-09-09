from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from app.codex_provider_compatibility import DB_PATH


SMOKE_RUN_STALE_SECONDS = 1800
_ACTIVE_STATUSES = {"queued", "running"}


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class CodexProviderSmokeRunStore:
    """Durable operator-facing state for the currently/latest requested smoke.

    Compatibility proof remains in the existing compatibility table. This table
    answers a different question: "what is the smoke I just started doing now?"
    Keeping it separate prevents a running attempt from invalidating a still-valid
    previous production proof while giving every admin worker the same progress.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path.resolve()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS smoke_runs (
                    provider_id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    runtime_version TEXT NOT NULL DEFAULT '',
                    runtime_source TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["provider_id"] = int(data["provider_id"])
        now = _now_dt()
        started = _parse_time(str(data.get("started_at") or ""))
        updated = _parse_time(str(data.get("updated_at") or ""))
        data["elapsed_seconds"] = (
            max(0, int((now - started).total_seconds())) if started is not None else 0
        )
        active = str(data.get("status") or "") in _ACTIVE_STATUSES
        if active and updated is not None:
            active = now - updated <= timedelta(seconds=SMOKE_RUN_STALE_SECONDS)
        data["active"] = active
        data["stale"] = str(data.get("status") or "") in _ACTIVE_STATUSES and not active
        return data

    def get(self, provider_id: int) -> dict[str, Any] | None:
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM smoke_runs WHERE provider_id=?",
                (int(provider_id),),
            ).fetchone()
        return self._row(row)

    def begin(
        self,
        provider_id: int,
        *,
        runtime_version: str,
        runtime_source: str,
        model: str,
    ) -> dict[str, Any]:
        current = self.get(int(provider_id))
        if current is not None and bool(current.get("active")):
            raise RuntimeError(
                f"该供应商已有正在执行的 full smoke（已用时 {int(current.get('elapsed_seconds') or 0)} 秒）"
            )
        run_id = uuid.uuid4().hex
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO smoke_runs(
                    provider_id,run_id,status,stage,started_at,updated_at,finished_at,
                    runtime_version,runtime_source,model,detail,error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    status=excluded.status,
                    stage=excluded.stage,
                    started_at=excluded.started_at,
                    updated_at=excluded.updated_at,
                    finished_at=excluded.finished_at,
                    runtime_version=excluded.runtime_version,
                    runtime_source=excluded.runtime_source,
                    model=excluded.model,
                    detail=excluded.detail,
                    error=excluded.error
                """,
                (
                    int(provider_id),
                    run_id,
                    "queued",
                    "queued",
                    now,
                    now,
                    "",
                    str(runtime_version)[:160],
                    str(runtime_source)[:80],
                    str(model)[:240],
                    "已进入后台队列，准备启动官方 Codex app-server",
                    "",
                ),
            )
        return self.get(int(provider_id)) or {}

    def update(
        self,
        provider_id: int,
        run_id: str,
        *,
        status: str = "running",
        stage: str = "full-smoke",
        detail: str = "",
    ) -> dict[str, Any] | None:
        with self.db() as conn:
            conn.execute(
                """
                UPDATE smoke_runs
                SET status=?,stage=?,updated_at=?,detail=?
                WHERE provider_id=? AND run_id=?
                """,
                (
                    str(status)[:40],
                    str(stage)[:80],
                    _now(),
                    str(detail)[:1000],
                    int(provider_id),
                    str(run_id),
                ),
            )
        return self.get(int(provider_id))

    def finish(
        self,
        provider_id: int,
        run_id: str,
        *,
        status: str,
        stage: str,
        detail: str = "",
        error: str = "",
    ) -> dict[str, Any] | None:
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                UPDATE smoke_runs
                SET status=?,stage=?,updated_at=?,finished_at=?,detail=?,error=?
                WHERE provider_id=? AND run_id=?
                """,
                (
                    str(status)[:40],
                    str(stage)[:80],
                    now,
                    now,
                    str(detail)[:1000],
                    str(error)[:4000],
                    int(provider_id),
                    str(run_id),
                ),
            )
        return self.get(int(provider_id))


@lru_cache(maxsize=1)
def codex_provider_smoke_run_store() -> CodexProviderSmokeRunStore:
    return CodexProviderSmokeRunStore()
