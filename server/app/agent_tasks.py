from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_OWNER = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")
_TERMINAL = {"succeeded", "failed", "canceled"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("invalid Agent owner scope")
    return clean


def _task_id(value: str) -> str:
    clean = (value or "").strip().lower()
    if not _TASK_ID.fullmatch(clean):
        raise ValueError("invalid Agent task id")
    return clean


class TaskRunBusy(RuntimeError):
    pass


class AgentTaskStore:
    """Durable owner-scoped task history for Coding Agent.

    The runtime can be served by several Uvicorn workers and can be restarted without losing
    task state. A small flock file serializes execution of one task across workers. No model
    prompt or tool result is written outside the owner-scoped task row.
    """

    def __init__(self, path: Path | None = None, lock_root: Path | None = None) -> None:
        data = Path(__file__).resolve().parents[1] / "data"
        self.path = (path or data / "agent-tasks.db").resolve()
        self.lock_root = (lock_root or data / "agent-task-locks").resolve()
        self._initialized = False
        self._init_lock = threading.Lock()

    def init(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_root.mkdir(parents=True, exist_ok=True)
            for item in (self.path.parent, self.lock_root):
                try:
                    os.chmod(item, 0o700)
                except OSError:
                    pass
            with self.db() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS agent_tasks (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        project_id INTEGER,
                        project_name TEXT NOT NULL DEFAULT '',
                        repository TEXT NOT NULL DEFAULT '',
                        base_branch TEXT NOT NULL DEFAULT 'main',
                        status TEXT NOT NULL DEFAULT 'queued',
                        result TEXT NOT NULL DEFAULT '',
                        error TEXT NOT NULL DEFAULT '',
                        branch TEXT NOT NULL DEFAULT '',
                        worktree TEXT NOT NULL DEFAULT '',
                        commit_sha TEXT NOT NULL DEFAULT '',
                        pushed INTEGER NOT NULL DEFAULT 0,
                        pr_url TEXT NOT NULL DEFAULT '',
                        changed_files_json TEXT NOT NULL DEFAULT '[]',
                        events_json TEXT NOT NULL DEFAULT '[]',
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        parent_task_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_updated
                        ON agent_tasks(owner_id, updated_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_status
                        ON agent_tasks(owner_id, status, updated_at DESC);
                    """
                )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._initialized = True

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

    @staticmethod
    def _iso(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat(timespec="microseconds")
        return str(value or _now())

    @staticmethod
    def _event_payload(event: Any) -> dict[str, str]:
        return {
            "type": str(getattr(event, "type", ""))[:100],
            "message": str(getattr(event, "message", ""))[:4000],
            "created_at": AgentTaskStore._iso(getattr(event, "created_at", None)),
        }

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _merge_events(existing: Any, incoming: list[dict[str, str]]) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in [*AgentTaskStore._json_list(existing), *incoming]:
            if not isinstance(item, dict):
                continue
            normalized = {
                "type": str(item.get("type") or "")[:100],
                "message": str(item.get("message") or "")[:4000],
                "created_at": str(item.get("created_at") or "")[:80],
            }
            fingerprint = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(normalized)
        return merged[-300:]

    def save(self, task: Any) -> None:
        self.init()
        task_id = _task_id(str(task.id))
        owner_id = _owner(str(task.owner_id))
        changed = sorted(str(item)[:1000] for item in getattr(task, "changed_files", set()))
        events = [self._event_payload(item) for item in list(getattr(task, "events", []))[-300:]]
        values: dict[str, Any] = {
            "id": task_id,
            "owner_id": owner_id,
            "prompt": str(task.prompt)[:12000],
            "project_id": getattr(task, "project_id", None),
            "project_name": str(getattr(task, "project_name", ""))[:200],
            "repository": str(getattr(task, "repository", ""))[:300],
            "base_branch": str(getattr(task, "base_branch", "main"))[:180],
            "status": str(getattr(task, "status", "queued"))[:30],
            "result": str(getattr(task, "result", ""))[:200000],
            "error": str(getattr(task, "error", ""))[:10000],
            "branch": str(getattr(task, "branch", ""))[:300],
            "worktree": str(getattr(task, "worktree", ""))[:2000],
            "commit_sha": str(getattr(task, "commit_sha", ""))[:100],
            "pushed": int(bool(getattr(task, "pushed", False))),
            "pr_url": str(getattr(task, "pr_url", ""))[:2000],
            "changed_files_json": json.dumps(changed, ensure_ascii=False, separators=(",", ":")),
            "events_json": json.dumps(events, ensure_ascii=False, separators=(",", ":")),
            "cancel_requested": int(bool(getattr(task, "cancel_requested", False))),
            "parent_task_id": str(getattr(task, "parent_task_id", ""))[:32],
            "created_at": self._iso(getattr(task, "created_at", None)),
            "updated_at": self._iso(getattr(task, "updated_at", None)),
        }
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if existing_row is not None:
                existing = dict(existing_row)
                existing_status = str(existing.get("status") or "")
                incoming_status = str(values["status"])
                values["owner_id"] = str(existing["owner_id"])
                values["created_at"] = str(existing["created_at"])
                values["updated_at"] = max(str(existing["updated_at"]), str(values["updated_at"]))
                values["cancel_requested"] = int(
                    bool(existing.get("cancel_requested")) or bool(values["cancel_requested"])
                )
                existing_changed = [str(item)[:1000] for item in self._json_list(existing.get("changed_files_json"))]
                values["changed_files_json"] = json.dumps(
                    sorted(set(existing_changed).union(changed)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                values["events_json"] = json.dumps(
                    self._merge_events(existing.get("events_json"), events),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for field in ("branch", "commit_sha", "pr_url", "parent_task_id"):
                    if not values[field] and existing.get(field):
                        values[field] = existing[field]
                values["pushed"] = int(bool(existing.get("pushed")) or bool(values["pushed"]))
                if existing_status in _TERMINAL:
                    # A stale worker must never revive or replace a terminal task. Cleanup may
                    # still clear the worktree; once empty, that release is also monotonic.
                    values["status"] = existing_status
                    values["result"] = existing["result"]
                    values["error"] = existing["error"]
                    if not existing.get("worktree") or not values["worktree"]:
                        values["worktree"] = ""
                elif not values["worktree"] and incoming_status not in _TERMINAL:
                    values["worktree"] = existing.get("worktree") or ""
            conn.execute(
                """
                INSERT INTO agent_tasks(
                    id,owner_id,prompt,project_id,project_name,repository,base_branch,status,
                    result,error,branch,worktree,commit_sha,pushed,pr_url,changed_files_json,
                    events_json,cancel_requested,parent_task_id,created_at,updated_at
                ) VALUES(
                    :id,:owner_id,:prompt,:project_id,:project_name,:repository,:base_branch,:status,
                    :result,:error,:branch,:worktree,:commit_sha,:pushed,:pr_url,:changed_files_json,
                    :events_json,:cancel_requested,:parent_task_id,:created_at,:updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    owner_id=excluded.owner_id,prompt=excluded.prompt,project_id=excluded.project_id,
                    project_name=excluded.project_name,repository=excluded.repository,
                    base_branch=excluded.base_branch,status=excluded.status,result=excluded.result,
                    error=excluded.error,branch=excluded.branch,worktree=excluded.worktree,
                    commit_sha=excluded.commit_sha,pushed=excluded.pushed,pr_url=excluded.pr_url,
                    changed_files_json=excluded.changed_files_json,events_json=excluded.events_json,
                    cancel_requested=excluded.cancel_requested,
                    parent_task_id=excluded.parent_task_id,
                    created_at=excluded.created_at,updated_at=excluded.updated_at
                """,
                values,
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("changed_files_json", "events_json"):
            try:
                data[key.removesuffix("_json")] = json.loads(str(data.pop(key) or "[]"))
            except json.JSONDecodeError:
                data[key.removesuffix("_json")] = []
        data["pushed"] = bool(data["pushed"])
        data["cancel_requested"] = bool(data["cancel_requested"])
        return data

    def get_any(self, task_id: str) -> dict[str, Any] | None:
        self.init()
        task_id = _task_id(task_id)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        return self._row(row) if row is not None else None

    def get(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        self.init()
        owner_id = _owner(owner_id)
        task_id = _task_id(task_id)
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM agent_tasks WHERE id=? AND owner_id=?",
                (task_id, owner_id),
            ).fetchone()
        return self._row(row) if row is not None else None

    def list(self, owner_id: str, *, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        self.init()
        owner_id = _owner(owner_id)
        limit = max(1, min(int(limit), 100))
        status = (status or "").strip().lower()
        with self.db() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM agent_tasks WHERE owner_id=? AND status=? ORDER BY updated_at DESC,id DESC LIMIT ?",
                    (owner_id, status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_tasks WHERE owner_id=? ORDER BY updated_at DESC,id DESC LIMIT ?",
                    (owner_id, limit),
                ).fetchall()
        return [self._row(row) for row in rows]

    def list_releasable(self, owner_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return terminal tasks that still own a worktree.

        Filtering in SQLite is important: taking the newest N task-history rows first can hide
        an older worktree forever when newer task rows have already been cleaned.
        """
        self.init()
        owner_id = _owner(owner_id)
        limit = max(1, min(int(limit), 1000))
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_tasks
                WHERE owner_id=? AND status IN ('succeeded','failed','canceled') AND worktree<>''
                ORDER BY updated_at,id LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def active_count(self, owner_id: str) -> int:
        self.init()
        owner_id = _owner(owner_id)
        with self.db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM agent_tasks WHERE owner_id=? AND status IN ('queued','running')",
                (owner_id,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def request_cancel(self, owner_id: str, task_id: str, *, force_terminal: bool = False) -> dict[str, Any]:
        current = self.get(owner_id, task_id)
        if current is None:
            raise KeyError("task not found")
        if str(current["status"]) in _TERMINAL:
            return current
        now = _now()
        status = "canceled" if force_terminal or str(current["status"]) == "queued" else str(current["status"])
        with self.db() as conn:
            conn.execute(
                "UPDATE agent_tasks SET cancel_requested=1,status=?,updated_at=? WHERE id=? AND owner_id=?",
                (status, now, _task_id(task_id), _owner(owner_id)),
            )
        updated = self.get(owner_id, task_id)
        if updated is None:
            raise KeyError("task not found")
        return updated

    def cancel_requested(self, task_id: str) -> bool:
        row = self.get_any(task_id)
        return bool(row and row.get("cancel_requested"))

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        owner_id = _owner(owner_id)
        with self.db() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE owner_id=?", (owner_id,)).fetchone()[0])
            conn.execute("DELETE FROM agent_tasks WHERE owner_id=?", (owner_id,))
        return count

    @contextmanager
    def run_lock(self, task_id: str) -> Iterator[None]:
        self.init()
        task_id = _task_id(task_id)
        path = (self.lock_root / f"{task_id}.lock").resolve()
        if self.lock_root not in path.parents:
            raise ValueError("task lock escaped lock root")
        handle = path.open("a+", encoding="utf-8")
        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError as exc:
                raise TaskRunBusy("Agent task is already running") from exc
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


@lru_cache(maxsize=1)
def agent_task_store() -> AgentTaskStore:
    store = AgentTaskStore()
    store.init()
    return store
