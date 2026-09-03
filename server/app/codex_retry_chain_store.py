from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Iterator

from app.agent_tasks import agent_task_store


_ALLOWED_STATES = {"queued", "running", "succeeded", "failed", "blocked", "canceled"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _json_ids(values: Any) -> str:
    ids: set[int] = set()
    for item in values or ():
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.add(value)
    return json.dumps(sorted(ids), separators=(",", ":"))


class CodexRetryChainStore:
    """Structured logical-task/attempt ledger for bounded Codex retries.

    AgentTask rows remain the execution authority. This table is a projection/audit layer that
    records which task is the user-facing logical root, which tasks are internal retry attempts,
    which Provider each attempt actually used, and which structured health decision caused the
    next attempt to exist. It never decides whether a retry is allowed.
    """

    def __init__(self) -> None:
        self.path = agent_task_store().path
        self._initialized = False
        self._init_lock = threading.Lock()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
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
            # AgentTaskStore owns creation/migration of the shared table so every code path that
            # lists tasks can safely reference it even before this projection helper is imported.
            agent_task_store().init()
            self._initialized = True

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        try:
            parsed = json.loads(str(data.pop("excluded_provider_ids_json") or "[]"))
        except json.JSONDecodeError:
            parsed = []
        data["excluded_provider_ids"] = [int(item) for item in parsed if isinstance(item, int) and item > 0]
        data["internal"] = int(data.get("attempt_index") or 0) > 0
        return data

    def record_queued(
        self,
        *,
        owner_id: str,
        root_task_id: str,
        attempt_task_id: str,
        parent_task_id: str,
        attempt_index: int,
        trigger_code: str = "",
        trigger_reason: str = "",
        backoff_seconds: float = 0.0,
        excluded_provider_ids: Any = (),
    ) -> dict[str, Any]:
        self.init()
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO codex_retry_attempts(
                    owner_id,root_task_id,attempt_task_id,parent_task_id,attempt_index,state,
                    trigger_code,trigger_reason,backoff_seconds,excluded_provider_ids_json,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?)
                ON CONFLICT(attempt_task_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    root_task_id=excluded.root_task_id,
                    parent_task_id=excluded.parent_task_id,
                    attempt_index=excluded.attempt_index,
                    trigger_code=CASE WHEN excluded.trigger_code<>'' THEN excluded.trigger_code ELSE codex_retry_attempts.trigger_code END,
                    trigger_reason=CASE WHEN excluded.trigger_reason<>'' THEN excluded.trigger_reason ELSE codex_retry_attempts.trigger_reason END,
                    backoff_seconds=CASE WHEN excluded.backoff_seconds>0 THEN excluded.backoff_seconds ELSE codex_retry_attempts.backoff_seconds END,
                    excluded_provider_ids_json=CASE WHEN excluded.excluded_provider_ids_json<>'[]' THEN excluded.excluded_provider_ids_json ELSE codex_retry_attempts.excluded_provider_ids_json END,
                    updated_at=excluded.updated_at
                """,
                (
                    owner_id,
                    root_task_id,
                    attempt_task_id,
                    parent_task_id,
                    max(0, int(attempt_index)),
                    str(trigger_code or "")[:100],
                    str(trigger_reason or "")[:2000],
                    max(0.0, float(backoff_seconds or 0.0)),
                    _json_ids(excluded_provider_ids),
                    now,
                    now,
                ),
            )
        row = self.get_attempt(owner_id, attempt_task_id)
        assert row is not None
        return row

    def record_started(
        self,
        *,
        owner_id: str,
        root_task_id: str,
        attempt_task_id: str,
        parent_task_id: str,
        attempt_index: int,
        provider_id: int = 0,
        provider_name: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        self.record_queued(
            owner_id=owner_id,
            root_task_id=root_task_id,
            attempt_task_id=attempt_task_id,
            parent_task_id=parent_task_id,
            attempt_index=attempt_index,
        )
        now = _now()
        with self.db() as conn:
            conn.execute(
                """
                UPDATE codex_retry_attempts
                SET state='running',provider_id=?,provider_name=?,model=?,started_at=COALESCE(NULLIF(started_at,''),?),updated_at=?
                WHERE owner_id=? AND attempt_task_id=?
                """,
                (
                    max(0, int(provider_id or 0)),
                    str(provider_name or "")[:300],
                    str(model or "")[:300],
                    now,
                    now,
                    owner_id,
                    attempt_task_id,
                ),
            )
        row = self.get_attempt(owner_id, attempt_task_id)
        assert row is not None
        return row

    def record_decision(
        self,
        *,
        owner_id: str,
        attempt_task_id: str,
        state: str,
        decision_code: str,
        decision_reason: str,
        error: str = "",
    ) -> None:
        self.init()
        clean_state = str(state or "failed").strip().lower()
        if clean_state not in _ALLOWED_STATES:
            raise ValueError("invalid Codex retry attempt state")
        now = _now()
        terminal_at = now if clean_state in {"succeeded", "failed", "blocked", "canceled"} else ""
        with self.db() as conn:
            conn.execute(
                """
                UPDATE codex_retry_attempts
                SET state=?,decision_code=?,decision_reason=?,error=?,
                    completed_at=CASE WHEN ?<>'' THEN ? ELSE completed_at END,updated_at=?
                WHERE owner_id=? AND attempt_task_id=?
                """,
                (
                    clean_state,
                    str(decision_code or "")[:100],
                    str(decision_reason or "")[:2000],
                    str(error or "")[:4000],
                    terminal_at,
                    terminal_at,
                    now,
                    owner_id,
                    attempt_task_id,
                ),
            )

    def record_terminal(
        self,
        *,
        owner_id: str,
        attempt_task_id: str,
        state: str,
        error: str = "",
    ) -> None:
        self.record_decision(
            owner_id=owner_id,
            attempt_task_id=attempt_task_id,
            state=state,
            decision_code="",
            decision_reason="",
            error=error,
        )

    def get_attempt(self, owner_id: str, attempt_task_id: str) -> dict[str, Any] | None:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_retry_attempts WHERE owner_id=? AND attempt_task_id=?",
                (owner_id, attempt_task_id),
            ).fetchone()
        return self._row(row)

    def list_for_root(self, owner_id: str, root_task_id: str) -> list[dict[str, Any]]:
        self.init()
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codex_retry_attempts
                WHERE owner_id=? AND root_task_id=?
                ORDER BY attempt_index ASC,created_at ASC,attempt_task_id ASC
                """,
                (owner_id, root_task_id),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def chain_for_task(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        self.init()
        attempt = self.get_attempt(owner_id, task_id)
        root_task_id = str((attempt or {}).get("root_task_id") or task_id)
        attempts = self.list_for_root(owner_id, root_task_id)
        if not attempts:
            return None
        latest = attempts[-1]
        active = next(
            (
                item
                for item in reversed(attempts)
                if str(item.get("state") or "") in {"queued", "running"}
            ),
            None,
        )
        return {
            "root_task_id": root_task_id,
            "requested_task_id": task_id,
            "requested_is_internal": bool(attempt and int(attempt.get("attempt_index") or 0) > 0),
            "attempt_count": len(attempts),
            "retry_count": max(0, len(attempts) - 1),
            "active_attempt_task_id": str((active or {}).get("attempt_task_id") or ""),
            "latest_attempt_task_id": str(latest.get("attempt_task_id") or root_task_id),
            "latest_state": str(latest.get("state") or ""),
            "attempts": attempts,
        }


@lru_cache(maxsize=1)
def codex_retry_chain_store() -> CodexRetryChainStore:
    store = CodexRetryChainStore()
    store.init()
    return store
