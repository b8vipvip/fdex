from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Query

from app.config import fresh_settings
from app.release_sync import (
    latest_release_tag,
    load_manifest,
    normalize_version,
    sync_latest_release,
    version_tuple,
)

router = APIRouter(prefix="/api/client", tags=["client-update"])


@router.get("/update")
def client_update(
    background_tasks: BackgroundTasks,
    current_version: str = Query(default="0.0.0", max_length=64),
) -> dict:
    settings = fresh_settings()
    manifest = load_manifest()
    if not manifest:
        background_tasks.add_task(sync_latest_release)
        return {
            "available": False,
            "status": "waiting_for_server_cache",
            "strategy": "latest_only",
            "current_version": current_version,
        }

    cached_version = str(manifest.get("version") or "0.0.0")
    cached_tag = str(manifest.get("tag_name") or f"v{cached_version}")

    # Never offer the cached APK until we have confirmed that it is still the
    # latest stable GitHub Release. Otherwise a client several versions behind
    # could install vN, restart, then immediately install vN+1 again.
    try:
        remote_tag = latest_release_tag(timeout_seconds=6.0)
        remote_version = normalize_version(remote_tag)
    except Exception:
        return {
            "available": False,
            "status": "latest_version_unverified",
            "strategy": "latest_only",
            "current_version": current_version,
            "cached_version": cached_version,
        }

    if version_tuple(remote_version) <= version_tuple(current_version):
        return {
            "available": False,
            "status": "ready",
            "strategy": "latest_only",
            "current_version": current_version,
            "latest_version": remote_version,
            "tag_name": remote_tag,
        }

    if version_tuple(remote_version) > version_tuple(cached_version):
        background_tasks.add_task(sync_latest_release)
        return {
            "available": False,
            "status": "waiting_for_server_cache",
            "strategy": "latest_only",
            "current_version": current_version,
            "cached_version": cached_version,
            "latest_version": remote_version,
            "tag_name": remote_tag,
        }

    # If the cache somehow points to a version newer than GitHub's current
    # latest release, do not install that stale/deleted artifact either.
    if version_tuple(cached_version) != version_tuple(remote_version):
        background_tasks.add_task(sync_latest_release)
        return {
            "available": False,
            "status": "waiting_for_server_cache",
            "strategy": "latest_only",
            "current_version": current_version,
            "cached_version": cached_version,
            "latest_version": remote_version,
            "tag_name": remote_tag,
        }

    filename = str(manifest.get("filename") or "")
    apk_path = Path(settings.release_cache_dir) / filename
    if not filename or not apk_path.exists() or apk_path.stat().st_size <= 0:
        background_tasks.add_task(sync_latest_release)
        return {
            "available": False,
            "status": "cache_incomplete",
            "strategy": "latest_only",
            "current_version": current_version,
            "latest_version": remote_version,
            "tag_name": remote_tag,
        }

    available = version_tuple(remote_version) > version_tuple(current_version)
    base_url = settings.public_base_url.rstrip("/")
    return {
        "available": available,
        "status": "ready",
        "strategy": "latest_only",
        "current_version": current_version,
        "latest_version": remote_version,
        "tag_name": remote_tag or cached_tag,
        "name": str(manifest.get("name") or f"FDEX {remote_version}"),
        "body": str(manifest.get("body") or ""),
        "published_at": str(manifest.get("published_at") or ""),
        "synced_at": str(manifest.get("synced_at") or ""),
        "sha256": str(manifest.get("sha256") or ""),
        "size": int(manifest.get("size") or apk_path.stat().st_size),
        "apk_url": f"{base_url}/downloads/{filename}",
    }
