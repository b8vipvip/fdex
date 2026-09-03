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
    """Structured logical-task/attempt projection for bounded Codex retries.

    Phase 7.40 makes ``agent_tasks.task_kind/logical_root_id/attempt_index`` the identity and
    lineage authority. ``codex_retry_attempts`` remains the richer audit ledger for actual
    Provider/Model, structured health, backoff and final retry decisions. Projection merges both:
    a child created immediately before a worker crash is still discoverable from ``agent_tasks``
    even when no retry-ledger row was written yet.
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
            # AgentTaskStore owns creation/migration of the shared tables so every projection path
            # sees task-kind lineage before reading the retry audit ledger.
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

    @staticmethod
    def _lineage_projection(task: dict[str, Any]) -> dict[str, Any]:
        """Create a bounded attempt projection when the richer retry ledger is absent."""

        state = str(task.get("status") or "queued").strip().lower()
        if state not in _ALLOWED_STATES:
            state = "queued"
        return {
            "attempt_task_id": str(task.get("id") or ""),
            "owner_id": str(task.get("owner_id") or ""),
            "root_task_id": str(task.get("logical_root_id") or task.get("id") or ""),
            "parent_task_id": str(task.get("parent_task_id") or ""),
            "attempt_index": max(0, int(task.get("attempt_index") or 0)),
            "state": state,
            "provider_id": 0,
            "provider_name": "",
            "model": "",
            "trigger_code": "",
            "trigger_reason": "",
            "decision_code": "",
            "decision_reason": "",
            "backoff_seconds": 0.0,
            "excluded_provider_ids": [],
            "error": str(task.get("error") or "")[:4000],
            "started_at": "",
            "completed_at": "",
            "created_at": str(task.get("created_at") or ""),
            "updated_at": str(task.get("updated_at") or ""),
            "internal": str(task.get("task_kind") or "") == "auto_retry",
            "audit_pending": True,
        }

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
        task_store = agent_task_store()
        task = task_store.get(owner_id, task_id)
        if task is None:
            return None
        audit_attempt = self.get_attempt(owner_id, task_id)
        root_task_id = str(
            task.get("logical_root_id")
            or (audit_attempt or {}).get("root_task_id")
            or task_id
        )
        lineage = task_store.list_execution_lineage(owner_id, root_task_id)
        audit = {
            str(item.get("attempt_task_id") or ""): item
            for item in self.list_for_root(owner_id, root_task_id)
            if str(item.get("attempt_task_id") or "")
        }
        attempts: list[dict[str, Any]] = []
        for lineage_task in lineage:
            attempt_task_id = str(lineage_task.get("id") or "")
            row = audit.get(attempt_task_id)
            if row is not None:
                projected = dict(row)
                projected["audit_pending"] = False
                projected["internal"] = str(lineage_task.get("task_kind") or "") == "auto_retry"
                attempts.append(projected)
            else:
                attempts.append(self._lineage_projection(lineage_task))

        # Compatibility for a Phase 7.39 ledger row whose corresponding AgentTask was already
        # deleted/corrupted: keep the audit evidence visible rather than silently discarding it.
        known = {str(item.get("attempt_task_id") or "") for item in attempts}
        for attempt_task_id, row in audit.items():
            if attempt_task_id not in known:
                projected = dict(row)
                projected["audit_pending"] = False
                attempts.append(projected)
        attempts.sort(
            key=lambda item: (
                int(item.get("attempt_index") or 0),
                str(item.get("created_at") or ""),
                str(item.get("attempt_task_id") or ""),
            )
        )

        # Do not render a retry-chain panel for a task that has never had an automatic retry and
        # has no retry audit record. Once a child exists, main-table lineage alone is sufficient.
        has_internal = any(bool(item.get("internal")) for item in attempts)
        if not audit and not has_internal:
            return None
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
        requested_is_internal = str(task.get("task_kind") or "") == "auto_retry"
        return {
            "root_task_id": root_task_id,
            "requested_task_id": task_id,
            "requested_is_internal": requested_is_internal,
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