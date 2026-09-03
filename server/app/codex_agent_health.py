from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

import httpx

from app.codex_app_server import CodexAppServerClient
from app.codex_engine import (
    _codex_home,
    _launch_args,
    _safe_process_env,
    resolve_codex_runtime,
    select_codex_provider_from,
)
from app.codex_process_isolation import codex_process_isolation_status
from app.codex_provider_compatibility import (
    COMPATIBILITY_MAX_AGE_HOURS,
    codex_provider_compatibility_store,
    level_at_least,
)
from app.codex_provider_rollout import rollout_selection
from app.config import SERVER_DIR, fresh_settings
from app.provider_manager import api_roots, provider_store

DB_PATH = SERVER_DIR / "data" / "codex-agent-health.db"
MONITOR_INTERVAL_SECONDS = 60
HOST_HANDSHAKE_INTERVAL_SECONDS = 300
HISTORY_RETENTION_DAYS = 7
LEASE_TTL_SECONDS = 95
SMOKE_WARNING_HOURS = 24.0

_MONITOR_HOLDER = f"{os.getpid()}:{uuid.uuid4().hex}"
_monitor_task: asyncio.Task[None] | None = None


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _safe_error(value: Any, *secrets: str) -> str:
    text = str(value or "").strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text[:700]


class CodexAgentHealthStore:
    def __init__(self, path: Path = DB_PATH) -> None:
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
            CREATE TABLE IF NOT EXISTS latest (
                id INTEGER PRIMARY KEY CHECK(id=1),
                checked_at TEXT NOT NULL,
                state TEXT NOT NULL,
                code TEXT NOT NULL,
                reason TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT NOT NULL,
                state TEXT NOT NULL,
                code TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_codex_agent_health_history_time
                ON history(checked_at DESC);
            CREATE TABLE IF NOT EXISTS provider_live (
                provider_id INTEGER PRIMARY KEY,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'unknown',
                status_code INTEGER,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS monitor_lease (
                name TEXT PRIMARY KEY,
                holder TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )

    def save(self, payload: dict[str, Any]) -> None:
        checked_at = str(payload.get("checked_at") or _now())
        state = str(payload.get("state") or "UNKNOWN")[:40]
        code = str(payload.get("code") or "UNKNOWN")[:80]
        reason = str(payload.get("reason") or "")[:1200]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        cutoff = (_now_dt() - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat(timespec="seconds")
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO latest(id,checked_at,state,code,reason,payload_json)
                VALUES(1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    checked_at=excluded.checked_at,
                    state=excluded.state,
                    code=excluded.code,
                    reason=excluded.reason,
                    payload_json=excluded.payload_json
                """,
                (checked_at, state, code, reason, encoded),
            )
            conn.execute(
                "INSERT INTO history(checked_at,state,code,reason) VALUES(?,?,?,?)",
                (checked_at, state, code, reason),
            )
            conn.execute("DELETE FROM history WHERE checked_at<?", (cutoff,))
            conn.execute(
                "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 20000)"
            )

    def latest(self) -> dict[str, Any] | None:
        with self.db() as conn:
            row = conn.execute("SELECT payload_json FROM latest WHERE id=1").fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.db() as conn:
            rows = conn.execute(
                "SELECT checked_at,state,code,reason FROM history ORDER BY id DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_provider_live(
        self,
        provider_id: int,
        *,
        state: str,
        status_code: int | None,
        latency_ms: int,
        healthy: bool,
    ) -> int:
        with self.db() as conn:
            row = conn.execute(
                "SELECT consecutive_failures FROM provider_live WHERE provider_id=?",
                (int(provider_id),),
            ).fetchone()
            previous = int(row["consecutive_failures"] or 0) if row else 0
            failures = 0 if healthy else previous + 1
            conn.execute(
                """
                INSERT INTO provider_live(
                    provider_id,consecutive_failures,state,status_code,latency_ms,checked_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    consecutive_failures=excluded.consecutive_failures,
                    state=excluded.state,
                    status_code=excluded.status_code,
                    latency_ms=excluded.latency_ms,
                    checked_at=excluded.checked_at
                """,
                (
                    int(provider_id),
                    failures,
                    str(state)[:40],
                    int(status_code) if status_code is not None else None,
                    max(0, int(latency_ms)),
                    _now(),
                ),
            )
        return failures

    def try_acquire_lease(self, holder: str, *, ttl_seconds: int = LEASE_TTL_SECONDS) -> bool:
        now = _now_dt()
        expires = now + timedelta(seconds=max(30, int(ttl_seconds)))
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT holder,expires_at FROM monitor_lease WHERE name='background'"
            ).fetchone()
            if row is not None:
                current_expiry = _parse_time(row["expires_at"])
                current_holder = str(row["holder"] or "")
                if current_holder != holder and current_expiry is not None and current_expiry > now:
                    return False
            conn.execute(
                """
                INSERT INTO monitor_lease(name,holder,expires_at) VALUES('background',?,?)
                ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at
                """,
                (holder, expires.isoformat(timespec="seconds")),
            )
        return True

    def release_lease(self, holder: str) -> None:
        with self.db() as conn:
            conn.execute(
                "DELETE FROM monitor_lease WHERE name='background' AND holder=?",
                (holder,),
            )


def codex_agent_health_store() -> CodexAgentHealthStore:
    return CodexAgentHealthStore()


def _compatibility_code(status: dict[str, Any]) -> str:
    if bool(status.get("valid")):
        return "READY"
    record = status.get("record") if isinstance(status.get("record"), dict) else None
    if record is None:
        return "SMOKE_MISSING"
    if str(record.get("fingerprint") or "") != str(status.get("fingerprint_current") or ""):
        return "FINGERPRINT_MISMATCH"
    age_hours = status.get("age_hours")
    if isinstance(age_hours, (int, float)) and float(age_hours) > float(COMPATIBILITY_MAX_AGE_HOURS):
        return "SMOKE_EXPIRED"
    if not level_at_least(str(status.get("level") or "none"), str(status.get("required_level") or "full")):
        return "COMPATIBILITY_INSUFFICIENT"
    if str(record.get("error") or ""):
        return "SMOKE_FAILED"
    return "PROVIDER_NOT_READY"


def _compatibility_snapshot(runtime: Any | None) -> tuple[Any | None, list[dict[str, Any]]]:
    selected = None
    if runtime is not None:
        try:
            selected = rollout_selection(runtime).get("provider")
        except Exception:
            selected = None
    compatibility = codex_provider_compatibility_store()
    rows: list[dict[str, Any]] = []
    for provider in provider_store().list(enabled_only=True, include_secret=True):
        provider_id = int(provider.get("id") or 0)
        name = str(provider.get("name") or f"Provider {provider_id}")
        spec = select_codex_provider_from([provider])
        if spec is None:
            rows.append(
                {
                    "provider_id": provider_id,
                    "provider_name": name,
                    "model": "",
                    "eligible": False,
                    "selected": False,
                    "level": "none",
                    "code": "PROVIDER_CONFIG_INVALID",
                    "reason": "未完整配置 Responses 协议、API Key、Base URL 或文本模型",
                    "age_hours": None,
                    "remaining_hours": None,
                }
            )
            continue
        if runtime is None:
            rows.append(
                {
                    "provider_id": spec.provider_id,
                    "provider_name": spec.name,
                    "model": spec.model,
                    "eligible": False,
                    "selected": False,
                    "level": "none",
                    "code": "RUNTIME_UNAVAILABLE",
                    "reason": "Codex Runtime 不可用，无法验证 Provider compatibility",
                    "age_hours": None,
                    "remaining_hours": None,
                }
            )
            continue
        status = compatibility.evaluate(
            provider,
            runtime,
            required_level="full",
            max_age_hours=COMPATIBILITY_MAX_AGE_HOURS,
        )
        age = status.get("age_hours")
        remaining = None
        if isinstance(age, (int, float)):
            remaining = max(0.0, float(COMPATIBILITY_MAX_AGE_HOURS) - float(age))
        rows.append(
            {
                "provider_id": spec.provider_id,
                "provider_name": spec.name,
                "model": spec.model,
                "eligible": bool(status.get("valid")),
                "selected": bool(selected and int(selected.provider_id) == int(spec.provider_id)),
                "level": str(status.get("level") or "none"),
                "code": _compatibility_code(status),
                "reason": str(status.get("reason") or "")[:1000],
                "age_hours": round(float(age), 2) if isinstance(age, (int, float)) else None,
                "remaining_hours": round(float(remaining), 2) if remaining is not None else None,
            }
        )
    return selected, rows


async def _probe_provider_live(provider: dict[str, Any], store: CodexAgentHealthStore) -> dict[str, Any]:
    spec = select_codex_provider_from([provider])
    provider_id = int(provider.get("id") or 0)
    provider_name = str(provider.get("name") or f"Provider {provider_id}")
    if spec is None:
        return {
            "provider_id": provider_id,
            "provider_name": provider_name,
            "model": "",
            "state": "config_invalid",
            "status_code": None,
            "latency_ms": 0,
            "consecutive_failures": 0,
            "error": "Responses/API Key/Base URL/text model 配置不完整",
        }

    timeout = min(5.0, max(1.0, float(provider.get("timeout_seconds") or 5.0)))
    started = perf_counter()
    state = "unreachable"
    status_code: int | None = None
    error = ""
    for root in api_roots(spec.base_url):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    root.rstrip("/") + "/models",
                    headers={
                        "Authorization": f"Bearer {spec.api_key}",
                        "Accept": "application/json",
                    },
                )
            status_code = int(response.status_code)
            if 200 <= status_code < 400:
                state = "ok"
            elif status_code in {401, 403}:
                state = "auth_error"
            elif status_code == 429:
                state = "rate_limited"
            elif status_code >= 500:
                state = "upstream_error"
            else:
                # A 404/405 still proves DNS/TLS/HTTP reachability. Full compatibility remains
                # authoritative for Codex semantics; this lightweight monitor is not a smoke test.
                state = "reachable"
            error = "" if state in {"ok", "reachable"} else f"HTTP {status_code}"
            if state not in {"upstream_error"}:
                break
        except (httpx.HTTPError, ValueError) as exc:
            state = "unreachable"
            error = _safe_error(exc, spec.api_key)
    latency_ms = int((perf_counter() - started) * 1000)
    healthy = state in {"ok", "reachable"}
    failures = await asyncio.to_thread(
        store.record_provider_live,
        provider_id,
        state=state,
        status_code=status_code,
        latency_ms=latency_ms,
        healthy=healthy,
    )
    return {
        "provider_id": provider_id,
        "provider_name": provider_name,
        "model": spec.model,
        "state": state,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "consecutive_failures": failures,
        "error": error,
    }


def _host_probe_due(previous: dict[str, Any] | None, runtime: Any, provider: Any) -> bool:
    if not previous:
        return True
    host = previous.get("host") if isinstance(previous.get("host"), dict) else {}
    checked = _parse_time(host.get("checked_at"))
    if checked is None:
        return True
    if str(host.get("runtime_version") or "") != str(getattr(runtime, "version", "") or ""):
        return True
    if int(host.get("provider_id") or 0) != int(getattr(provider, "provider_id", 0) or 0):
        return True
    return _now_dt() >= checked + timedelta(seconds=HOST_HANDSHAKE_INTERVAL_SECONDS)


async def _probe_host(runtime: Any, provider: Any) -> dict[str, Any]:
    settings = fresh_settings()
    workspace = SERVER_DIR / "data" / "codex-health" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    codex_home = await asyncio.to_thread(_codex_home, "__health_monitor__")
    started = perf_counter()
    client: CodexAppServerClient | None = None
    try:
        client = CodexAppServerClient(
            _launch_args(runtime.path, provider),
            env=_safe_process_env(codex_home, provider.api_key),
            cwd=workspace,
            client_version=settings.app_version,
            request_timeout=10.0,
            experimental_api=True,
        )
        async with client:
            initialize = dict(client.initialize_result)
            unit = client.process_unit
        return {
            "state": "ok",
            "code": "READY",
            "reason": "official codex app-server initialize/initialized handshake completed",
            "checked_at": _now(),
            "latency_ms": int((perf_counter() - started) * 1000),
            "runtime_version": str(runtime.version),
            "provider_id": int(provider.provider_id),
            "process_unit": unit,
            "server_info_present": bool(initialize),
        }
    except Exception as exc:
        return {
            "state": "failed",
            "code": "HOST_UNAVAILABLE",
            "reason": _safe_error(exc, str(getattr(provider, "api_key", ""))),
            "checked_at": _now(),
            "latency_ms": int((perf_counter() - started) * 1000),
            "runtime_version": str(getattr(runtime, "version", "") or ""),
            "provider_id": int(getattr(provider, "provider_id", 0) or 0),
            "process_unit": client.process_unit if client is not None else "",
            "server_info_present": False,
        }


def _blocking_reason(compatibility: list[dict[str, Any]]) -> tuple[str, str]:
    configured = [row for row in compatibility if int(row.get("provider_id") or 0) > 0]
    if not configured:
        return "PROVIDER_CONFIG_INVALID", "没有已启用的 Codex Responses Provider"
    first = configured[0]
    return str(first.get("code") or "PROVIDER_NOT_READY"), str(first.get("reason") or "Provider 未就绪")


async def run_codex_agent_health_check(*, force_host: bool = False) -> dict[str, Any]:
    store = codex_agent_health_store()
    previous = await asyncio.to_thread(store.latest)
    started = perf_counter()
    checked_at = _now()

    runtime = None
    runtime_error = ""
    runtime_started = perf_counter()
    try:
        runtime = await asyncio.to_thread(resolve_codex_runtime)
    except Exception as exc:
        runtime_error = _safe_error(exc)
    runtime_snapshot = {
        "state": "ok" if runtime is not None else "failed",
        "code": "READY" if runtime is not None else "RUNTIME_UNAVAILABLE",
        "reason": "" if runtime is not None else runtime_error,
        "version": str(getattr(runtime, "version", "") or ""),
        "source": str(getattr(runtime, "source", "") or ""),
        "path": str(getattr(runtime, "path", "") or ""),
        "latency_ms": int((perf_counter() - runtime_started) * 1000),
    }

    isolation = await asyncio.to_thread(codex_process_isolation_status)
    selected, compatibility = await asyncio.to_thread(_compatibility_snapshot, runtime)

    providers = provider_store().list(enabled_only=True, include_secret=True)
    live_results = await asyncio.gather(
        *(_probe_provider_live(provider, store) for provider in providers),
        return_exceptions=True,
    )
    live: list[dict[str, Any]] = []
    for provider, result in zip(providers, live_results):
        if isinstance(result, Exception):
            live.append(
                {
                    "provider_id": int(provider.get("id") or 0),
                    "provider_name": str(provider.get("name") or "Provider"),
                    "model": "",
                    "state": "unreachable",
                    "status_code": None,
                    "latency_ms": 0,
                    "consecutive_failures": 1,
                    "error": _safe_error(result, str(provider.get("api_key") or "")),
                }
            )
        else:
            live.append(result)

    host: dict[str, Any]
    if runtime is None:
        host = {
            "state": "blocked",
            "code": "RUNTIME_UNAVAILABLE",
            "reason": runtime_error or "Codex Runtime 不可用",
            "checked_at": checked_at,
            "latency_ms": 0,
        }
    elif not bool(isolation.get("enforced")):
        host = {
            "state": "blocked",
            "code": "PROCESS_ISOLATION_UNAVAILABLE",
            "reason": str(isolation.get("reason") or "Phase 7.32 process isolation 未生效"),
            "checked_at": checked_at,
            "latency_ms": 0,
        }
    elif selected is None:
        code, reason = _blocking_reason(compatibility)
        host = {
            "state": "blocked",
            "code": code,
            "reason": reason,
            "checked_at": checked_at,
            "latency_ms": 0,
        }
    elif force_host or _host_probe_due(previous, runtime, selected):
        host = await _probe_host(runtime, selected)
    else:
        previous_host = previous.get("host") if isinstance(previous, dict) else None
        host = dict(previous_host) if isinstance(previous_host, dict) else await _probe_host(runtime, selected)

    state = "READY"
    code = "READY"
    reason = "Runtime、Host、process isolation、Provider 与 fresh-full compatibility 链路正常"
    settings = fresh_settings()
    if not bool(settings.fdex_agent_enabled):
        state = "DISABLED"
        code = "AGENT_DISABLED"
        reason = "Coding Agent 当前已关闭；健康监控仍持续检测 Codex 链路"
    elif runtime is None:
        state = "BLOCKED"
        code = "RUNTIME_UNAVAILABLE"
        reason = runtime_error or "Codex Runtime 不可用"
    elif bool(isolation.get("required", True)) and not bool(isolation.get("enforced")):
        state = "BLOCKED"
        code = "PROCESS_ISOLATION_UNAVAILABLE"
        reason = str(isolation.get("reason") or "Phase 7.32 process isolation 未生效")
    elif selected is None:
        state = "BLOCKED"
        code, reason = _blocking_reason(compatibility)
    else:
        selected_live = next(
            (row for row in live if int(row.get("provider_id") or 0) == int(selected.provider_id)),
            None,
        )
        selected_compat = next(
            (row for row in compatibility if int(row.get("provider_id") or 0) == int(selected.provider_id)),
            None,
        )
        if selected_live and str(selected_live.get("state")) == "auth_error":
            state = "BLOCKED"
            code = "PROVIDER_AUTH_FAILED"
            reason = f"{selected.name} 实时鉴权失败（HTTP {selected_live.get('status_code') or '401/403'}）"
        elif str(host.get("state") or "") == "failed":
            state = "DEGRADED"
            code = "HOST_UNAVAILABLE"
            reason = str(host.get("reason") or "Codex app-server handshake 失败")
        elif selected_live and str(selected_live.get("state")) == "rate_limited":
            state = "DEGRADED"
            code = "PROVIDER_RATE_LIMITED"
            reason = f"{selected.name} 当前返回 429；full compatibility 仍有效，但实时链路受限"
        elif selected_live and str(selected_live.get("state")) in {"unreachable", "upstream_error"} and int(selected_live.get("consecutive_failures") or 0) >= 3:
            state = "DEGRADED"
            code = "PROVIDER_UNREACHABLE"
            reason = f"{selected.name} 已连续 {selected_live.get('consecutive_failures')} 次实时链路检测失败"
        elif selected_compat and isinstance(selected_compat.get("remaining_hours"), (int, float)) and float(selected_compat["remaining_hours"]) <= SMOKE_WARNING_HOURS:
            state = "DEGRADED"
            code = "SMOKE_EXPIRING"
            reason = f"{selected.name} 的 full-smoke 将在约 {selected_compat['remaining_hours']:.1f} 小时后过期"

    snapshot = {
        "state": state,
        "code": code,
        "reason": reason[:1200],
        "checked_at": checked_at,
        "duration_ms": int((perf_counter() - started) * 1000),
        "monitor": {
            "background_interval_seconds": MONITOR_INTERVAL_SECONDS,
            "host_handshake_interval_seconds": HOST_HANDSHAKE_INTERVAL_SECONDS,
            "ui_poll_seconds": 5,
            "history_retention_days": HISTORY_RETENTION_DAYS,
            "leader_lease_seconds": LEASE_TTL_SECONDS,
        },
        "runtime": runtime_snapshot,
        "host": host,
        "isolation": {
            "state": "ok" if bool(isolation.get("enforced")) else "failed",
            "code": "READY" if bool(isolation.get("enforced")) else "PROCESS_ISOLATION_UNAVAILABLE",
            "enforced": bool(isolation.get("enforced")),
            "required": bool(isolation.get("required", True)),
            "reason": str(isolation.get("reason") or "")[:1000],
            "parent_unit": str(isolation.get("parent_unit") or ""),
            "controllers": list(isolation.get("controllers") or []),
            "memory_mb": isolation.get("memory_mb"),
            "cpu_percent": isolation.get("cpu_percent"),
            "pids_max": isolation.get("pids_max"),
        },
        "selected_provider": {
            "provider_id": int(getattr(selected, "provider_id", 0) or 0),
            "provider_name": str(getattr(selected, "name", "") or ""),
            "model": str(getattr(selected, "model", "") or ""),
        } if selected is not None else None,
        "providers": live,
        "compatibility": compatibility,
    }
    await asyncio.to_thread(store.save, snapshot)
    snapshot["history"] = await asyncio.to_thread(store.history, 30)
    return snapshot


def codex_agent_health_snapshot() -> dict[str, Any]:
    store = codex_agent_health_store()
    payload = store.latest()
    if payload is None:
        payload = {
            "state": "UNKNOWN",
            "code": "MONITOR_STARTING",
            "reason": "健康监控尚未完成第一次链路检测",
            "checked_at": "",
            "duration_ms": 0,
            "monitor": {
                "background_interval_seconds": MONITOR_INTERVAL_SECONDS,
                "host_handshake_interval_seconds": HOST_HANDSHAKE_INTERVAL_SECONDS,
                "ui_poll_seconds": 5,
                "history_retention_days": HISTORY_RETENTION_DAYS,
                "leader_lease_seconds": LEASE_TTL_SECONDS,
            },
            "runtime": {},
            "host": {},
            "isolation": {},
            "selected_provider": None,
            "providers": [],
            "compatibility": [],
        }
    payload = dict(payload)
    payload["history"] = store.history(30)
    return payload


async def _monitor_loop() -> None:
    store = codex_agent_health_store()
    first = True
    while True:
        try:
            leader = await asyncio.to_thread(store.try_acquire_lease, _MONITOR_HOLDER)
            if leader:
                try:
                    await run_codex_agent_health_check(force_host=first)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failure = {
                        "state": "UNKNOWN",
                        "code": "MONITOR_ERROR",
                        "reason": _safe_error(exc),
                        "checked_at": _now(),
                        "duration_ms": 0,
                        "monitor": {
                            "background_interval_seconds": MONITOR_INTERVAL_SECONDS,
                            "host_handshake_interval_seconds": HOST_HANDSHAKE_INTERVAL_SECONDS,
                            "ui_poll_seconds": 5,
                            "history_retention_days": HISTORY_RETENTION_DAYS,
                            "leader_lease_seconds": LEASE_TTL_SECONDS,
                        },
                        "runtime": {},
                        "host": {},
                        "isolation": {},
                        "selected_provider": None,
                        "providers": [],
                        "compatibility": [],
                    }
                    await asyncio.to_thread(store.save, failure)
                first = False
            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


async def start_codex_agent_health_monitor() -> None:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor_loop(), name="fdex-codex-agent-health")


async def stop_codex_agent_health_monitor() -> None:
    global _monitor_task
    task = _monitor_task
    _monitor_task = None
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await asyncio.to_thread(codex_agent_health_store().release_lease, _MONITOR_HOLDER)
