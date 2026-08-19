from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

import anyio

from app.fdex_memory import LettaMemory, MemPalaceStore, MemoryOperationError, MemoryScope, safe_id

logger = logging.getLogger(__name__)
_PATCHED = False
_ORIGINAL_MEMPALACE_SEARCH = MemPalaceStore.search

_LATIN = re.compile(r"[a-z0-9_+.#/-]{2,}", re.IGNORECASE)
_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
_WHITESPACE = re.compile(r"\s+")


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    result: set[str] = set(_LATIN.findall(normalized))
    for match in _CJK.finditer(normalized):
        run = match.group(0)
        if len(run) <= 8:
            result.add(run)
        for index in range(max(0, len(run) - 1)):
            result.add(run[index : index + 2])
    return {item for item in result if item.strip()}


def _lexical_score(query: str, text: str) -> float:
    query_normalized = unicodedata.normalize("NFKC", query or "").strip().lower()
    text_normalized = unicodedata.normalize("NFKC", text or "").strip().lower()
    if not query_normalized or not text_normalized:
        return 0.0
    query_tokens = _tokens(query_normalized)
    text_tokens = _tokens(text_normalized)
    overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
    phrase = 0.45 if query_normalized in text_normalized else 0.0
    return min(1.0, overlap + phrase)


def _compact(value: str, limit: int) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value or "")).strip()[:limit]


def _structured_entry(user_text: str, assistant_text: str, conversation_id: str) -> str:
    user = _compact(user_text, 1400)
    assistant = _compact(assistant_text, 1400)
    tags = sorted(_tokens(f"{user} {assistant}"), key=lambda item: (-len(item), item))[:18]
    return "\n".join(
        [
            "[FDEX_STRUCTURED_MEMORY_V2]",
            f"time={datetime.now(UTC).isoformat()}",
            f"conversation={safe_id(conversation_id, 'unknown')}",
            f"tags={','.join(tags)}",
            f"USER_STATEMENT={user}",
            f"ASSISTANT_RESULT={assistant}",
        ]
    )


def _bounded_block(existing: str, entry: str, max_chars: int) -> str:
    header = (
        "FDEX structured long-term memory. USER_STATEMENT is user-provided data; "
        "ASSISTANT_RESULT is prior AI context and is not automatically a user fact.\n"
    )
    old = existing.strip()
    combined = f"{old}\n\n{entry}".strip() if old else entry
    budget = max(1000, max_chars)
    if len(header) + len(combined) <= budget:
        return (header + combined).strip()
    tail_budget = max(1, budget - len(header))
    return (header + combined[-tail_budget:]).strip()


