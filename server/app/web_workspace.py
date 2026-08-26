from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import fresh_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not clean.startswith("usr_") or len(clean) < 12:
        raise ValueError("无效的 FDEX 用户")
    return clean


class WebWorkspaceStore:
    """Server-side Web workspace isolated by the canonical FDEX user_id.

    Android historically keeps employee/chat/work/knowledge records in its per-user local SQLite.
    The Web client cannot reuse that device-local database, so the browser-facing product stores
    equivalent records in the center service. Every query is owner-scoped at SQL level; a caller
    never supplies another account's owner id through a form field.
    """

    def __init__(self, path: Path | None = None) -> None:
        cfg = fresh_settings()
        data_dir = Path(cfg.app_dir).expanduser().resolve() / "server" / "data"
        self.path = (path or data_dir / "web-workspace.db").resolve()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_workspace_records (
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    id INTEGER NOT NULL,
                    parent_id INTEGER,
                    sort_key TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id,kind,id)
                );
                CREATE INDEX IF NOT EXISTS idx_web_workspace_owner_kind
                    ON web_workspace_records(owner_id,kind,deleted,sort_key,id);
                CREATE INDEX IF NOT EXISTS idx_web_workspace_parent
                    ON web_workspace_records(owner_id,kind,parent_id,deleted,sort_key,id);
                CREATE TABLE IF NOT EXISTS web_workspace_counters (
                    owner_id TEXT PRIMARY KEY,
                    next_id INTEGER NOT NULL
                );
                """
            )
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def next_id(self, owner_id: str) -> int:
        owner_id = _owner(owner_id)
        self.init()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT next_id FROM web_workspace_counters WHERE owner_id=?", (owner_id,)).fetchone()
            value = int(row["next_id"]) if row is not None else 1
            conn.execute(
                "INSERT INTO web_workspace_counters(owner_id,next_id) VALUES(?,?) "
                "ON CONFLICT(owner_id) DO UPDATE SET next_id=excluded.next_id",
                (owner_id, value + 1),
            )
        return value

    def create(
        self,
        owner_id: str,
        kind: str,
        data: dict[str, Any],
        *,
        parent_id: int | None = None,
        sort_key: str = "",
        record_id: int | None = None,
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        clean_kind = self._kind(kind)
        record_id = int(record_id or self.next_id(owner_id))
        now = _now()
        payload = {**data, "id": record_id}
        with self.db() as conn:
            conn.execute(
                """INSERT INTO web_workspace_records(
                       owner_id,kind,id,parent_id,sort_key,data_json,deleted,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,0,?,?)""",
                (
                    owner_id,
                    clean_kind,
                    record_id,
                    int(parent_id) if parent_id is not None else None,
                    (sort_key or now)[:200],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
        return payload

    def upsert(
        self,
        owner_id: str,
        kind: str,
        record_id: int,
        data: dict[str, Any],
        *,
        parent_id: int | None = None,
        sort_key: str = "",
        deleted: bool = False,
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        clean_kind = self._kind(kind)
        record_id = int(record_id)
        if record_id <= 0:
            raise ValueError("记录 ID 无效")
        now = _now()
        payload = {**data, "id": record_id}
        with self.db() as conn:
            existing = conn.execute(
                "SELECT created_at FROM web_workspace_records WHERE owner_id=? AND kind=? AND id=?",
                (owner_id, clean_kind, record_id),
            ).fetchone()
            created = str(existing["created_at"]) if existing is not None else now
            conn.execute(
                """INSERT INTO web_workspace_records(
                       owner_id,kind,id,parent_id,sort_key,data_json,deleted,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id,kind,id) DO UPDATE SET
                       parent_id=excluded.parent_id,sort_key=excluded.sort_key,data_json=excluded.data_json,
                       deleted=excluded.deleted,updated_at=excluded.updated_at""",
                (
                    owner_id,
                    clean_kind,
                    record_id,
                    int(parent_id) if parent_id is not None else None,
                    (sort_key or now)[:200],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    1 if deleted else 0,
                    created,
                    now,
                ),
            )
        return payload

    def get(self, owner_id: str, kind: str, record_id: int, *, include_deleted: bool = False) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        clean_kind = self._kind(kind)
        self.init()
        sql = "SELECT * FROM web_workspace_records WHERE owner_id=? AND kind=? AND id=?"
        params: list[Any] = [owner_id, clean_kind, int(record_id)]
        if not include_deleted:
            sql += " AND deleted=0"
        with self.db() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            raise KeyError(f"{clean_kind} not found")
        return self._row(row)

    def list(
        self,
        owner_id: str,
        kind: str,
        *,
        parent_id: int | None = None,
        include_deleted: bool = False,
        newest_first: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        owner_id = _owner(owner_id)
        clean_kind = self._kind(kind)
        self.init()
        clauses = ["owner_id=?", "kind=?"]
        params: list[Any] = [owner_id, clean_kind]
        if parent_id is not None:
            clauses.append("parent_id=?")
            params.append(int(parent_id))
        if not include_deleted:
            clauses.append("deleted=0")
        direction = "DESC" if newest_first else "ASC"
        sql = (
            "SELECT * FROM web_workspace_records WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY sort_key {direction}, id {direction} LIMIT ?"
        )
        params.append(max(1, min(int(limit), 5000)))
        with self.db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row(row) for row in rows]

    def update_fields(self, owner_id: str, kind: str, record_id: int, **updates: Any) -> dict[str, Any]:
        current = self.get(owner_id, kind, record_id, include_deleted=True)
        meta_parent = current.pop("_parent_id", None)
        current.pop("_deleted", None)
        current.update(updates)
        sort_key = str(current.get("updated_at") or current.get("created_at") or _now())
        return self.upsert(owner_id, kind, record_id, current, parent_id=meta_parent, sort_key=sort_key)

    def set_deleted(self, owner_id: str, kind: str, record_id: int, deleted: bool) -> None:
        owner_id = _owner(owner_id)
        clean_kind = self._kind(kind)
        self.init()
        with self.db() as conn:
            cur = conn.execute(
                "UPDATE web_workspace_records SET deleted=?,updated_at=? WHERE owner_id=? AND kind=? AND id=?",
                (1 if deleted else 0, _now(), owner_id, clean_kind, int(record_id)),
            )
            if cur.rowcount != 1:
                raise KeyError(f"{clean_kind} not found")

    def restore_all_deleted_messages(self, owner_id: str) -> int:
        owner_id = _owner(owner_id)
        self.init()
        with self.db() as conn:
            cur = conn.execute(
                "UPDATE web_workspace_records SET deleted=0,updated_at=? "
                "WHERE owner_id=? AND kind IN ('message','group_message') AND deleted=1",
                (_now(), owner_id),
            )
        return max(0, int(cur.rowcount or 0))

    def clear_owner(self, owner_id: str) -> int:
        owner_id = _owner(owner_id)
        self.init()
        with self.db() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM web_workspace_records WHERE owner_id=?", (owner_id,)).fetchone()[0])
            conn.execute("DELETE FROM web_workspace_records WHERE owner_id=?", (owner_id,))
            conn.execute("DELETE FROM web_workspace_counters WHERE owner_id=?", (owner_id,))
        return count

    def preferences(self, owner_id: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        try:
            return self.get(owner_id, "preferences", 1)
        except KeyError:
            return self.upsert(
                owner_id,
                "preferences",
                1,
                {
                    "industry": "",
                    "professional_level": "business",
                    "auto_company_mode": False,
                    "default_home": "messages",
                },
                sort_key="preferences",
            )

    def save_preferences(self, owner_id: str, **values: Any) -> dict[str, Any]:
        current = self.preferences(owner_id)
        current.pop("_parent_id", None)
        current.pop("_deleted", None)
        current.update(values)
        return self.upsert(owner_id, "preferences", 1, current, sort_key="preferences")

    def ensure_defaults(self, owner_id: str) -> None:
        owner_id = _owner(owner_id)
        if self.list(owner_id, "employee", limit=1):
            return
        defaults = [
            ("小知", "资料中心", "资料管理员", "负责资料整理、检索、风险提醒和上下文管理。回答要准确、结构清楚，不编造未知事实。"),
            ("小策", "经营中心", "业务策划", "负责目标拆解、商业方案、执行步骤与清单。优先给出可落地的方案和关键风险。"),
            ("小运", "运营中心", "运营主管", "负责日常运营、流程优化、任务推进与复盘。输出简洁、可执行、带优先级。"),
            ("小数", "数据中心", "数据分析师", "负责数据分析、指标解释、异常定位和决策支持。明确区分事实、推断与证据不足。"),
        ]
        for name, department, position, prompt in defaults:
            self.create(
                owner_id,
                "employee",
                {
                    "name": name,
                    "department": department,
                    "position": position,
                    "role_prompt": prompt,
                    "industry": "",
                    "active": True,
                    "knowledge_read": True,
                    "knowledge_write": True,
                    "coding_agent": False,
                },
                sort_key=name,
            )

    @staticmethod
    def _kind(value: str) -> str:
        clean = (value or "").strip().lower()
        allowed = {
            "employee",
            "message",
            "group",
            "group_message",
            "knowledge",
            "project",
            "project_note",
            "project_asset",
            "report",
            "preferences",
        }
        if clean not in allowed:
            raise ValueError("不支持的 Web 工作区记录类型")
        return clean

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["data_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["id"] = int(row["id"])
        payload["_parent_id"] = int(row["parent_id"]) if row["parent_id"] is not None else None
        payload["_deleted"] = bool(row["deleted"])
        payload.setdefault("created_at", str(row["created_at"]))
        payload.setdefault("updated_at", str(row["updated_at"]))
        return payload


_store: WebWorkspaceStore | None = None


def web_workspace_store() -> WebWorkspaceStore:
    global _store
    if _store is None:
        _store = WebWorkspaceStore()
    return _store
