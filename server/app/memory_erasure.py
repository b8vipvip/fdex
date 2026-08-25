from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import anyio
import httpx

from app.config import Settings, fresh_settings
from app.fdex_memory import MemoryScope
from app.memory_scope_registry import MemoryScopeRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MemoryErasureError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = (code or "memory_erasure_failed").strip().lower()
        super().__init__(message or self.code)


@dataclass(frozen=True, slots=True)
class MemoryErasureReport:
    account_hash: str
    memory_scopes: int
    mempalace_rows: int
    qdrant_points: int
    letta_agents: int
    completed: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MemoryErasureRegistry:
    """Durable, content-free tombstone/attempt ledger for destructive memory erasure."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_erasure_jobs (
                account_hash TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                memory_scopes INTEGER NOT NULL DEFAULT 0,
                mempalace_rows INTEGER NOT NULL DEFAULT 0,
                qdrant_points INTEGER NOT NULL DEFAULT 0,
                letta_agents INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_erasure_jobs)").fetchall()}
        if "memory_scopes" not in columns:
            conn.execute("ALTER TABLE memory_erasure_jobs ADD COLUMN memory_scopes INTEGER NOT NULL DEFAULT 0")
        return conn

    def begin(self, account_hash: str, *, memory_scopes: int = 0) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_erasure_jobs(account_hash,phase,memory_scopes,created_at,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(account_hash) DO UPDATE SET
                    phase='started',memory_scopes=excluded.memory_scopes,last_error='',
                    updated_at=excluded.updated_at,completed_at=''
                """,
                (account_hash, "started", int(memory_scopes), now, now),
            )

    def update(
        self,
        account_hash: str,
        phase: str,
        *,
        memory_scopes: int | None = None,
        mempalace_rows: int | None = None,
        qdrant_points: int | None = None,
        letta_agents: int | None = None,
        error: str = "",
        completed: bool = False,
    ) -> None:
        now = _now()
        assignments = ["phase=?", "last_error=?", "updated_at=?", "completed_at=?"]
        values: list[object] = [phase, error[:300], now, now if completed else ""]
        for column, value in (
            ("memory_scopes", memory_scopes),
            ("mempalace_rows", mempalace_rows),
            ("qdrant_points", qdrant_points),
            ("letta_agents", letta_agents),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(int(value))
        values.append(account_hash)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE memory_erasure_jobs SET {','.join(assignments)} WHERE account_hash=?",
                tuple(values),
            )

    def get(self, account_hash: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_erasure_jobs WHERE account_hash=?",
                (account_hash,),
            ).fetchone()
        return dict(row) if row is not None else None


