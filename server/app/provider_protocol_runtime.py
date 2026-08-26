from __future__ import annotations

from time import perf_counter
from typing import Any, Iterable

import httpx

from app.multimodal_service import RouteResult, TASK_TEXT, TASK_VISION, build_chat_messages
from app.provider_manager import api_roots, text_model_candidates

_PROTOCOLS = ("chat", "responses", "legacy")


def _protocols(provider: dict[str, Any]) -> list[str]:
    raw = provider.get("protocol_order") or _PROTOCOLS
    out = [str(item).strip().lower() for item in raw if str(item).strip().lower() in _PROTOCOLS]
    return list(dict.fromkeys(out)) or list(_PROTOCOLS)


def _safe_error(exc: BaseException | str, api_key: str = "") -> str:
    if isinstance(exc, BaseException):
        text = str(exc).strip() or type(exc).__name__
    else:
        text = str(exc).strip()
    text = text.replace("\r", " ").replace("\n", " ")
    if api_key:
        text = text.replace(api_key, "***")
    return text[:360]


def _extract_chat(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    value = message.get("content")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or item.get("content") or "")
            for item in value
            if isinstance(item, dict)
        ).strip()
    return ""


def _extract_responses(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = data.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for piece in content:
            if not isinstance(piece, dict):
                continue
            text = piece.get("text") or piece.get("content")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _extract_legacy(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    value = choices[0].get("text")
    return value.strip() if isinstance(value, str) else ""


def _request_spec(
    protocol: str,
    *,
    model: str,
    system: str | None,
    prompt: str,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    if protocol == "responses":
        body: dict[str, Any] = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
        }
        if system and system.strip():
            body["instructions"] = system.strip()
        return "/responses", body
    if protocol == "legacy":
        combined = prompt
        if system and system.strip():
            combined = f"{system.strip()}\n\n{prompt}"
        return "/completions", {
            "model": model,
            "prompt": combined,
            "max_tokens": max_tokens,
            "temperature": 0.5,
            "stream": False,
        }
    return "/chat/completions", {
        "model": model,
        "messages": build_chat_messages(system, prompt),
        "temperature": 0.5,
        "max_tokens": max_tokens,
        "stream": False,
    }


def _extract(protocol: str, data: dict[str, Any]) -> str:
    if protocol == "responses":
        return _extract_responses(data)
    if protocol == "legacy":
        return _extract_legacy(data)
    return _extract_chat(data)


async def route_text_protocols(
    *,
    system: str | None,
    prompt: str,
    max_tokens: int,
    images: list[dict[str, str]] | None = None,
    providers: Iterable[dict[str, Any]] | None = None,
) -> RouteResult:
    """Route text through the provider's configured protocol order.

    Vision stays on Chat Completions because the existing multimodal payload is already validated
    for that protocol. Plain text now genuinely honors chat/responses/legacy instead of merely
    storing protocol_order in the database.
    """
    from app.provider_manager import provider_store

    started_all = perf_counter()
    errors: list[str] = []
    vision = bool(images)
    candidates = list(providers) if providers is not None else provider_store().list(enabled_only=True, include_secret=True)

    for provider in candidates:
        provider_name = str(provider.get("name") or "供应商")
        api_key = str(provider.get("api_key") or "")
        models = text_model_candidates(provider, vision=vision)
        if not api_key or not models:
            errors.append(f"{provider_name}：API Key 或 {'视觉/文本' if vision else '文本'}模型未配置")
            continue
        protocols = ["chat"] if vision else _protocols(provider)
        timeout_seconds = float(provider.get("timeout_seconds") or 60)
        timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FDEX-ProviderRuntime/1.0",
        }

        for model in models:
            for protocol in protocols:
                suffix, body = _request_spec(
                    protocol,
                    model=model,
                    system=system,
                    prompt=prompt,
                    max_tokens=max_tokens,
                )
                roots = api_roots(str(provider.get("base_url") or ""))
                for root_index, root in enumerate(roots):
                    url = root.rstrip("/") + suffix
                    try:
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                            response = await client.post(url, headers=headers, json=body)
                    except httpx.TimeoutException as exc:
                        errors.append(f"{provider_name} / {model} / {protocol}：{_safe_error(exc)}")
                        # A timeout on the canonical root is a transport/upstream failure; trying a
                        # second guessed root only hides the real cause and can double the wait.
                        break
                    except httpx.HTTPError as exc:
                        errors.append(f"{provider_name} / {model} / {protocol}：{_safe_error(exc)}")
                        break

                    if not response.is_success:
                        errors.append(
                            f"{provider_name} / {model} / {protocol}：HTTP {response.status_code} "
                            f"{_safe_error(response.text, api_key)}"
                        )
                        # Only 404/405 plausibly mean the alternate /v1 root is useful.
                        if response.status_code in {404, 405} and root_index + 1 < len(roots):
                            continue
                        break
                    try:
                        data = response.json()
                    except ValueError as exc:
                        errors.append(f"{provider_name} / {model} / {protocol}：JSON 解析失败 {_safe_error(exc)}")
                        break
                    content = _extract(protocol, data) if isinstance(data, dict) else ""
                    if not content:
                        errors.append(f"{provider_name} / {model} / {protocol}：上游未返回正文")
                        break
                    return RouteResult(
                        ok=True,
                        task=TASK_VISION if vision else TASK_TEXT,
                        content=content,
                        provider=provider_name,
                        provider_id=int(provider["id"]) if provider.get("id") is not None else None,
                        model=model,
                        latency_ms=int((perf_counter() - started_all) * 1000),
                        errors=errors,
                    )

    return RouteResult(
        ok=False,
        task=TASK_VISION if vision else TASK_TEXT,
        latency_ms=int((perf_counter() - started_all) * 1000),
        errors=errors,
    )


def install_provider_protocol_runtime() -> None:
    """Install protocol-aware text routing into already imported runtime modules."""
    import app.client_ai as client_ai_module
    import app.multimodal_service as multimodal_module

    client_ai_module.route_text = route_text_protocols
    multimodal_module.route_text = route_text_protocols
