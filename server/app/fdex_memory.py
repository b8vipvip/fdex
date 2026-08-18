from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx

from app.config import Settings, fresh_settings

logger = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"[^a-zA-Z0-9_.-]+")


def safe_id(value: str, fallback: str = "default") -> str:
    normalized = _SAFE_ID.sub("_", (value or "").strip()).strip("_.-")
    return normalized[:96] or fallback


class MemoryOperationError(RuntimeError):
    def __init__(self, code: str):
        normalized = code.strip().lower()
        if not normalized or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in normalized):
            raise ValueError("invalid memory error code")
        super().__init__(normalized)
        self.code = normalized


@dataclass(frozen=True, slots=True)
class MemoryScope:
    account_id: str
    vault_id: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", self._component(self.account_id, "default"))
        object.__setattr__(self, "vault_id", self._component(self.vault_id, "default"))

    @staticmethod
    def _component(value: str, fallback: str) -> str:
        normalized = safe_id(value, fallback)
        if len(normalized) <= 32:
            return normalized
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:10]
        return f"{normalized[:21]}-{digest}"

    @property
    def storage_key(self) -> str:
        return f"acct.{self.account_id}.vault.{self.vault_id}"

    @property
    def display_key(self) -> str:
        return f"account:{self.account_id}/{self.vault_id}"


@dataclass(frozen=True, slots=True)
class MemoryRecall:
    mempalace_raw: str = ""
    letta_structured: str = ""
    error_codes: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.mempalace_raw.strip() and not self.letta_structured.strip()


class RemoteEmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.fdex_memory_recall_timeout_seconds),
            follow_redirects=True,
        )

    async def embed(self, texts: list[str], timeout_seconds: float) -> list[list[float]]:
        if not texts:
            return []
        token = self.settings.fdex_memory_proxy_token.strip()
        if not token:
            raise MemoryOperationError("memory_proxy_token_missing")
        try:
            response = await self._client.post(
                f"{self.settings.fdex_memory_proxy_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": "text-embedding-3-small", "input": texts},
                timeout=max(0.1, timeout_seconds),
            )
        except httpx.TimeoutException as exc:
            raise MemoryOperationError("mempalace_embedding_timeout") from exc
        except httpx.HTTPError as exc:
            raise MemoryOperationError("mempalace_embedding_unavailable") from exc
        if response.status_code in {401, 403}:
            raise MemoryOperationError("mempalace_embedding_auth_failed")
        if response.status_code == 429:
            raise MemoryOperationError("mempalace_embedding_rate_limited")
        if response.status_code >= 400:
            raise MemoryOperationError("mempalace_embedding_rejected")
        try:
            payload = response.json()
            data = payload["data"]
            ordered: list[list[float] | None] = [None] * len(texts)
            dimension: int | None = None
            for fallback_index, item in enumerate(data):
                index = int(item.get("index", fallback_index))
                vector = [float(value) for value in item["embedding"]]
                if dimension is None:
                    dimension = len(vector)
                if not vector or len(vector) != dimension or not 0 <= index < len(texts):
                    raise ValueError("invalid vector")
                ordered[index] = vector
            if any(item is None for item in ordered):
                raise ValueError("missing vector")
            return [item for item in ordered if item is not None]
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryOperationError("mempalace_embedding_invalid_response") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class MemPalaceStore:
    """FDEX port of SuMeMe's remote MemPalace raw-history store.

    Verbatim drawers stay in SQLite. Qdrant stores only semantic vectors plus
    scope metadata and a drawer id. Raw history is written before indexing so a
    temporary vector/Qdrant failure never loses the conversation itself.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._db_path = Path(settings.fdex_memory_data_dir) / "mempalace-raw.sqlite3"
        self._embedding = RemoteEmbeddingClient(settings)
        self._qdrant = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.fdex_memory_qdrant_timeout_seconds),
            follow_redirects=True,
        )
        self._initialize_lock = asyncio.Lock()
        self._collection_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await anyio.to_thread.run_sync(self._initialize_sync)
            self._initialized = True

    async def search(
        self,
        query: str,
        scope: MemoryScope,
        allowed_employee_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        await self.initialize()
        vector = (
            await self._embedding.embed(
                [query[: self.settings.fdex_memory_embedding_max_chars]],
                self.settings.fdex_memory_recall_timeout_seconds,
            )
        )[0]
        await self._ensure_collection(len(vector))
        payload = await self._qdrant_request(
            "POST",
            f"/collections/{self.settings.fdex_memory_qdrant_collection}/points/query",
            json_body={
                "query": vector,
                "filter": self._qdrant_filter(scope, allowed_employee_ids),
                "limit": self.settings.fdex_memory_recall_limit,
                "with_payload": True,
                "with_vector": False,
            },
            operation="query",
        )
        result = payload.get("result")
        points = result.get("points", []) if isinstance(result, dict) else result
        if not isinstance(points, list):
            return []
        ordered: list[str] = []
        scores: dict[str, float] = {}
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("payload"), dict):
                continue
            drawer_id = str(point["payload"].get("drawer_id") or "")
            if not drawer_id or drawer_id in scores:
                continue
            ordered.append(drawer_id)
            try:
                scores[drawer_id] = float(point.get("score") or 0.0)
            except (TypeError, ValueError):
                scores[drawer_id] = 0.0
        if not ordered:
            return []
        rows = await anyio.to_thread.run_sync(self._read_drawers_sync, scope, ordered, allowed_employee_ids)
        by_id = {str(row["drawer_id"]): row for row in rows}
        output: list[dict[str, Any]] = []
        for drawer_id in ordered:
            row = by_id.get(drawer_id)
            if row is None:
                continue
            output.append(
                {
                    "drawer_id": drawer_id,
                    "wing": str(row["wing"]),
                    "room": str(row["room"]),
                    "role": str(row["role"]),
                    "conversation_id": str(row["conversation_id"]),
                    "text": str(row["content"]),
                    "similarity": scores.get(drawer_id, 0.0),
                }
            )
        return output[: self.settings.fdex_memory_recall_limit]

    async def add_exchange(
        self,
        *,
        scope: MemoryScope,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        employee_id: str = "",
    ) -> bool:
        await self.initialize()
        items = self._build_items(scope, conversation_id, user_text, assistant_text, employee_id)
        if not items:
            return True
        await anyio.to_thread.run_sync(self._store_drawers_sync, items)
        vectors = await self._embedding.embed(
            [str(item["search_text"]) for item in items],
            self.settings.fdex_memory_write_timeout_seconds,
        )
        if not vectors:
            raise MemoryOperationError("mempalace_embedding_empty")
        await self._ensure_collection(len(vectors[0]))
        points = []
        for item, vector in zip(items, vectors, strict=True):
            points.append(
                {
                    "id": item["point_id"],
                    "vector": vector,
                    "payload": {
                        "drawer_id": item["drawer_id"],
                        "scope_key": scope.storage_key,
                        "wing": item["wing"],
                        "room": item["room"],
                        "role": item["role"],
                        "content_hash": item["content_hash"],
                        "employee_id": item["employee_id"],
                        "created_at": item["created_at"],
                    },
                }
            )
        await self._qdrant_request(
            "PUT",
            f"/collections/{self.settings.fdex_memory_qdrant_collection}/points?wait=true",
            json_body={"points": points},
            operation="write",
        )
        return True

    def _build_items(
        self,
        scope: MemoryScope,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        employee_id: str = "",
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC).isoformat()
        conversation = safe_id(conversation_id, "unknown")
        wing = f"scope_{safe_id(scope.storage_key)}"
        raw_items = [("user", user_text.strip())]
        if assistant_text.strip():
            raw_items.append(("assistant", assistant_text.strip()))
        output: list[dict[str, Any]] = []
        for role, text in raw_items:
            if not text:
                continue
            stable = json.dumps(
                {"role": role, "content": text},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = hashlib.sha256(stable.encode()).hexdigest()
            identity = f"{scope.storage_key}\n{conversation}\n{role}\n{content_hash}"
            point_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"fdex:mempalace:{identity}")
            output.append(
                {
                    "drawer_id": point_uuid.hex,
                    "point_id": str(point_uuid),
                    "account_id": scope.account_id,
                    "vault_id": scope.vault_id,
                    "wing": wing,
                    "room": "conversation",
                    "role": role,
                    "conversation_id": conversation,
                    "employee_id": str(employee_id or ""),
                    "source": f"fdex:{conversation}",
                    "content": text,
                    "content_hash": content_hash,
                    "search_text": text[: self.settings.fdex_memory_embedding_max_chars],
                    "created_at": now,
                }
            )
        return output

    @staticmethod
    def _qdrant_filter(scope: MemoryScope, allowed_employee_ids: set[str] | None) -> dict[str, Any]:
        must: list[dict[str, Any]] = [
            {"key": "scope_key", "match": {"value": scope.storage_key}}
        ]
        if allowed_employee_ids is not None:
            normalized = sorted({str(value) for value in allowed_employee_ids if str(value)})
            if not normalized:
                # A condition that cannot match any valid employee id. Callers normally skip
                # MemPalace entirely for NONE, but keeping this filter makes direct use safe.
                must.append({"key": "employee_id", "match": {"value": "__none__"}})
            elif len(normalized) == 1:
                must.append({"key": "employee_id", "match": {"value": normalized[0]}})
            else:
                must.append({"key": "employee_id", "match": {"any": normalized}})
        return {"must": must}

    async def _ensure_collection(self, dimension: int) -> None:
        async with self._collection_lock:
            current = await self._collection_dimension()
            if current is None:
                try:
                    response = await self._qdrant.put(
                        self._qdrant_url(f"/collections/{self.settings.fdex_memory_qdrant_collection}"),
                        json={"vectors": {"size": dimension, "distance": "Cosine", "on_disk": True}},
                    )
                except httpx.HTTPError as exc:
                    raise MemoryOperationError("mempalace_qdrant_unavailable") from exc
                if response.status_code not in {200, 201, 409}:
                    raise self._qdrant_error(response, "collection_create")
                current = await self._collection_dimension()
            if current != dimension:
                raise MemoryOperationError("mempalace_embedding_dimension_changed")
            for field_name in ("scope_key", "employee_id"):
                try:
                    response = await self._qdrant.put(
                        self._qdrant_url(
                            f"/collections/{self.settings.fdex_memory_qdrant_collection}/index?wait=true"
                        ),
                        json={"field_name": field_name, "field_schema": "keyword"},
                    )
                    if response.status_code not in {200, 201, 409}:
                        logger.warning(
                            "FDEX MemPalace payload index creation failed field=%s status=%s",
                            field_name,
                            response.status_code,
                        )
                except httpx.HTTPError:
                    logger.warning(
                        "FDEX MemPalace payload index creation unavailable field=%s",
                        field_name,
                        exc_info=True,
                    )

    async def _collection_dimension(self) -> int | None:
        try:
            response = await self._qdrant.get(
                self._qdrant_url(f"/collections/{self.settings.fdex_memory_qdrant_collection}")
            )
        except httpx.TimeoutException as exc:
            raise MemoryOperationError("mempalace_qdrant_timeout") from exc
        except httpx.HTTPError as exc:
            raise MemoryOperationError("mempalace_qdrant_unavailable") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise self._qdrant_error(response, "collection_read")
        try:
            return int(response.json()["result"]["config"]["params"]["vectors"]["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryOperationError("mempalace_qdrant_invalid_response") from exc

    async def _qdrant_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        try:
            response = await self._qdrant.request(method, self._qdrant_url(path), json=json_body)
        except httpx.TimeoutException as exc:
            raise MemoryOperationError("mempalace_qdrant_timeout") from exc
        except httpx.HTTPError as exc:
            raise MemoryOperationError("mempalace_qdrant_unavailable") from exc
        if response.status_code >= 400:
            raise self._qdrant_error(response, operation)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MemoryOperationError("mempalace_qdrant_invalid_response") from exc
        if not isinstance(payload, dict):
            raise MemoryOperationError("mempalace_qdrant_invalid_response")
        return payload

    @staticmethod
    def _qdrant_error(response: httpx.Response, operation: str) -> MemoryOperationError:
        if response.status_code in {401, 403}:
            return MemoryOperationError("mempalace_qdrant_auth_failed")
        if response.status_code == 429:
            return MemoryOperationError("mempalace_qdrant_rate_limited")
        if response.status_code >= 500:
            return MemoryOperationError("mempalace_qdrant_server_error")
        return MemoryOperationError(f"mempalace_qdrant_{operation}_rejected")

    def _qdrant_url(self, path: str) -> str:
        return f"{self.settings.fdex_memory_qdrant_url.rstrip('/')}/{path.lstrip('/')}"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mempalace_drawers (
                    drawer_id TEXT PRIMARY KEY,
                    point_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL,
                    vault_id TEXT NOT NULL,
                    wing TEXT NOT NULL,
                    room TEXT NOT NULL,
                    role TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    employee_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(mempalace_drawers)").fetchall()}
            if "employee_id" not in columns:
                connection.execute("ALTER TABLE mempalace_drawers ADD COLUMN employee_id TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS mempalace_drawers_scope_idx "
                "ON mempalace_drawers(account_id,vault_id,employee_id,created_at DESC)"
            )

    def _store_drawers_sync(self, items: list[dict[str, Any]]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO mempalace_drawers(
                    drawer_id,point_id,account_id,vault_id,wing,room,role,
                    conversation_id,employee_id,source,content,content_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        item["drawer_id"], item["point_id"], item["account_id"], item["vault_id"],
                        item["wing"], item["room"], item["role"], item["conversation_id"],
                        item["employee_id"], item["source"], item["content"], item["content_hash"], item["created_at"],
                    )
                    for item in items
                ],
            )

    def _read_drawers_sync(
        self,
        scope: MemoryScope,
        drawer_ids: list[str],
        allowed_employee_ids: set[str] | None = None,
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in drawer_ids)
        employee_clause = ""
        params: list[Any] = [scope.account_id, scope.vault_id]
        if allowed_employee_ids is not None:
            normalized = sorted({str(value) for value in allowed_employee_ids if str(value)})
            if not normalized:
                return []
            employee_clause = " AND employee_id IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        params.extend(drawer_ids)
        with self._connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT drawer_id,wing,room,role,conversation_id,content
                    FROM mempalace_drawers
                    WHERE account_id=? AND vault_id=?{employee_clause}
                      AND drawer_id IN ({placeholders})
                    """,
                    tuple(params),
                ).fetchall()
            )

    async def aclose(self) -> None:
        await self._embedding.aclose()
        await self._qdrant.aclose()


