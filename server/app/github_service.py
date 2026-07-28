from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()
_CACHE_SECONDS = 300


async def _get_json(url: str) -> dict[str, Any] | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "FDEX-Admin-Dashboard",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None


async def github_status(settings: Settings, force: bool = False) -> dict[str, Any]:
    owner, repo = settings.github_owner_repo
    cache_key = f"{owner}/{repo}"
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
                _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest"),
                _get_json(f"https://api.github.com/repos/{owner}/{repo}/commits/main"),
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
