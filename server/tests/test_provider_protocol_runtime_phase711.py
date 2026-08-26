from __future__ import annotations

import httpx
import pytest

from app import provider_protocol_runtime as runtime


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, headers, json):
        request = httpx.Request("POST", url)
        if url.endswith("/chat/completions"):
            return httpx.Response(404, request=request, json={"detail": "Not Found"})
        if url.endswith("/responses"):
            return httpx.Response(200, request=request, json={"output_text": "responses-ok"})
        raise AssertionError(url)


@pytest.mark.asyncio
async def test_text_runtime_honors_protocol_order_and_falls_back_to_responses(monkeypatch) -> None:
    monkeypatch.setattr(runtime.httpx, "AsyncClient", _FakeClient)
    provider = {
        "id": 7,
        "name": "test",
        "base_url": "https://example.invalid/v1",
        "api_key": "secret",
        "main_text_model": "model-x",
        "backup_text_models": [],
        "protocol_order": ["chat", "responses", "legacy"],
        "timeout_seconds": 5,
    }
    result = await runtime.route_text_protocols(
        system="system",
        prompt="hello",
        max_tokens=64,
        providers=[provider],
    )
    assert result.ok is True
    assert result.content == "responses-ok"
    assert result.model == "model-x"
    assert any("/ chat：HTTP 404" in error for error in result.errors)


class _TimeoutClient(_FakeClient):
    async def post(self, url: str, *, headers, json):
        raise httpx.ReadTimeout("", request=httpx.Request("POST", url))


@pytest.mark.asyncio
async def test_timeout_error_is_not_blank_and_does_not_try_guessed_root(monkeypatch) -> None:
    calls: list[str] = []

    class Client(_TimeoutClient):
        async def post(self, url: str, *, headers, json):
            calls.append(url)
            return await super().post(url, headers=headers, json=json)

    monkeypatch.setattr(runtime.httpx, "AsyncClient", Client)
    provider = {
        "id": 8,
        "name": "slow",
        "base_url": "https://example.invalid/v1",
        "api_key": "secret",
        "main_text_model": "model-y",
        "backup_text_models": [],
        "protocol_order": ["chat"],
        "timeout_seconds": 5,
    }
    result = await runtime.route_text_protocols(system=None, prompt="hello", max_tokens=64, providers=[provider])
    assert result.ok is False
    assert len(calls) == 1
    assert "ReadTimeout" in result.errors[0]


def test_responses_extractor_supports_output_content_shape() -> None:
    assert runtime._extract_responses(
        {"output": [{"content": [{"type": "output_text", "text": "hello"}]}]}
    ) == "hello"