class LettaMemory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any | None = None
        self._agents: dict[str, str] = {}
        self._owners: dict[str, str] = {}
        self._loaded = False
        self._lock = anyio.Lock()
        self._state_file = Path(settings.fdex_memory_data_dir) / "letta-agent.json"

    def _get_client(self) -> Any:
        if self._client is None:
            from letta_client import Letta

            kwargs: dict[str, Any] = {"base_url": self.settings.fdex_letta_base_url.rstrip("/")}
            password = self.settings.fdex_letta_server_password.strip()
            if password:
                kwargs["api_key"] = password
            self._client = Letta(**kwargs)
        return self._client

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            agents = payload.get("agents") if isinstance(payload, dict) else None
            if not isinstance(agents, dict):
                return
            for scope_key, agent_id in agents.items():
                scope_value = str(scope_key or "").strip()
                agent_value = str(agent_id or "").strip()
                if not scope_value or not agent_value or agent_value in self._owners:
                    continue
                self._agents[scope_value] = agent_value
                self._owners[agent_value] = scope_value
        except Exception:
            logger.warning("Could not read FDEX Letta agent map", exc_info=True)

    def _persist(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_file.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"schema_version": 1, "agents": self._agents}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._state_file)

    async def ensure_agent(self, scope: MemoryScope) -> str | None:
        if not self.settings.fdex_letta_enabled:
            return None
        self._load()
        scope_key = scope.storage_key
        if self._agents.get(scope_key):
            return self._agents[scope_key]
        async with self._lock:
            if self._agents.get(scope_key):
                return self._agents[scope_key]

            def create() -> Any:
                return self._get_client().agents.create(
                    name=f"fdex-company-memory-{safe_id(scope_key)}",
                    model=self.settings.fdex_letta_model,
                    embedding=self.settings.fdex_letta_embedding,
                    memory_blocks=[
                        {
                            "label": "human",
                            "value": (
                                "This is one isolated FDEX company memory vault. "
                                f"Scope: {scope.display_key}. Keep stable facts, preferences, projects, "
                                "people, events, dates, changes and contradictions. Never copy facts from another scope."
                            ),
                        },
                        {
                            "label": "persona",
                            "value": (
                                "You are FDEX's structured memory curator. MEMORY_UPDATE updates durable memory. "
                                "RECALL_ONLY returns only relevant remembered facts, uncertainty and dates; it never answers the task itself."
                            ),
                        },
                    ],
                    request_options={"timeout_in_seconds": self.settings.fdex_letta_timeout_seconds},
                )

            try:
                agent = await anyio.to_thread.run_sync(create, abandon_on_cancel=True)
            except Exception as exc:
                raise self._operation_error(exc, "agent_create") from exc
            agent_id = str(getattr(agent, "id", "") or "")
            if not agent_id:
                raise MemoryOperationError("letta_invalid_response")
            owner = self._owners.get(agent_id)
            if owner and owner != scope_key:
                raise MemoryOperationError("letta_agent_ownership_conflict")
            self._agents[scope_key] = agent_id
            self._owners[agent_id] = scope_key
            self._persist()
            return agent_id

    async def recall(self, query: str, scope: MemoryScope) -> str:
        if not query.strip() or not self.settings.fdex_letta_enabled:
            return ""
        agent_id = await self.ensure_agent(scope)
        if not agent_id:
            return ""
        prompt = (
            "[RECALL_ONLY]\n"
            f"Memory scope: {scope.display_key}\n"
            "只返回与当前问题相关的长期结构化记忆，使用简洁中文要点；包含日期和不确定性；"
            "不要回答用户当前问题，不得引用其他 scope。\n\n"
            f"Current question:\n{query[:12000]}"
        )
        try:
            response = await anyio.to_thread.run_sync(
                lambda: self._get_client().agents.messages.create(
                    agent_id=agent_id,
                    input=prompt,
                    request_options={"timeout_in_seconds": self.settings.fdex_letta_timeout_seconds},
                ),
                abandon_on_cancel=True,
            )
        except Exception as exc:
            raise self._operation_error(exc, "recall") from exc
        return self._extract_text(response)

    async def remember(
        self,
        *,
        scope: MemoryScope,
        user_text: str,
        assistant_text: str,
        conversation_id: str,
    ) -> bool:
        if not self.settings.fdex_letta_enabled:
            return False
        if not user_text.strip():
            return True
        agent_id = await self.ensure_agent(scope)
        if not agent_id:
            raise MemoryOperationError("letta_agent_unavailable")
        prompt = (
            "[MEMORY_UPDATE]\n"
            f"Memory scope: {scope.display_key}\n"
            "学习下面这轮 FDEX 对话并更新耐久结构化记忆。保留明确的名称、日期、数字、偏好、决定、"
            "项目状态和变化；不要把 AI 猜测当成用户事实，不得读取或更新其他 scope。只回复 SAVED。\n\n"
            f"conversation_id: {safe_id(conversation_id, 'unknown')}\n"
            f"USER:\n{user_text[:30000]}\n\nASSISTANT:\n{assistant_text[:30000]}"
        )
        try:
            await anyio.to_thread.run_sync(
                lambda: self._get_client().agents.messages.create(
                    agent_id=agent_id,
                    input=prompt,
                    request_options={"timeout_in_seconds": self.settings.fdex_letta_timeout_seconds},
                ),
                abandon_on_cancel=True,
            )
        except Exception as exc:
            raise self._operation_error(exc, "write") from exc
        return True

    @staticmethod
    def _operation_error(exc: Exception, operation: str) -> MemoryOperationError:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            code = int(status) if status is not None else None
        except (TypeError, ValueError):
            code = None
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return MemoryOperationError("letta_timeout")
        if code in {401, 403}:
            return MemoryOperationError("letta_auth_failed")
        if code == 404:
            return MemoryOperationError("letta_agent_not_found")
        if code == 429:
            return MemoryOperationError("letta_rate_limited")
        if code is not None and code >= 500:
            return MemoryOperationError("letta_server_error")
        if code is not None and code >= 400:
            return MemoryOperationError(f"letta_{operation}_rejected")
        if "connection" in name or "connect" in name:
            return MemoryOperationError("letta_unavailable")
        return MemoryOperationError(f"letta_{operation}_failed")

    @staticmethod
    def _extract_text(response: Any) -> str:
        if hasattr(response, "model_dump"):
            value = response.model_dump()
        elif isinstance(response, dict):
            value = response
        else:
            value = json.loads(json.dumps(response, default=lambda obj: getattr(obj, "__dict__", str(obj))))
        texts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                message_type = str(node.get("message_type") or node.get("type") or "")
                if "assistant" in message_type:
                    content = node.get("content") or node.get("text")
                    if isinstance(content, str) and content.strip():
                        texts.append(content.strip())
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return "\n".join(dict.fromkeys(texts)).strip()


