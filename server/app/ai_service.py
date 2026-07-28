from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from app.config import Settings


def _safe_text(response: httpx.Response, api_key: str) -> str:
    text = response.text.strip().replace(api_key, "***") if api_key else response.text.strip()
    return text[:500]


async def test_ai_connection(settings: Settings) -> dict[str, object]:
    if not settings.ai_enabled:
        return {"ok": False, "message": "AI 接口尚未完整配置", "status": None, "latency_ms": 0}

    parsed = urlparse(settings.ai_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "message": "AI_BASE_URL 不是有效的 HTTP/HTTPS 地址", "status": None, "latency_ms": 0}

    base = settings.ai_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key}",
        "Accept": "application/json",
        "User-Agent": f"FDEX-Server/{settings.app_version}",
    }
    timeout = httpx.Timeout(settings.ai_timeout_seconds)
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(f"{base}/models", headers=headers)
            if response.is_success:
                latency = int((time.perf_counter() - started) * 1000)
                return {
                    "ok": True,
                    "message": "AI 接口连接成功，模型列表可访问",
                    "status": response.status_code,
                    "latency_ms": latency,
                }

            if response.status_code not in {404, 405}:
                latency = int((time.perf_counter() - started) * 1000)
                return {
                    "ok": False,
                    "message": f"上游返回 {response.status_code}：{_safe_text(response, settings.ai_api_key)}",
                    "status": response.status_code,
                    "latency_ms": latency,
                }

            # Some OpenAI-compatible gateways do not expose /models. Use a minimal completion fallback.
            payload = {
                "model": settings.ai_model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 2,
                "temperature": 0,
                "stream": False,
            }
            response = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
            latency = int((time.perf_counter() - started) * 1000)
            if response.is_success:
                return {
                    "ok": True,
                    "message": "AI 接口连接成功，最小对话测试通过",
                    "status": response.status_code,
                    "latency_ms": latency,
                }
            return {
                "ok": False,
                "message": f"上游返回 {response.status_code}：{_safe_text(response, settings.ai_api_key)}",
                "status": response.status_code,
                "latency_ms": latency,
            }
    except httpx.TimeoutException:
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "message": "连接上游 AI 接口超时", "status": None, "latency_ms": latency}
    except httpx.HTTPError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "message": f"连接失败：{exc}", "status": None, "latency_ms": latency}
