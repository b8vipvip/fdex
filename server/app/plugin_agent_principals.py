from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agent_tasks import agent_task_store
from app.config import fresh_settings
from app.web_workspace import web_workspace_store

_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_TASK_RE = re.compile(r"^[0-9a-f]{32}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-agent-principals.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _task(value: str) -> str:
    clean = (value or "").strip().lower()
    if not _TASK_RE.fullmatch(clean):
        raise ValueError("Agent task id is invalid")
    return clean


class PluginAgentPrincipalStore:
    """Bind an Agent task lineage to the user-created 智体 that initiated it.

    AgentTask deliberately predates the generalized 智体 model and remains a reusable runtime
    object for API-created jobs. Rather than weakening that contract, this sidecar table records a
    principal only for tasks created from an employee/智体 chat. Automatic retry children inherit
    the nearest explicit parent binding by walking durable task lineage. API-created tasks with no
    binding receive no per-agent plugin capability.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path.resolve()
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS plugin_agent_principals (
                       owner_id TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       employee_id INTEGER NOT NULL,
                       employee_name TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL,
                       PRIMARY KEY(owner_id,task_id)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_plugin_principal_owner_employee ON plugin_agent_principals(owner_id,employee_id)"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._initialized = True

    def bind(self, owner_id: str, task_id: str, employee: dict[str, Any]) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        clean_task = _task(task_id)
        employee_id = int(employee.get("id") or 0)
        if employee_id <= 0:
            raise ValueError("智体 id 无效，不能建立插件主体绑定")

        # The task/principal pair is immutable. Check the durable binding before dereferencing the
        # caller-supplied employee id so an attempted rebind can never be disguised as a missing or
        # deleted employee lookup. This also keeps the security error deterministic across workers.
        with sqlite3.connect(self.path) as conn:
            existing = conn.execute(
                "SELECT employee_id FROM plugin_agent_principals WHERE owner_id=? AND task_id=?",
                (clean_owner, clean_task),
            ).fetchone()
        if existing is not None and int(existing[0]) != employee_id:
            raise ValueError("Agent task 已绑定其它智体，拒绝变更插件主体")

        # Re-read the owner-scoped row so a caller cannot bind a forged employee projection.
        try:
            persisted = web_workspace_store().get(clean_owner, "employee", employee_id)
        except (KeyError, ValueError) as exc:
            raise ValueError("智体不存在，不能建立插件主体绑定") from exc
        if bool(persisted.get("_deleted")) or not bool(persisted.get("active", True)):
            raise ValueError("智体已停用，不能建立插件主体绑定")
        task_row = agent_task_store().get(clean_owner, clean_task)
        if task_row is None:
            raise ValueError("Agent task 不存在，不能建立插件主体绑定")
        name = str(persisted.get("name") or employee.get("name") or "")[:80]
        with sqlite3.connect(self.path) as conn:
            # Re-check inside the write transaction to close the race between the initial immutable
            # check and INSERT when multiple workers try to bind the same task concurrently.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT employee_id FROM plugin_agent_principals WHERE owner_id=? AND task_id=?",
                (clean_owner, clean_task),
            ).fetchone()
            if existing is not None and int(existing[0]) != employee_id:
                raise ValueError("Agent task 已绑定其它智体，拒绝变更插件主体")
            conn.execute(
                """INSERT INTO plugin_agent_principals(owner_id,task_id,employee_id,employee_name,created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(owner_id,task_id) DO NOTHING""",
                (clean_owner, clean_task, employee_id, name, _now()),
            )
        return {
            "owner_id": clean_owner,
            "task_id": clean_task,
            "employee_id": employee_id,
            "employee_name": name,
            "source_task_id": clean_task,
        }

    def explicit(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        self.init()
        clean_owner = _owner(owner_id)
        clean_task = _task(task_id)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM plugin_agent_principals WHERE owner_id=? AND task_id=? LIMIT 1",
                (clean_owner, clean_task),
            ).fetchone()
        if row is None:
            return None
        return {
            "owner_id": str(row["owner_id"]),
            "task_id": str(row["task_id"]),
            "employee_id": int(row["employee_id"]),
            "employee_name": str(row["employee_name"]),
            "created_at": str(row["created_at"]),
            "source_task_id": str(row["task_id"]),
        }

    def resolve(self, owner_id: str, task_id: str, *, max_depth: int = 16) -> dict[str, Any] | None:
        """Resolve explicit binding or inherit it through immutable Agent parent lineage."""
        clean_owner = _owner(owner_id)
        current = _task(task_id)
        seen: set[str] = set()
        for _ in range(max(1, min(int(max_depth), 64))):
            if current in seen:
                return None
            seen.add(current)
            direct = self.explicit(clean_owner, current)
            if direct is not None:
                direct["resolved_for_task_id"] = _task(task_id)
                return direct
            try:
                row = agent_task_store().get(clean_owner, current)
            except ValueError:
                row = None
            parent = str((row or {}).get("parent_task_id") or "").strip().lower()
            if not parent:
                return None
            try:
                current = _task(parent)
            except ValueError:
                return None
        return None

    def active_employee(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        binding = self.resolve(owner_id, task_id)
        if binding is None:
            return None
        try:
            employee = web_workspace_store().get(_owner(owner_id), "employee", int(binding["employee_id"]))
        except (KeyError, ValueError):
            return None
        if bool(employee.get("_deleted")) or not bool(employee.get("active", True)):
            return None
        return employee

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute("DELETE FROM plugin_agent_principals WHERE owner_id=?", (_owner(owner_id),))
        return max(0, int(cur.rowcount or 0))


@lru_cache(maxsize=1)
def plugin_agent_principal_store() -> PluginAgentPrincipalStore:
    store = PluginAgentPrincipalStore()
    store.init()
    return store