class MemoryErasureService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        qdrant_client: Any | None = None,
        letta_client_factory: Callable[[], Any] | None = None,
        registry_path: Path | None = None,
        scope_registry: MemoryScopeRegistry | None = None,
    ) -> None:
        self.settings = settings or fresh_settings()
        memory_dir = Path(self.settings.fdex_memory_data_dir).expanduser().resolve()
        self.raw_db_path = memory_dir / "mempalace-raw.sqlite3"
        self.letta_state_path = memory_dir / "letta-agent.json"
        self.registry = MemoryErasureRegistry(registry_path or memory_dir / "memory-erasure-registry.sqlite3")
        self.scope_registry = scope_registry or MemoryScopeRegistry(memory_dir / "memory-scope-owners.sqlite3")
        self._qdrant_owned = qdrant_client is None
        self._qdrant = qdrant_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.fdex_memory_qdrant_timeout_seconds),
            follow_redirects=True,
        )
        self._letta_client_factory = letta_client_factory

    @staticmethod
    def account_hash(user_id: str) -> str:
        return hashlib.sha256((user_id or "").strip().encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_user_id(user_id: str) -> str:
        clean = (user_id or "").strip()
        if not clean.startswith("usr_") or len(clean) < 12:
            raise ValueError("invalid FDEX user id")
        return clean

    def account_ids_for_user(self, user_id: str) -> list[str]:
        clean = self._validate_user_id(user_id)
        raw_scopes = [clean, *self.scope_registry.scopes_for_user(clean)]
        return list(dict.fromkeys(MemoryScope(value).account_id for value in raw_scopes if value))

    async def erase_account(self, user_id: str) -> MemoryErasureReport:
        clean = self._validate_user_id(user_id)
        account_hash = self.account_hash(clean)
        account_ids = self.account_ids_for_user(clean)
        self.registry.begin(account_hash, memory_scopes=len(account_ids))
        letta_count = 0
        qdrant_count = 0
        raw_count = 0
        try:
            for account_id in account_ids:
                letta_count += await self._erase_letta_account_id(account_id)
            self.registry.update(account_hash, "letta_erased", memory_scopes=len(account_ids), letta_agents=letta_count)

            rows: list[sqlite3.Row] = []
            for account_id in account_ids:
                rows.extend(await anyio.to_thread.run_sync(self._mempalace_rows, account_id))
            point_ids = list(dict.fromkeys(str(row["point_id"]) for row in rows if str(row["point_id"] or "").strip()))
            qdrant_count = await self._erase_qdrant_points(point_ids)
            self.registry.update(
                account_hash,
                "qdrant_erased",
                memory_scopes=len(account_ids),
                qdrant_points=qdrant_count,
                letta_agents=letta_count,
            )

            raw_count = await anyio.to_thread.run_sync(self._erase_raw_rows_for_accounts, account_ids)
            report = MemoryErasureReport(
                account_hash=account_hash,
                memory_scopes=len(account_ids),
                mempalace_rows=raw_count,
                qdrant_points=qdrant_count,
                letta_agents=letta_count,
            )
            self.registry.update(
                account_hash,
                "completed",
                memory_scopes=len(account_ids),
                mempalace_rows=raw_count,
                qdrant_points=qdrant_count,
                letta_agents=letta_count,
                completed=True,
            )
            return report
        except MemoryErasureError as exc:
            self.registry.update(
                account_hash,
                "failed",
                memory_scopes=len(account_ids),
                mempalace_rows=raw_count,
                qdrant_points=qdrant_count,
                letta_agents=letta_count,
                error=exc.code,
            )
            raise
        except Exception as exc:
            self.registry.update(
                account_hash,
                "failed",
                memory_scopes=len(account_ids),
                mempalace_rows=raw_count,
                qdrant_points=qdrant_count,
                letta_agents=letta_count,
                error=type(exc).__name__,
            )
            raise MemoryErasureError("memory_erasure_exception") from exc

    def status(self, user_id: str) -> dict[str, object]:
        clean = self._validate_user_id(user_id)
        stored = self.registry.get(self.account_hash(clean)) or {}
        return {
            "phase": str(stored.get("phase") or "idle"),
            "memory_scopes": max(int(stored.get("memory_scopes") or 0), len(self.account_ids_for_user(clean))),
            "mempalace_rows": int(stored.get("mempalace_rows") or 0),
            "qdrant_points": int(stored.get("qdrant_points") or 0),
            "letta_agents": int(stored.get("letta_agents") or 0),
            "last_error": str(stored.get("last_error") or ""),
            "updated_at": str(stored.get("updated_at") or ""),
            "completed_at": str(stored.get("completed_at") or ""),
        }

    def _mempalace_rows(self, account_id: str) -> list[sqlite3.Row]:
        if not self.raw_db_path.exists():
            return []
        conn = sqlite3.connect(self.raw_db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mempalace_drawers'").fetchone()
            if table is None:
                return []
            return list(conn.execute("SELECT point_id FROM mempalace_drawers WHERE account_id=? ORDER BY rowid", (account_id,)).fetchall())
        finally:
            conn.close()

    async def _erase_qdrant_points(self, point_ids: list[str]) -> int:
        if not point_ids:
            return 0
        url = f"{self.settings.fdex_memory_qdrant_url.rstrip('/')}/collections/{self.settings.fdex_memory_qdrant_collection}/points/delete?wait=true"
        deleted = 0
        for offset in range(0, len(point_ids), 256):
            chunk = point_ids[offset : offset + 256]
            try:
                response = await self._qdrant.post(url, json={"points": chunk})
            except httpx.TimeoutException as exc:
                raise MemoryErasureError("mempalace_qdrant_delete_timeout") from exc
            except httpx.HTTPError as exc:
                raise MemoryErasureError("mempalace_qdrant_delete_unavailable") from exc
            if response.status_code == 404:
                return len(point_ids)
            if response.status_code in {401, 403}:
                raise MemoryErasureError("mempalace_qdrant_delete_auth_failed")
            if response.status_code == 429:
                raise MemoryErasureError("mempalace_qdrant_delete_rate_limited")
            if response.status_code >= 500:
                raise MemoryErasureError("mempalace_qdrant_delete_server_error")
            if response.status_code >= 400:
                raise MemoryErasureError("mempalace_qdrant_delete_rejected")
            deleted += len(chunk)
        return deleted

    def _erase_raw_rows(self, account_id: str) -> int:
        return self._erase_raw_rows_for_accounts([account_id])

    def _erase_raw_rows_for_accounts(self, account_ids: list[str]) -> int:
        if not self.raw_db_path.exists() or not account_ids:
            return 0
        conn = sqlite3.connect(self.raw_db_path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA secure_delete=ON")
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mempalace_drawers'").fetchone()
            if table is None:
                return 0
            placeholders = ",".join("?" for _ in account_ids)
            cursor = conn.execute(f"DELETE FROM mempalace_drawers WHERE account_id IN ({placeholders})", tuple(account_ids))
            deleted = max(0, int(cursor.rowcount))
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            return deleted
        finally:
            conn.close()

    def _load_letta_agents(self) -> dict[str, str]:
        if not self.letta_state_path.exists():
            return {}
        try:
            payload = json.loads(self.letta_state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise MemoryErasureError("letta_state_invalid") from exc
        agents = payload.get("agents") if isinstance(payload, dict) else None
        if not isinstance(agents, dict):
            raise MemoryErasureError("letta_state_invalid")
        return {str(scope_key): str(agent_id) for scope_key, agent_id in agents.items() if str(scope_key).strip() and str(agent_id).strip()}

    def _default_letta_client(self) -> Any:
        from letta_client import Letta

        kwargs: dict[str, Any] = {"base_url": self.settings.fdex_letta_base_url.rstrip("/")}
        password = self.settings.fdex_letta_server_password.strip()
        if password:
            kwargs["api_key"] = password
        return Letta(**kwargs)

    @staticmethod
    def _exception_status(exc: Exception) -> int | None:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    def _persist_letta_without(self, removed_scope_key: str) -> None:
        current = self._load_letta_agents()
        current.pop(removed_scope_key, None)
        self.letta_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.letta_state_path.with_suffix(".json.erasure.tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "agents": current}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.letta_state_path)

    async def _erase_letta(self, scope: MemoryScope) -> int:
        return await self._erase_letta_account_id(scope.account_id)

    async def _erase_letta_account_id(self, account_id: str) -> int:
        agents = self._load_letta_agents()
        prefix = f"acct.{account_id}.vault."
        owned = [(scope_key, agent_id) for scope_key, agent_id in agents.items() if scope_key.startswith(prefix)]
        if not owned:
            return 0
        client = self._letta_client_factory() if self._letta_client_factory is not None else self._default_letta_client()
        deleted = 0
        for scope_key, agent_id in owned:
            try:
                await asyncio.wait_for(
                    anyio.to_thread.run_sync(lambda aid=agent_id: client.agents.delete(aid), abandon_on_cancel=True),
                    timeout=self.settings.fdex_letta_timeout_seconds,
                )
            except TimeoutError as exc:
                raise MemoryErasureError("letta_delete_timeout") from exc
            except Exception as exc:
                status = self._exception_status(exc)
                if status != 404:
                    if status in {401, 403}:
                        code = "letta_delete_auth_failed"
                    elif status == 429:
                        code = "letta_delete_rate_limited"
                    elif status is not None and status >= 500:
                        code = "letta_delete_server_error"
                    else:
                        code = "letta_delete_failed"
                    raise MemoryErasureError(code) from exc
            self._persist_letta_without(scope_key)
            deleted += 1
        return deleted

    async def aclose(self) -> None:
        if self._qdrant_owned:
            await self._qdrant.aclose()


async def erase_account_memory(user_id: str) -> dict[str, object]:
    service = MemoryErasureService()
    try:
        return (await service.erase_account(user_id)).to_dict()
    finally:
        await service.aclose()


def memory_erasure_status(user_id: str) -> dict[str, object]:
    settings = fresh_settings()
    memory_dir = Path(settings.fdex_memory_data_dir).expanduser().resolve()
    service = MemoryErasureService(
        settings,
        qdrant_client=_NoopAsyncClient(),
        scope_registry=MemoryScopeRegistry(memory_dir / "memory-scope-owners.sqlite3"),
    )
    return service.status(user_id)


class _NoopAsyncClient:
    async def aclose(self) -> None:
        return None
