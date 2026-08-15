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
                    last_test_at TEXT,
                    last_status TEXT NOT NULL DEFAULT '未测试',
                    last_latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fdex_provider_priority
                    ON providers(enabled, priority, id);
                """
            )
            self._ensure_multimodal_columns(conn)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _ensure_multimodal_columns(conn: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(providers)").fetchall()}
        additions = {
            "main_image_model": "TEXT NOT NULL DEFAULT ''",
            "backup_image_models_json": "TEXT NOT NULL DEFAULT '[]'",
            "main_audio_model": "TEXT NOT NULL DEFAULT ''",
            "backup_audio_models_json": "TEXT NOT NULL DEFAULT '[]'",
            "audio_protocol": "TEXT NOT NULL DEFAULT 'auto'",
            "audio_voice": "TEXT NOT NULL DEFAULT 'alloy'",
            "audio_format": "TEXT NOT NULL DEFAULT 'wav'",
        }
        for column, ddl in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE providers ADD COLUMN {column} {ddl}")

    def _cipher(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            tmp = self.key_path.with_suffix(".tmp")
            tmp.write_bytes(key + b"\n")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.key_path)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("AI 供应商密钥无法解密，请确认 server/data/ai-providers.key 未变化") from exc

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _models(values: Iterable[str] | None) -> list[str]:
        return list(dict.fromkeys(str(x).strip() for x in (values or []) if str(x).strip()))

    @staticmethod
    def _protocols(values: Iterable[str] | None) -> list[str]:
        allowed = {"chat", "responses", "legacy"}
        result = [str(x).strip() for x in (values or []) if str(x).strip() in allowed]
        return list(dict.fromkeys(result)) or DEFAULT_PROTOCOLS.copy()

    def _row(self, row: sqlite3.Row, include_secret: bool = False) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["auto_test_enabled"] = bool(data["auto_test_enabled"])
        data["backup_text_models"] = _parse(data.pop("backup_text_models_json"), [])
        data["backup_vision_models"] = _parse(data.pop("backup_vision_models_json"), [])
        data["backup_image_models"] = _parse(data.pop("backup_image_models_json"), [])
        data["backup_audio_models"] = _parse(data.pop("backup_audio_models_json"), [])
        data["protocol_order"] = _parse(data.pop("protocol_order_json"), DEFAULT_PROTOCOLS.copy())
        data["model_capabilities"] = _parse(data.pop("model_capabilities_json"), {})
        data["audio_protocol"] = _normalize_audio_protocol(data.get("audio_protocol"))
        data["audio_format"] = _normalize_audio_format(data.get("audio_format"))
        cipher = data.pop("api_key_cipher")
        plain = self.decrypt(cipher) if cipher else ""
        data["api_key_masked"] = mask_key(plain)
        data["api_key_configured"] = bool(plain)
        if include_secret:
            data["api_key"] = plain
        return data

    def list(self, *, enabled_only: bool = False, include_secret: bool = False) -> list[dict[str, Any]]:
        self.init()
        sql = "SELECT * FROM providers" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY priority,id"
        with self.db() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row(row, include_secret=include_secret) for row in rows]

    def get(self, provider_id: int, *, include_secret: bool = False) -> dict[str, Any]:
        self.init()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if row is None:
            raise KeyError("供应商不存在")
        return self._row(row, include_secret=include_secret)

    def create(self, **values: Any) -> dict[str, Any]:
        self.init()
        name = str(values.get("name") or "").strip()
        base_url = normalize_base_url(str(values.get("base_url") or ""))
        if not name or not base_url.startswith(("http://", "https://")):
            raise ValueError("供应商名称或 BaseUrl 无效")
        now = _now()
        columns = [
            "name", "base_url", "api_key_cipher", "enabled", "priority",
            "main_text_model", "backup_text_models_json",
            "main_vision_model", "backup_vision_models_json",
            "main_image_model", "backup_image_models_json",
            "main_audio_model", "backup_audio_models_json", "audio_protocol", "audio_voice", "audio_format",
            "protocol_order_json", "model_capabilities_json", "timeout_seconds",
            "auto_test_enabled", "auto_test_interval_hours", "created_at", "updated_at",
        ]
        params = (
            name,
            base_url,
            self.encrypt(str(values.get("api_key") or "").strip()),
            1 if values.get("enabled", True) else 0,
            max(1, int(values.get("priority") or 100)),
            str(values.get("main_text_model") or "").strip(),
            _json(self._models(values.get("backup_text_models"))),
            str(values.get("main_vision_model") or "").strip(),
            _json(self._models(values.get("backup_vision_models"))),
            str(values.get("main_image_model") or "").strip(),
            _json(self._models(values.get("backup_image_models"))),
            str(values.get("main_audio_model") or "").strip(),
            _json(self._models(values.get("backup_audio_models"))),
            _normalize_audio_protocol(values.get("audio_protocol")),
            str(values.get("audio_voice") or "alloy").strip() or "alloy",
            _normalize_audio_format(values.get("audio_format")),
            _json(self._protocols(values.get("protocol_order"))),
            "{}",
            max(5, min(600, int(values.get("timeout_seconds") or 60))),
            1 if values.get("auto_test_enabled", False) else 0,
            max(1, min(720, int(values.get("auto_test_interval_hours") or 12))),
            now,
            now,
        )
        placeholders = ",".join("?" for _ in columns)
        with self.db() as conn:
            cur = conn.execute(
                f"INSERT INTO providers({','.join(columns)}) VALUES({placeholders})",
                params,
            )
            provider_id = int(cur.lastrowid)
        return self.get(provider_id)

    def update(self, provider_id: int, **values: Any) -> dict[str, Any]:
        old = self.get(provider_id, include_secret=True)
        name = str(values.get("name", old["name"])).strip()
        base_url = normalize_base_url(str(values.get("base_url", old["base_url"])))
        if not name or not base_url.startswith(("http://", "https://")):
            raise ValueError("供应商名称或 BaseUrl 无效")
        incoming_key = str(values.get("api_key") or "").strip()
        key = incoming_key if incoming_key else old.get("api_key", "")
        with self.db() as conn:
            conn.execute(
                """UPDATE providers SET
                    name=?,base_url=?,api_key_cipher=?,enabled=?,priority=?,
                    main_text_model=?,backup_text_models_json=?,main_vision_model=?,backup_vision_models_json=?,
                    main_image_model=?,backup_image_models_json=?,main_audio_model=?,backup_audio_models_json=?,
                    audio_protocol=?,audio_voice=?,audio_format=?,protocol_order_json=?,timeout_seconds=?,
                    auto_test_enabled=?,auto_test_interval_hours=?,updated_at=?
                    WHERE id=?""",
                (
                    name,
                    base_url,
                    self.encrypt(key),
                    1 if values.get("enabled", old["enabled"]) else 0,
                    max(1, int(values.get("priority", old["priority"]))),
                    str(values.get("main_text_model", old["main_text_model"])).strip(),
                    _json(self._models(values.get("backup_text_models", old["backup_text_models"]))),
                    str(values.get("main_vision_model", old["main_vision_model"])).strip(),
                    _json(self._models(values.get("backup_vision_models", old["backup_vision_models"]))),
                    str(values.get("main_image_model", old["main_image_model"])).strip(),
                    _json(self._models(values.get("backup_image_models", old["backup_image_models"]))),
                    str(values.get("main_audio_model", old["main_audio_model"])).strip(),
                    _json(self._models(values.get("backup_audio_models", old["backup_audio_models"]))),
                    _normalize_audio_protocol(values.get("audio_protocol", old["audio_protocol"])),
                    str(values.get("audio_voice", old["audio_voice"]) or "alloy").strip() or "alloy",
                    _normalize_audio_format(values.get("audio_format", old["audio_format"])),
                    _json(self._protocols(values.get("protocol_order", old["protocol_order"]))),
                    max(5, min(600, int(values.get("timeout_seconds", old["timeout_seconds"])))),
                    1 if values.get("auto_test_enabled", old["auto_test_enabled"]) else 0,
                    max(1, min(720, int(values.get("auto_test_interval_hours", old["auto_test_interval_hours"])))),
                    _now(),
                    provider_id,
                ),
            )
        return self.get(provider_id)

    def clear_key(self, provider_id: int) -> None:
        self.get(provider_id)
        with self.db() as conn:
            conn.execute("UPDATE providers SET api_key_cipher='',updated_at=? WHERE id=?", (_now(), provider_id))

    def delete(self, provider_id: int) -> None:
        self.get(provider_id)
        with self.db() as conn:
            conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))

    def record_probe(
        self,
        provider_id: int,
        *,
        status: str,
        latency_ms: int,
        capabilities: dict[str, Any] | None = None,
        main_model: str | None = None,
        backups: Iterable[str] | None = None,
        protocols: Iterable[str] | None = None,
    ) -> None:
        old = self.get(provider_id)
        with self.db() as conn:
            conn.execute(
                """UPDATE providers SET last_status=?,last_latency_ms=?,last_test_at=?,model_capabilities_json=?,
                    main_text_model=?,backup_text_models_json=?,protocol_order_json=?,updated_at=? WHERE id=?""",
                (
                    status,
                    int(latency_ms),
                    _now(),
                    _json(capabilities if capabilities is not None else old["model_capabilities"]),
                    old["main_text_model"] if main_model is None else main_model,
                    _json(old["backup_text_models"] if backups is None else self._models(backups)),
                    _json(old["protocol_order"] if protocols is None else self._protocols(protocols)),
                    _now(),
                    provider_id,
                ),
            )

    def migrate_legacy(self) -> None:
        if self.list():
            return
        settings = fresh_settings()
        if settings.ai_base_url.strip() and settings.ai_api_key.strip() and settings.ai_model.strip():
            self.create(
                name="原 FDEX AI 接口",
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                main_text_model=settings.ai_model,
                priority=1,
                timeout_seconds=int(settings.ai_timeout_seconds),
            )


@lru_cache
def provider_store() -> ProviderStore:
    store = ProviderStore()
    store.init()
    store.migrate_legacy()
    return store


def provider_stats() -> dict[str, int]:
    providers = provider_store().list()
    return {
        "total": len(providers),
        "enabled": sum(1 for p in providers if p["enabled"]),
        "healthy": sum(1 for p in providers if str(p["last_status"]).startswith("可用")),
        "image": sum(1 for p in providers if image_model_candidates(p)),
        "audio": sum(1 for p in providers if audio_model_candidates(p)),
    }


def _dedupe_models(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def text_model_candidates(provider: dict[str, Any], *, vision: bool = False) -> list[str]:
    if vision:
        overrides = _dedupe_models(
            [provider.get("main_vision_model", ""), *(provider.get("backup_vision_models") or [])]
        )
        if overrides:
            return overrides
    return _dedupe_models(
        [provider.get("main_text_model", ""), *(provider.get("backup_text_models") or [])]
    )


def model_candidates(provider: dict[str, Any]) -> list[str]:
    """Backward-compatible alias for text routing."""
    return text_model_candidates(provider)


def image_model_candidates(provider: dict[str, Any]) -> list[str]:
    return _dedupe_models(
        [provider.get("main_image_model", ""), *(provider.get("backup_image_models") or [])]
    )


def audio_model_candidates(provider: dict[str, Any]) -> list[str]:
    return _dedupe_models(
        [provider.get("main_audio_model", ""), *(provider.get("backup_audio_models") or [])]
    )


def api_roots(base_url: str) -> list[str]:
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        roots = [base, base[:-3].rstrip("/")]
    else:
        roots = [base + "/v1", base]
    return list(dict.fromkeys(x for x in roots if x))


def _extract_models(data: Any) -> list[str]:
    items: Any = None
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data.get("models"), list):
            items = data["models"]
    elif isinstance(data, list):
        items = data
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            value = item.get("id") or item.get("name") or item.get("model")
            if value:
                out.append(str(value))
    return _dedupe_models(out)


def _extract_chat(data: dict[str, Any]) -> str:
    try:
        value = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or item.get("content") or "")
            for item in value
            if isinstance(item, dict)
        ).strip()
    return ""


def _version_key(model: str) -> tuple[int, ...]:
    numbers = [int(x) for x in re.findall(r"\d+", model)]
    return tuple(numbers[:6]) if numbers else (0,)


async def probe_provider(provider_id: int, mode: str = "ordinary") -> dict[str, Any]:
    """Probe text routing only.

    Periodic deep tests deliberately avoid image/audio generation so scheduled
    health checks cannot create recurring media-generation costs.
    """
    store = provider_store()
    provider = store.get(provider_id, include_secret=True)
    if not provider.get("api_key"):
        store.record_probe(provider_id, status="失败：API Key 未配置", latency_ms=0)
        return {"ok": False, "message": "API Key 未配置", "models": [], "latency_ms": 0}

    started = perf_counter()
    discovered: list[str] = []
    if mode == "deep":
        for root in api_roots(provider["base_url"]):
            try:
                async with httpx.AsyncClient(timeout=provider["timeout_seconds"], follow_redirects=True) as client:
                    response = await client.get(
                        root.rstrip("/") + "/models",
                        headers={"Authorization": f"Bearer {provider['api_key']}", "Accept": "application/json"},
                    )
                if response.is_success:
                    discovered = _extract_models(response.json())
                    if discovered:
                        break
            except (httpx.HTTPError, ValueError):
                continue

    configured = text_model_candidates(provider)
    models = discovered if discovered else configured
    if mode != "deep":
        models = configured[:1]
    models = _dedupe_models(models)[:20]
    if not models:
        store.record_probe(provider_id, status="失败：没有可测试文本模型", latency_ms=0)
        return {"ok": False, "message": "没有可测试文本模型", "models": [], "latency_ms": 0}

    results: list[dict[str, Any]] = []
    capabilities = dict(provider.get("model_capabilities") or {})
    for model in models:
        caps = dict(capabilities.get(model) or {})
        marker = "FDEX_XAPI_OK"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": f"Reply with {marker}."}],
            "max_tokens": 24,
            "temperature": 0,
            "stream": False,
        }
        one_started = perf_counter()
        success = False
        error = ""
        status_code: int | None = None
        for root in api_roots(provider["base_url"]):
            url = root.rstrip("/") + "/chat/completions"
            try:
                async with httpx.AsyncClient(timeout=provider["timeout_seconds"], follow_redirects=True) as client:
                    response = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {provider['api_key']}", "Content-Type": "application/json"},
                        json=payload,
                    )
                status_code = response.status_code
                text = _extract_chat(response.json()) if response.is_success else ""
                success = bool(text)
                error = "" if success else response.text[:180]
                if success:
                    break
            except (httpx.HTTPError, ValueError) as exc:
                error = str(exc)[:180]
        latency = int((perf_counter() - one_started) * 1000)
        caps["chat"] = success
        caps.setdefault("responses", False)
        caps.setdefault("legacy", False)
        results.append(
            {
                "model": model,
                "protocol": "chat",
                "ok": success,
                "status": status_code,
                "latency_ms": latency,
                "error": error,
            }
        )
        capabilities[model] = caps

    usable = [model for model in models if capabilities.get(model, {}).get("chat")]
    main = provider["main_text_model"]
    backups = provider["backup_text_models"]
    if mode == "deep" and usable:
        if main not in usable:
            main = sorted(usable, key=_version_key, reverse=True)[0]
        backups = [x for x in sorted(usable, key=_version_key, reverse=True) if x != main]

    elapsed = int((perf_counter() - started) * 1000)
    ok = bool(usable)
    store.record_probe(
        provider_id,
        status=(f"可用：{len(usable)} 个文本模型" if ok else "失败：主/备用文本模型不可用"),
        latency_ms=elapsed,
        capabilities=capabilities,
        main_model=main,
        backups=backups,
    )
    return {
        "ok": ok,
        "message": (f"发现 {len(usable)} 个可用文本模型" if ok else "未发现可用文本模型"),
        "models": usable,
        "results": results,
        "latency_ms": elapsed,
    }


def auto_test_due(provider: dict[str, Any], now: datetime | None = None) -> bool:
    if not provider.get("enabled") or not provider.get("auto_test_enabled"):
        return False
    now = now or _now_dt()
    last = provider.get("last_test_at")
    if not last:
        return True
    try:
        parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    hours = max(1, int(provider.get("auto_test_interval_hours") or 12))
    return now >= parsed + timedelta(hours=hours)


async def run_due_provider_tests() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for provider in provider_store().list():
        if not auto_test_due(provider):
            continue
        try:
            result = await probe_provider(int(provider["id"]), mode="deep")
            results.append({"provider_id": provider["id"], "provider": provider["name"], **result})
        except Exception as exc:
            results.append(
                {
                    "provider_id": provider["id"],
                    "provider": provider["name"],
                    "ok": False,
                    "message": str(exc)[:300],
                }
            )
    return results
