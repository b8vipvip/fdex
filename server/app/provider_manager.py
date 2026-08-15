from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, Iterator

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings

DEFAULT_PROTOCOLS = ["chat", "responses", "legacy"]
AUDIO_PROTOCOLS = {"auto", "chat_audio", "speech", "realtime"}
AUDIO_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "ai-providers.db"
KEY_PATH = _DATA_DIR / "ai-providers.key"


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    for suffix in (
        "/chat/completions",
        "/images/generations",
        "/audio/speech",
        "/responses",
        "/completions",
    ):
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def mask_key(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _normalize_audio_protocol(value: Any) -> str:
    protocol = str(value or "auto").strip().lower()
    return protocol if protocol in AUDIO_PROTOCOLS else "auto"


def _normalize_audio_format(value: Any) -> str:
    audio_format = str(value or "wav").strip().lower()
    return audio_format if audio_format in AUDIO_FORMATS else "wav"


class ProviderStore:
    def __init__(self, db_path: Path = DB_PATH, key_path: Path = KEY_PATH):
        self.db_path = db_path.resolve()
        self.key_path = key_path.resolve()
        self._fernet: Fernet | None = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self._cipher()
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS providers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key_cipher TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 100,
                    main_text_model TEXT NOT NULL DEFAULT '',
                    backup_text_models_json TEXT NOT NULL DEFAULT '[]',
                    main_vision_model TEXT NOT NULL DEFAULT '',
                    backup_vision_models_json TEXT NOT NULL DEFAULT '[]',
                    main_image_model TEXT NOT NULL DEFAULT '',
                    backup_image_models_json TEXT NOT NULL DEFAULT '[]',
                    main_audio_model TEXT NOT NULL DEFAULT '',
                    backup_audio_models_json TEXT NOT NULL DEFAULT '[]',
                    audio_protocol TEXT NOT NULL DEFAULT 'auto',
                    audio_voice TEXT NOT NULL DEFAULT 'alloy',
                    audio_format TEXT NOT NULL DEFAULT 'wav',
                    protocol_order_json TEXT NOT NULL DEFAULT '["chat","responses","legacy"]',
                    model_capabilities_json TEXT NOT NULL DEFAULT '{}',
                    timeout_seconds INTEGER NOT NULL DEFAULT 60,
                    auto_test_enabled INTEGER NOT NULL DEFAULT 0,
                    auto_test_interval_hours INTEGER NOT NULL DEFAULT 12,
                    last_test_at TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '未测试',
                    last_latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate_columns(conn)
            conn.commit()
        self._migrate_legacy_env()

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(providers)").fetchall()}
        additions = {
            "main_vision_model": "TEXT NOT NULL DEFAULT ''",
            "backup_vision_models_json": "TEXT NOT NULL DEFAULT '[]'",
            "main_image_model": "TEXT NOT NULL DEFAULT ''",
            "backup_image_models_json": "TEXT NOT NULL DEFAULT '[]'",
            "main_audio_model": "TEXT NOT NULL DEFAULT ''",
            "backup_audio_models_json": "TEXT NOT NULL DEFAULT '[]'",
            "audio_protocol": "TEXT NOT NULL DEFAULT 'auto'",
            "audio_voice": "TEXT NOT NULL DEFAULT 'alloy'",
            "audio_format": "TEXT NOT NULL DEFAULT 'wav'",
        }
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE providers ADD COLUMN {name} {ddl}")

    def _migrate_legacy_env(self) -> None:
        with self.db() as conn:
            if conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0] > 0:
                return
        settings = fresh_settings()
        if not settings.ai_base_url or not settings.ai_api_key or not settings.ai_model:
            return
        self.create(
            name="原 FDEX AI 接口",
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            enabled=True,
            priority=1,
            main_text_model=settings.ai_model,
            backup_text_models=[],
            main_vision_model="",
            backup_vision_models=[],
            main_image_model="",
            backup_image_models=[],
            main_audio_model="",
            backup_audio_models=[],
            audio_protocol="auto",
            audio_voice="alloy",
            audio_format="wav",
            protocol_order=DEFAULT_PROTOCOLS,
            timeout_seconds=int(settings.ai_timeout_seconds),
            auto_test_enabled=False,
            auto_test_interval_hours=12,
        )

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _cipher(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
        key = self.key_path.read_bytes().strip()
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"供应商密钥文件无效：{self.key_path}") from exc
        return self._fernet

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def _decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("供应商 API Key 无法解密，请确认 ai-providers.key 与数据库匹配。") from exc

    def _row(self, row: sqlite3.Row, include_secret: bool = False) -> dict[str, Any]:
        cipher = row["api_key_cipher"]
        api_key = self._decrypt(cipher) if include_secret else ""
        return {
            "id": row["id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "api_key": api_key,
            "api_key_masked": mask_key(self._decrypt(cipher)) if cipher else "",
            "api_key_configured": bool(cipher),
            "enabled": bool(row["enabled"]),
            "priority": row["priority"],
            "main_text_model": row["main_text_model"],
            "backup_text_models": _parse(row["backup_text_models_json"], []),
            "main_vision_model": row["main_vision_model"],
            "backup_vision_models": _parse(row["backup_vision_models_json"], []),
            "main_image_model": row["main_image_model"],
            "backup_image_models": _parse(row["backup_image_models_json"], []),
            "main_audio_model": row["main_audio_model"],
            "backup_audio_models": _parse(row["backup_audio_models_json"], []),
            "audio_protocol": _normalize_audio_protocol(row["audio_protocol"]),
            "audio_voice": row["audio_voice"] or "alloy",
            "audio_format": _normalize_audio_format(row["audio_format"]),
            "protocol_order": _parse(row["protocol_order_json"], DEFAULT_PROTOCOLS),
            "model_capabilities": _parse(row["model_capabilities_json"], {}),
            "timeout_seconds": row["timeout_seconds"],
            "auto_test_enabled": bool(row["auto_test_enabled"]),
            "auto_test_interval_hours": row["auto_test_interval_hours"],
            "last_test_at": row["last_test_at"],
            "last_status": row["last_status"],
            "last_latency_ms": row["last_latency_ms"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self, enabled_only: bool = False, include_secret: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM providers"
        values: list[Any] = []
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY priority ASC, id ASC"
        with self.db() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._row(row, include_secret=include_secret) for row in rows]

    def get(self, provider_id: int, include_secret: bool = False) -> dict[str, Any]:
        with self.db() as conn:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if row is None:
            raise KeyError(f"供应商 {provider_id} 不存在")
        return self._row(row, include_secret=include_secret)

    def create(self, **values: Any) -> dict[str, Any]:
        now = _now()
        with self.db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO providers (
                    name, base_url, api_key_cipher, enabled, priority,
                    main_text_model, backup_text_models_json,
                    main_vision_model, backup_vision_models_json,
                    main_image_model, backup_image_models_json,
                    main_audio_model, backup_audio_models_json,
                    audio_protocol, audio_voice, audio_format,
                    protocol_order_json, model_capabilities_json,
                    timeout_seconds, auto_test_enabled, auto_test_interval_hours,
                    last_test_at, last_status, last_latency_ms, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(values.get("name") or "").strip(),
                    normalize_base_url(str(values.get("base_url") or "")),
                    self._encrypt(str(values.get("api_key") or "")),
                    1 if values.get("enabled", True) else 0,
                    max(1, int(values.get("priority") or 100)),
                    str(values.get("main_text_model") or "").strip(),
                    _json(values.get("backup_text_models") or []),
                    str(values.get("main_vision_model") or "").strip(),
                    _json(values.get("backup_vision_models") or []),
                    str(values.get("main_image_model") or "").strip(),
                    _json(values.get("backup_image_models") or []),
                    str(values.get("main_audio_model") or "").strip(),
                    _json(values.get("backup_audio_models") or []),
                    _normalize_audio_protocol(values.get("audio_protocol")),
                    str(values.get("audio_voice") or "alloy").strip(),
                    _normalize_audio_format(values.get("audio_format")),
                    _json(values.get("protocol_order") or DEFAULT_PROTOCOLS),
                    _json(values.get("model_capabilities") or {}),
                    max(5, min(int(values.get("timeout_seconds") or 60), 600)),
                    1 if values.get("auto_test_enabled") else 0,
                    max(1, min(int(values.get("auto_test_interval_hours") or 12), 720)),
                    "",
                    "未测试",
                    0,
                    now,
                    now,
                ),
            )
            provider_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(provider_id)

    def update(self, provider_id: int, **values: Any) -> dict[str, Any]:
        current = self.get(provider_id, include_secret=True)
        api_key = str(values.get("api_key") or "")
        api_key_cipher = self._encrypt(api_key) if api_key else self._encrypt(current["api_key"])
        with self.db() as conn:
            conn.execute(
                """
                UPDATE providers SET
                    name=?, base_url=?, api_key_cipher=?, enabled=?, priority=?,
                    main_text_model=?, backup_text_models_json=?,
                    main_vision_model=?, backup_vision_models_json=?,
                    main_image_model=?, backup_image_models_json=?,
                    main_audio_model=?, backup_audio_models_json=?,
                    audio_protocol=?, audio_voice=?, audio_format=?,
                    protocol_order_json=?, timeout_seconds=?,
                    auto_test_enabled=?, auto_test_interval_hours=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(values.get("name") or current["name"]).strip(),
                    normalize_base_url(str(values.get("base_url") or current["base_url"])),
                    api_key_cipher,
                    1 if values.get("enabled", current["enabled"]) else 0,
                    max(1, int(values.get("priority") or current["priority"])),
                    str(values.get("main_text_model", current["main_text_model"]) or "").strip(),
                    _json(values.get("backup_text_models", current["backup_text_models"])),
                    str(values.get("main_vision_model", current["main_vision_model"]) or "").strip(),
                    _json(values.get("backup_vision_models", current["backup_vision_models"])),
                    str(values.get("main_image_model", current["main_image_model"]) or "").strip(),
                    _json(values.get("backup_image_models", current["backup_image_models"])),
                    str(values.get("main_audio_model", current["main_audio_model"]) or "").strip(),
                    _json(values.get("backup_audio_models", current["backup_audio_models"])),
                    _normalize_audio_protocol(values.get("audio_protocol", current["audio_protocol"])),
                    str(values.get("audio_voice", current["audio_voice"]) or "alloy").strip(),
                    _normalize_audio_format(values.get("audio_format", current["audio_format"])),
                    _json(values.get("protocol_order", current["protocol_order"])),
                    max(5, min(int(values.get("timeout_seconds") or current["timeout_seconds"]), 600)),
                    1 if values.get("auto_test_enabled", current["auto_test_enabled"]) else 0,
                    max(1, min(int(values.get("auto_test_interval_hours") or current["auto_test_interval_hours"]), 720)),
                    _now(),
                    provider_id,
                ),
            )
            conn.commit()
        return self.get(provider_id)

    def clear_key(self, provider_id: int) -> None:
        with self.db() as conn:
            conn.execute(
                "UPDATE providers SET api_key_cipher='', updated_at=? WHERE id=?",
                (_now(), provider_id),
            )
            conn.commit()

    def delete(self, provider_id: int) -> None:
        with self.db() as conn:
            conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
            conn.commit()

    def update_probe(
        self,
        provider_id: int,
        *,
        status: str,
        latency_ms: int,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        with self.db() as conn:
            conn.execute(
                """
                UPDATE providers
                SET last_test_at=?, last_status=?, last_latency_ms=?,
                    model_capabilities_json=COALESCE(?, model_capabilities_json), updated_at=?
                WHERE id=?
                """,
                (
                    _now(),
                    status[:200],
                    max(0, int(latency_ms)),
                    _json(capabilities) if capabilities is not None else None,
                    _now(),
                    provider_id,
                ),
            )
            conn.commit()

    def due_for_auto_test(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or _now_dt()
        due: list[dict[str, Any]] = []
        for item in self.list(enabled_only=True):
            if not item["auto_test_enabled"]:
                continue
            last = item["last_test_at"]
            if not last:
                due.append(item)
                continue
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            except ValueError:
                due.append(item)
                continue
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt >= timedelta(hours=item["auto_test_interval_hours"]):
                due.append(item)
        return due


@lru_cache(maxsize=1)
def provider_store() -> ProviderStore:
    store = ProviderStore()
    store.init()
    return store


def model_candidates(item: dict[str, Any], *, vision: bool = False) -> list[str]:
    if vision and (item.get("main_vision_model") or item.get("backup_vision_models")):
        values = [item.get("main_vision_model", ""), *(item.get("backup_vision_models") or [])]
    else:
        values = [item.get("main_text_model", ""), *(item.get("backup_text_models") or [])]
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def text_model_candidates(item: dict[str, Any], *, vision: bool = False) -> list[str]:
    return model_candidates(item, vision=vision)


def image_model_candidates(item: dict[str, Any]) -> list[str]:
    values = [item.get("main_image_model", ""), *(item.get("backup_image_models") or [])]
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def audio_model_candidates(item: dict[str, Any]) -> list[str]:
    values = [item.get("main_audio_model", ""), *(item.get("backup_audio_models") or [])]
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def provider_stats() -> dict[str, int]:
    items = provider_store().list()
    return {
        "total": len(items),
        "enabled": sum(1 for x in items if x["enabled"]),
        "healthy": sum(1 for x in items if x["last_status"].startswith("可用")),
        "auto": sum(1 for x in items if x["auto_test_enabled"]),
        "image": sum(1 for x in items if image_model_candidates(x)),
        "audio": sum(1 for x in items if audio_model_candidates(x)),
    }


def api_roots(base_url: str) -> list[str]:
    base = normalize_base_url(base_url)
    roots = [base]
    if not base.lower().endswith("/v1"):
        roots.append(base + "/v1")
    return list(dict.fromkeys(x.rstrip("/") for x in roots if x))


async def fetch_models(item: dict[str, Any]) -> tuple[list[str], int, list[str]]:
    errors: list[str] = []
    started = perf_counter()
    api_key = str(item.get("api_key") or "")
    if not api_key:
        return [], 0, ["API Key 未配置"]
    for root in api_roots(str(item.get("base_url") or "")):
        url = root + "/models"
        try:
            timeout = httpx.Timeout(float(item.get("timeout_seconds") or 60), connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                )
            if not response.is_success:
                errors.append(f"{url}: HTTP {response.status_code}")
                continue
            data = response.json()
            values = data.get("data") if isinstance(data, dict) else None
            if not isinstance(values, list):
                errors.append(f"{url}: 响应里没有 data[]")
                continue
            models = list(
                dict.fromkeys(
                    str(x.get("id") or "").strip()
                    for x in values
                    if isinstance(x, dict) and str(x.get("id") or "").strip()
                )
            )
            return models, int((perf_counter() - started) * 1000), errors
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{url}: {str(exc)[:160]}")
    return [], int((perf_counter() - started) * 1000), errors


async def _probe_chat(item: dict[str, Any], model: str) -> tuple[bool, int, str]:
    started = perf_counter()
    errors: list[str] = []
    api_key = str(item.get("api_key") or "")
    for root in api_roots(str(item.get("base_url") or "")):
        try:
            timeout = httpx.Timeout(float(item.get("timeout_seconds") or 60), connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.post(
                    root + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "请只回复 OK"}],
                        "stream": False,
                        "max_tokens": 16,
                    },
                )
            latency = int((perf_counter() - started) * 1000)
            if response.is_success:
                try:
                    data = response.json()
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if isinstance(choices, list) and choices:
                        return True, latency, "Chat Completions 可用"
                except ValueError:
                    pass
            errors.append(f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            errors.append(str(exc)[:120])
    return False, int((perf_counter() - started) * 1000), "; ".join(errors[-3:]) or "Chat 请求失败"


async def probe_provider(provider_id: int, mode: str = "ordinary") -> dict[str, Any]:
    store = provider_store()
    item = store.get(provider_id, include_secret=True)
    if not item["enabled"]:
        result = {"ok": False, "message": "供应商已停用", "latency_ms": 0, "models": []}
        store.update_probe(provider_id, status="失败：供应商已停用", latency_ms=0)
        return result
    candidates = model_candidates(item)
    if not candidates:
        result = {"ok": False, "message": "未配置文本模型", "latency_ms": 0, "models": []}
        store.update_probe(provider_id, status="失败：未配置文本模型", latency_ms=0)
        return result

    if mode == "ordinary":
        ok, latency, message = await _probe_chat(item, candidates[0])
        store.update_probe(
            provider_id,
            status=("可用" if ok else "失败") + f"：{message}",
            latency_ms=latency,
        )
        return {"ok": ok, "message": message, "latency_ms": latency, "models": [candidates[0]] if ok else []}

    discovered, discovery_ms, discovery_errors = await fetch_models(item)
    to_test = discovered[:20] if discovered else candidates[:20]
    capabilities: dict[str, Any] = {}
    usable: list[str] = []
    total_latency = discovery_ms
    for model in to_test:
        ok, latency, message = await _probe_chat(item, model)
        total_latency += latency
        capabilities[model] = {"chat": ok, "message": message, "latency_ms": latency}
        if ok:
            usable.append(model)
    if usable:
        store.update_probe(
            provider_id,
            status=f"可用：{len(usable)} 个文本模型",
            latency_ms=total_latency,
            capabilities=capabilities,
        )
        return {
            "ok": True,
            "message": f"发现并验证 {len(usable)} 个可用文本模型",
            "latency_ms": total_latency,
            "models": usable,
            "discovery_errors": discovery_errors,
            "capabilities": capabilities,
        }
    store.update_probe(
        provider_id,
        status="失败：没有通过深测的文本模型",
        latency_ms=total_latency,
        capabilities=capabilities,
    )
    return {
        "ok": False,
        "message": "没有通过深测的文本模型",
        "latency_ms": total_latency,
        "models": [],
        "discovery_errors": discovery_errors,
        "capabilities": capabilities,
    }
