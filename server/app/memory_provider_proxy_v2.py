from __future__ import annotations

import asyncio
import hashlib
import math
import re
import unicodedata
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.memory_provider_proxy import (
    _embedding_inputs,
    _json_body,
    _native_embeddings,
    _providers,
    _proxy_openai,
    _settings,
    authenticate,
    lifespan,
)

app = FastAPI(title="FDEX Memory Provider Proxy", lifespan=lifespan)
app.middleware("http")(authenticate)

_LATIN = re.compile(r"[a-z0-9_+.#/-]{2,}", re.IGNORECASE)
_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")


def _features(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "").strip().lower()
    values: list[str] = []
    values.extend(_LATIN.findall(normalized))
    for match in _CJK.finditer(normalized):
        run = match.group(0)
        if len(run) <= 12:
            values.append(run)
        for index in range(max(0, len(run) - 1)):
            values.append(run[index : index + 2])
        for index in range(max(0, len(run) - 2)):
            values.append(run[index : index + 3])
    if not values and normalized:
        values.append(normalized[:120])
    return list(dict.fromkeys(item for item in values if item))


def _hash_text(text: str, dimension: int) -> list[float]:
    vector = [0.0] * dimension
    features = _features(text)
    if not features:
        raise ValueError("empty embedding input")
    for rank, feature in enumerate(features):
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        weight = 1.0 / math.sqrt(rank + 1)
        for projection in range(4):
            offset = projection * 4
            index = int.from_bytes(digest[offset : offset + 4], "big") % dimension
            sign = 1.0 if digest[16 + projection] & 1 else -1.0
            vector[index] += sign * weight * 0.5
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError("empty embedding vector")
    return [value / norm for value in vector]


@app.get("/health")
async def health() -> dict[str, Any]:
    providers = _providers()
    return {
        "status": "ok" if providers else "degraded",
        "service": "fdex-memory-provider-proxy",
        "provider_count": len(providers),
        "embedding_mode": "native-openai-compatible-with-local-text-hash-fallback",
        "local_models": False,
        "generative_embedding_fallback": False,
    }


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": "gpt-4o-mini", "object": "model", "owned_by": "fdex"},
            {"id": "text-embedding-3-small", "object": "model", "owned_by": "fdex"},
        ],
    }


@app.get("/v1/models/{model_id:path}")
async def model(model_id: str) -> dict[str, Any]:
    return {"id": model_id or "gpt-4o-mini", "object": "model", "owned_by": "fdex"}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Response:
    payload = await _json_body(request)
    return await _proxy_openai(request, payload, "chat/completions")


@app.post("/v1/responses")
async def responses(request: Request) -> Response:
    payload = await _json_body(request)
    return await _proxy_openai(request, payload, "responses")


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    values = _embedding_inputs(payload.get("input"))
    settings = _settings()

    # Native OpenAI-compatible embeddings remain preferred, but probing is strictly bounded.
    # A provider without /embeddings must never turn one user message into another chat request.
    native: dict[str, Any] | None = None
    native_budget = min(1.5, max(0.35, settings.fdex_memory_recall_timeout_seconds / 4.0))
    try:
        native = await asyncio.wait_for(_native_embeddings(request, values), timeout=native_budget)
    except TimeoutError:
        native = None
    if native is not None:
        return JSONResponse(native)

    dimension = settings.fdex_memory_embedding_dimension
    vectors = [_hash_text(value, dimension) for value in values]
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"object": "embedding", "embedding": vector, "index": index}
                for index, vector in enumerate(vectors)
            ],
            "model": settings.fdex_memory_embedding_model,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
            "fdex_fallback": "local_text_hash",
        }
    )