class MemoryCoordinator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or fresh_settings()
        self.mempalace = MemPalaceStore(self.settings)
        self.letta = LettaMemory(self.settings)

    async def recall(
        self,
        query: str,
        scope: MemoryScope,
        *,
        allowed_employee_ids: set[str] | None = None,
        include_letta: bool = True,
    ) -> MemoryRecall:
        if not self.settings.fdex_memory_enabled or not query.strip():
            return MemoryRecall()
        if allowed_employee_ids is not None and not allowed_employee_ids:
            raw_task = asyncio.sleep(0, result=([], ""))
        else:
            raw_task = self._recall_component(
                "mempalace",
                self.mempalace.search(query, scope, allowed_employee_ids),
                [],
            )
        if include_letta:
            letta_task = self._recall_component("letta", self.letta.recall(query, scope), "")
        else:
            letta_task = asyncio.sleep(0, result=("", ""))
        raw, structured = await asyncio.gather(raw_task, letta_task)
        raw_value, raw_error = raw
        structured_value, letta_error = structured
        rendered = []
        for item in raw_value[: self.settings.fdex_memory_recall_limit]:
            role = "用户" if item.get("role") == "user" else "AI"
            rendered.append(
                f"- [{item.get('wing')}/{item.get('room')} score={item.get('similarity', 0):.4f} "
                f"conversation={item.get('conversation_id')}] {role}：{item.get('text', '')}"
            )
        errors = tuple(code for code in (raw_error, letta_error) if code)
        return MemoryRecall(
            mempalace_raw="\n".join(rendered)[: self.settings.fdex_memory_context_max_chars],
            letta_structured=str(structured_value)[: self.settings.fdex_memory_context_max_chars],
            error_codes=errors,
        )

    async def remember_exchange(
        self,
        *,
        scope: MemoryScope,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        employee_id: str = "",
        write_structured: bool = True,
    ) -> dict[str, Any]:
        if not self.settings.fdex_memory_enabled or not user_text.strip() or not assistant_text.strip():
            return {"mempalace": False, "letta": False, "errors": []}
        outcomes = await asyncio.gather(
            self._write_component(
                "mempalace",
                self.mempalace.add_exchange(
                    scope=scope,
                    conversation_id=conversation_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    employee_id=employee_id,
                ),
            ),
            self._write_component(
                "letta",
                self.letta.remember(
                    scope=scope,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    conversation_id=conversation_id,
                ),
            ) if write_structured else asyncio.sleep(0, result=("letta", False, "")),
        )
        components: dict[str, bool] = {}
        errors: list[str] = []
        for name, accepted, error in outcomes:
            components[name] = accepted
            if error:
                errors.append(error)
        return {**components, "errors": errors}

    async def _recall_component(self, name: str, operation: Any, default: Any) -> tuple[Any, str]:
        try:
            return await asyncio.wait_for(
                operation,
                timeout=self.settings.fdex_memory_recall_timeout_seconds,
            ), ""
        except TimeoutError:
            code = f"{name}_recall_timeout"
        except MemoryOperationError as exc:
            code = exc.code
        except Exception:
            logger.exception("FDEX memory recall failed component=%s", name)
            code = f"{name}_recall_exception"
        logger.warning("FDEX memory recall degraded component=%s code=%s", name, code)
        return default, code

    async def _write_component(self, name: str, operation: Any) -> tuple[str, bool, str]:
        try:
            accepted = await asyncio.wait_for(
                operation,
                timeout=self.settings.fdex_memory_write_timeout_seconds,
            )
            return name, accepted is True, "" if accepted is True else f"{name}_write_rejected"
        except TimeoutError:
            code = f"{name}_write_timeout"
        except MemoryOperationError as exc:
            code = exc.code
        except Exception:
            logger.exception("FDEX memory write failed component=%s", name)
            code = f"{name}_write_exception"
        logger.warning("FDEX memory write degraded component=%s code=%s", name, code)
        return name, False, code

    async def aclose(self) -> None:
        await self.mempalace.aclose()


_coordinator: MemoryCoordinator | None = None


def memory_coordinator() -> MemoryCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = MemoryCoordinator()
    return _coordinator


async def close_memory_coordinator() -> None:
    global _coordinator
    if _coordinator is not None:
        await _coordinator.aclose()
        _coordinator = None
