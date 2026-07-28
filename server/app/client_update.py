from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from app.config import fresh_settings
from app.release_sync import load_manifest, version_tuple

router = APIRouter(prefix="/api/client", tags=["client-update"])


@router.get("/update")
def client_update(current_version: str = Query(default="0.0.0", max_length=64)) -> dict:
    settings = fresh_settings()
    manifest = load_manifest()
    if not manifest:
        return {
            "available": False,
            "status": "waiting_for_server_cache",
            "current_version": current_version,
        }

    filename = str(manifest.get("filename") or "")
    apk_path = Path(settings.release_cache_dir) / filename
    if not filename or not apk_path.exists() or apk_path.stat().st_size <= 0:
        return {
            "available": False,
            "status": "cache_incomplete",
            "current_version": current_version,
        }

    latest_version = str(manifest.get("version") or "0.0.0")
    available = version_tuple(latest_version) > version_tuple(current_version)
    base_url = settings.public_base_url.rstrip("/")
    return {
        "available": available,
        "status": "ready",
        "current_version": current_version,
        "latest_version": latest_version,
        "tag_name": str(manifest.get("tag_name") or f"v{latest_version}"),
        "name": str(manifest.get("name") or f"FDEX {latest_version}"),
        "body": str(manifest.get("body") or ""),
        "published_at": str(manifest.get("published_at") or ""),
        "synced_at": str(manifest.get("synced_at") or ""),
        "sha256": str(manifest.get("sha256") or ""),
        "size": int(manifest.get("size") or apk_path.stat().st_size),
        "apk_url": f"{base_url}/downloads/{filename}",
    }
