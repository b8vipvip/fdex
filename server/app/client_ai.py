from __future__ import annotations

from time import perf_counter

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import fresh_settings

router = APIRouter(prefix="/api/client", tags=["android-client"])


class AIRequest(BaseModel):
    system: str = Field(default="你是 FDEX AI 虚拟公司的企业助手。", max_length=12000)
    prompt: str = Field(min_length=1, max_length=40000)
    max_tokens: int = Field(default=1200, ge=32, le=4000)


class AIResponse(BaseModel):
    content: str
    model: str
    latency_ms: int


@router.post("/ai", response_model=AIResponse)
async def client_ai(payload: AIRequest) -> AIResponse:
    settings = fresh_settings()
    if not settings.ai_enabled:
        raise HTTPException(503, "服务端尚未配置 AI 接口，请先在管理后台完成 AI 配置。")

    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.ai_model,
        "messages": [
            {"role": "system", "content": payload.system},
            {"role": "user", "content": payload.prompt},
        ],
        "temperature": 0.5,
        "max_tokens": payload.max_tokens,
    }
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        content = str(data["choices"][0]["message"]["content"]).strip()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise HTTPException(502, f"上游 AI 返回错误：{detail}") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"AI 接口调用失败：{str(exc)[:300]}") from exc

    return AIResponse(
        content=content,
        model=settings.ai_model,
        latency_ms=int((perf_counter() - started) * 1000),
    )