async def _mempalace_search_resilient(
    self: MemPalaceStore,
    query: str,
    scope: MemoryScope,
    allowed_employee_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Keep request-time recall bounded and always fall back to raw SQLite history.

    Semantic Qdrant recall remains preferred, but it receives only a small budget. If the
    embedding proxy/Qdrant is unavailable, slow, or contains vectors from an older fallback
    space, recent verbatim drawers are still searched locally. This path never calls a chat
    completion model and therefore cannot compete with the user's real employee request.
    """
    if not query.strip():
        return []

    semantic: list[dict[str, Any]] = []
    semantic_budget = min(2.5, max(0.5, float(self.settings.fdex_memory_recall_timeout_seconds)))
    try:
        semantic = await asyncio.wait_for(
            _ORIGINAL_MEMPALACE_SEARCH(self, query, scope, allowed_employee_ids),
            timeout=semantic_budget,
        )
    except TimeoutError:
        logger.warning("FDEX MemPalace semantic recall exceeded %.1fs; using raw lexical fallback", semantic_budget)
    except MemoryOperationError as exc:
        logger.warning("FDEX MemPalace semantic recall degraded code=%s; using raw lexical fallback", exc.code)
    except Exception:
        logger.exception("FDEX MemPalace semantic recall failed; using raw lexical fallback")

    recent: list[dict[str, Any]] = []
    try:
        recent = await asyncio.wait_for(
            self.recent(
                scope,
                allowed_employee_ids,
                limit=max(30, self.settings.fdex_memory_recall_limit * 8),
            ),
            timeout=1.5,
        )
    except Exception:
        logger.warning("FDEX MemPalace raw lexical fallback unavailable", exc_info=True)

    merged: dict[str, dict[str, Any]] = {}
    for item in semantic:
        copy = dict(item)
        lexical = _lexical_score(query, str(copy.get("text") or ""))
        try:
            semantic_score = float(copy.get("similarity") or 0.0)
        except (TypeError, ValueError):
            semantic_score = 0.0
        copy["similarity"] = max(semantic_score, lexical)
        drawer_id = str(copy.get("drawer_id") or "")
        if drawer_id:
            merged[drawer_id] = copy

    for item in recent:
        score = _lexical_score(query, str(item.get("text") or ""))
        if score <= 0.0:
            continue
        drawer_id = str(item.get("drawer_id") or "")
        if not drawer_id:
            continue
        existing = merged.get(drawer_id)
        if existing is not None:
            existing["similarity"] = max(float(existing.get("similarity") or 0.0), score)
            continue
        merged[drawer_id] = {
            "drawer_id": drawer_id,
            "wing": str(item.get("wing") or ""),
            "room": str(item.get("room") or "conversation"),
            "role": str(item.get("role") or ""),
            "conversation_id": str(item.get("conversation_id") or ""),
            "text": str(item.get("text") or ""),
            "similarity": score,
        }

    return sorted(
        merged.values(),
        key=lambda item: float(item.get("similarity") or 0.0),
        reverse=True,
    )[: self.settings.fdex_memory_recall_limit]


async def _retrieve_human_block(self: LettaMemory, agent_id: str, timeout_seconds: float) -> Any:
    def retrieve() -> Any:
        return self._get_client().agents.blocks.retrieve(
            agent_id=agent_id,
            block_label="human",
            request_options={"timeout_in_seconds": timeout_seconds},
        )

    try:
        return await asyncio.wait_for(
            anyio.to_thread.run_sync(retrieve, abandon_on_cancel=True),
            timeout=timeout_seconds + 0.5,
        )
    except TimeoutError as exc:
        raise MemoryOperationError("letta_block_timeout") from exc
    except Exception as exc:
        raise self._operation_error(exc, "block_recall") from exc


async def _letta_recall_from_block(self: LettaMemory, query: str, scope: MemoryScope) -> str:
    """Recall Letta structured memory without generating another model response."""
    if not query.strip() or not self.settings.fdex_letta_enabled:
        return ""
    agent_id = await self.ensure_agent(scope)
    if not agent_id:
        return ""
    timeout_seconds = min(3.0, max(1.0, float(self.settings.fdex_letta_timeout_seconds)))
    block = await _retrieve_human_block(self, agent_id, timeout_seconds)
    value = getattr(block, "value", "")
    if not isinstance(value, str):
        value = str(value or "")
    return value.strip()[: self.settings.fdex_memory_context_max_chars]


async def _letta_remember_to_block(
    self: LettaMemory,
    *,
    scope: MemoryScope,
    user_text: str,
    assistant_text: str,
    conversation_id: str,
) -> bool:
    """Persist structured Letta memory by updating the core block, never by model generation.

    MemPalace remains the verbatim source of truth. Letta's human block receives a bounded,
    explicitly typed timeline so prior AI text cannot silently become a user fact. This keeps
    memory writes independent from the chat2api generation capacity used by the live employee.
    """
    if not self.settings.fdex_letta_enabled:
        return False
    if not user_text.strip():
        return True
    agent_id = await self.ensure_agent(scope)
    if not agent_id:
        raise MemoryOperationError("letta_agent_unavailable")

    timeout_seconds = min(3.0, max(1.0, float(self.settings.fdex_letta_timeout_seconds)))
    entry = _structured_entry(user_text, assistant_text, conversation_id)

    async with self._lock:
        block = await _retrieve_human_block(self, agent_id, timeout_seconds)
        existing = getattr(block, "value", "")
        if not isinstance(existing, str):
            existing = str(existing or "")
        value = _bounded_block(existing, entry, self.settings.fdex_memory_context_max_chars)

        def update() -> Any:
            return self._get_client().agents.blocks.update(
                agent_id=agent_id,
                block_label="human",
                value=value,
                limit=max(1000, self.settings.fdex_memory_context_max_chars),
                request_options={"timeout_in_seconds": timeout_seconds},
            )

        try:
            await asyncio.wait_for(
                anyio.to_thread.run_sync(update, abandon_on_cancel=True),
                timeout=timeout_seconds + 0.5,
            )
        except TimeoutError as exc:
            raise MemoryOperationError("letta_block_write_timeout") from exc
        except Exception as exc:
            raise self._operation_error(exc, "block_write") from exc
    return True


def apply_memory_resilience_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return
    MemPalaceStore.search = _mempalace_search_resilient
    LettaMemory.recall = _letta_recall_from_block
    LettaMemory.remember = _letta_remember_to_block
    _PATCHED = True
