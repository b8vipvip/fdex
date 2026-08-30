from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from app.config import SERVER_DIR, fresh_settings

COMPATIBILITY_MAX_AGE_HOURS = 168
LEVEL_ORDER = {"none": 0, "wire": 1, "tools": 2, "full": 3}
DB_PATH = SERVER_DIR / "data" / "codex-provider-compatibility.db"


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse_json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def normalize_level(value: str) -> str:
    level = str(value or "none").strip().lower()
    return level if level in LEVEL_ORDER else "none"


def level_at_least(value: str, required: str) -> bool:
    return LEVEL_ORDER[normalize_level(value)] >= LEVEL_ORDER[normalize_level(required)]


def provider_runtime_fingerprint(provider: dict[str, Any], runtime: Any) -> str:
    """Bind a compatibility result to every input that can change Codex wire/tool behavior.

    The API key itself is never persisted. It contributes only to the outer SHA-256 fingerprint so
    rotating credentials immediately invalidates an old smoke result without exposing the secret.
    """
    from app.codex_subagent_governance import codex_subagent_cli_overrides

    settings = fresh_settings()
    api_key = str(provider.get("api_key") or "")
    payload = {
        "v": 1,
        "provider_id": int(provider.get("id") or 0),
        "base_url": str(provider.get("base_url") or "").strip().rstrip("/"),
        "api_key_sha256": hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else "",
        "main_text_model": str(provider.get("main_text_model") or "").strip(),
        "protocol_order": [str(item) for item in provider.get("protocol_order") or []],
        "timeout_seconds": int(provider.get("timeout_seconds") or 60),
        "runtime_path": str(getattr(runtime, "path", "") or ""),
        "runtime_version": str(getattr(runtime, "version", "") or ""),
        "runtime_source": str(getattr(runtime, "source", "") or ""),
        "governance": list(codex_subagent_cli_overrides()),
        "memory_mb": int(settings.fdex_agent_sandbox_memory_mb),
        "cpu_percent": int(settings.fdex_agent_sandbox_cpu_percent),
        "pids_max": int(settings.fdex_agent_sandbox_pids_max),
        "app_version": str(settings.app_version),
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


class CodexProviderCompatibilityStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = path.resolve()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            self._init_conn(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _init_conn(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS compatibility (
                provider_id INTEGER PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                level TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                runtime_version TEXT NOT NULL DEFAULT '',
                runtime_source TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS smoke_capabilities (
                token_hash TEXT PRIMARY KEY,
                marker TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                call_count INTEGER NOT NULL DEFAULT 0,
                last_argument TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_codex_smoke_expiry
                ON smoke_capabilities(expires_at);
            """
        )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["provider_id"] = int(data["provider_id"])
        data["latency_ms"] = int(data.get("latency_ms") or 0)
        data["level"] = normalize_level(str(data.get("level") or "none"))
        data["evidence"] = _parse_json(data.pop("evidence_json", "{}"), {})
        return data

    def get(self, provider_id: int) -> dict[str, Any] | None:
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM compatibility WHERE provider_id=?",
                (int(provider_id),),
            ).fetchone()
        return self._row(row)

    def record(
        self,
        provider_id: int,
        *,
        fingerprint: str,
        level: str,
        runtime_version: str,
        runtime_source: str,
        model: str,
        base_url: str,
        latency_ms: int,
        evidence: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        normalized = normalize_level(level)
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO compatibility(
                    provider_id,fingerprint,level,checked_at,latency_ms,runtime_version,
                    runtime_source,model,base_url,evidence_json,error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    level=excluded.level,
                    checked_at=excluded.checked_at,
                    latency_ms=excluded.latency_ms,
                    runtime_version=excluded.runtime_version,
                    runtime_source=excluded.runtime_source,
                    model=excluded.model,
                    base_url=excluded.base_url,
                    evidence_json=excluded.evidence_json,
                    error=excluded.error
                """,
                (
                    int(provider_id),
                    str(fingerprint),
                    normalized,
                    _now(),
                    max(0, int(latency_ms)),
                    str(runtime_version)[:160],
                    str(runtime_source)[:80],
                    str(model)[:240],
                    str(base_url)[:1200],
                    _json(evidence or {}),
                    str(error)[:4000],
                ),
            )
        return self.get(int(provider_id)) or {}

    def delete(self, provider_id: int) -> None:
        with self.db() as conn:
            conn.execute("DELETE FROM compatibility WHERE provider_id=?", (int(provider_id),))

    def evaluate(
        self,
        provider: dict[str, Any],
        runtime: Any,
        *,
        required_level: str = "full",
        max_age_hours: int = COMPATIBILITY_MAX_AGE_HOURS,
    ) -> dict[str, Any]:
        provider_id = int(provider.get("id") or 0)
        record = self.get(provider_id)
        expected = provider_runtime_fingerprint(provider, runtime)
        result: dict[str, Any] = {
            "provider_id": provider_id,
            "valid": False,
            "level": "none",
            "required_level": normalize_level(required_level),
            "fingerprint_current": expected,
            "reason": "尚未执行真实 Codex Provider smoke",
            "record": record,
        }
        if record is None:
            return result
        result["level"] = normalize_level(str(record.get("level") or "none"))
        if str(record.get("fingerprint") or "") != expected:
            result["reason"] = "Provider、API Key、模型、Runtime 或治理配置已变化，旧 Codex smoke 已失效"
            return result
        try:
            checked = datetime.fromisoformat(str(record.get("checked_at") or ""))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=UTC)
        except ValueError:
            result["reason"] = "Codex smoke 时间记录无效"
            return result
        age = _now_dt() - checked.astimezone(UTC)
        result["age_hours"] = max(0.0, age.total_seconds() / 3600.0)
        if age > timedelta(hours=max(1, int(max_age_hours))):
            result["reason"] = f"Codex smoke 已超过 {max(1, int(max_age_hours))} 小时，需要重新验证"
            return result
        if not level_at_least(result["level"], result["required_level"]):
            result["reason"] = (
                f"当前 Codex compatibility={result['level']}，生产 rollout 要求 "
                f"{result['required_level']}"
            )
            return result
        if str(record.get("error") or ""):
            result["reason"] = "最近一次 Codex smoke 未完整通过，需要重新验证"
            return result
        result["valid"] = True
        result["reason"] = "fresh full Codex compatibility smoke verified"
        return result

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    def issue_smoke_capability(self, marker: str, *, lifetime_seconds: int = 600) -> str:
        token = secrets.token_urlsafe(32)
        now = _now_dt()
        expires = now + timedelta(seconds=max(30, min(1800, int(lifetime_seconds))))
        with self.db() as conn:
            conn.execute("DELETE FROM smoke_capabilities WHERE expires_at<=?", (_now(),))
            conn.execute(
                """
                INSERT INTO smoke_capabilities(token_hash,marker,created_at,expires_at,call_count,last_argument)
                VALUES(?,?,?,?,0,'')
                """,
                (self._token_hash(token), str(marker)[:500], now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
            )
        return token

    def smoke_capability(self, token: str) -> dict[str, Any] | None:
        token_hash = self._token_hash(token)
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM smoke_capabilities WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
            except ValueError:
                conn.execute("DELETE FROM smoke_capabilities WHERE token_hash=?", (token_hash,))
                return None
            if expires.astimezone(UTC) <= _now_dt():
                conn.execute("DELETE FROM smoke_capabilities WHERE token_hash=?", (token_hash,))
                return None
            return {
                "marker": str(row["marker"]),
                "call_count": int(row["call_count"] or 0),
                "last_argument": str(row["last_argument"] or ""),
                "expires_at": str(row["expires_at"]),
            }

    def record_smoke_tool_call(self, token: str, argument: str) -> dict[str, Any] | None:
        current = self.smoke_capability(token)
        if current is None:
            return None
        token_hash = self._token_hash(token)
        with self.db() as conn:
            conn.execute(
                "UPDATE smoke_capabilities SET call_count=call_count+1,last_argument=? WHERE token_hash=?",
                (str(argument)[:1000], token_hash),
            )
        return self.smoke_capability(token)

    def revoke_smoke_capability(self, token: str) -> None:
        with self.db() as conn:
            conn.execute("DELETE FROM smoke_capabilities WHERE token_hash=?", (self._token_hash(token),))


@lru_cache(maxsize=1)
def codex_provider_compatibility_store() -> CodexProviderCompatibilityStore:
    return CodexProviderCompatibilityStore()
