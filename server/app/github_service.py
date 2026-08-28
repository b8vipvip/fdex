from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()
_CACHE_SECONDS = 300


def _transport_cache_key(settings: Settings) -> str:
    proxy = settings.fdex_github_http_proxy.strip()
    proxy_fingerprint = hashlib.sha256(proxy.encode("utf-8")).hexdigest()[:10] if proxy else "direct"
    token_mode = "auth" if settings.github_token.strip() else "anon"
    return f"{proxy_fingerprint}:{token_mode}"


async def _get_json(url: str, settings: Settings) -> dict[str, Any] | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "FDEX-Admin-Dashboard",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = settings.github_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = httpx.Timeout(
        connect=float(settings.fdex_github_connect_timeout_seconds),
        read=float(settings.fdex_github_read_timeout_seconds),
        write=float(settings.fdex_github_read_timeout_seconds),
        pool=float(settings.fdex_github_connect_timeout_seconds),
    )
    proxy = settings.fdex_github_http_proxy.strip()
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        # An explicit FDEX GitHub proxy is application-scoped and must not be mixed with
        # process/global proxy variables. Direct mode preserves the historical environment path.
        "trust_env": not bool(proxy),
    }
    if proxy:
        scheme = (urlsplit(proxy).scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise ValueError("GitHub 出站代理仅支持 http:// 或 https://")
        kwargs["proxy"] = proxy

    async with httpx.AsyncClient(**kwargs) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None


async def github_status(settings: Settings, force: bool = False) -> dict[str, Any]:
    owner, repo = settings.github_owner_repo
    cache_key = f"{owner}/{repo}:{_transport_cache_key(settings)}"
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and not force and now - cached[0] < _CACHE_SECONDS:
        return cached[1]

    async with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and not force and now - cached[0] < _CACHE_SECONDS:
            return cached[1]
        try:
            release, commit = await asyncio.gather(
                _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", settings),
                _get_json(f"https://api.github.com/repos/{owner}/{repo}/commits/main", settings),
            )
            result = {
                "ok": True,
                "error": "",
                "latest_release": {
                    "tag": release.get("tag_name", "") if release else "",
                    "name": release.get("name", "") if release else "",
                    "url": release.get("html_url", "") if release else "",
                    "published_at": release.get("published_at", "") if release else "",
                },
                "remote_commit": {
                    "sha": str(commit.get("sha", ""))[:7] if commit else "",
                    "url": commit.get("html_url", "") if commit else "",
                    "message": (
                        commit.get("commit", {}).get("message", "").splitlines()[0]
                        if commit
                        else ""
                    ),
                },
            }
        except (httpx.HTTPError, ValueError) as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "latest_release": {},
                "remote_commit": {},
            }
        _cache[cache_key] = (time.monotonic(), result)
        return result
