from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import fresh_settings

_VERSION_RE = re.compile(r"^(?:v)?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$", re.IGNORECASE)


def normalize_version(value: str) -> str:
    return value.strip().lstrip("vV")


def version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(value.strip())
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def load_manifest() -> dict | None:
    settings = fresh_settings()
    path = Path(settings.release_cache_dir) / "manifest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _request_json(url: str, token: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "FDEX-Server-Release-Sync/1.0",
    }
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, destination: Path) -> tuple[str, int]:
    request = Request(url, headers={"User-Agent": "FDEX-Server-Release-Sync/1.0"})
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".fdex-apk-", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as output, urlopen(request, timeout=120) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return digest.hexdigest(), size


def sync_latest_release() -> dict:
    settings = fresh_settings()
    owner, repo = settings.github_owner_repo
    release_dir = Path(settings.release_cache_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

    release = _request_json(endpoint, settings.github_token)
    tag_name = str(release.get("tag_name") or "").strip()
    if not tag_name:
        raise RuntimeError("GitHub latest release 缺少 tag_name")

    assets = release.get("assets") or []
    apk = next(
        (
            item
            for item in assets
            if isinstance(item, dict)
            and str(item.get("name") or "").lower().endswith(".apk")
            and str(item.get("browser_download_url") or "").strip()
        ),
        None,
    )
    if apk is None:
        raise RuntimeError(f"GitHub Release {tag_name} 尚未包含 APK")

    version = normalize_version(tag_name)
    safe_version = re.sub(r"[^0-9A-Za-z._-]", "-", version)
    filename = f"fdex-{safe_version}.apk"
    destination = release_dir / filename
    existing = load_manifest()

    same_release = bool(
        existing
        and existing.get("tag_name") == tag_name
        and existing.get("filename") == filename
        and destination.exists()
        and destination.stat().st_size > 0
    )

    if same_release:
        print(f"FDEX APK 已是最新缓存：{tag_name} -> {destination}")
        return existing

    sha256, size = _download(str(apk["browser_download_url"]), destination)
    synced_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "tag_name": tag_name,
        "version": version,
        "name": str(release.get("name") or tag_name),
        "body": str(release.get("body") or ""),
        "published_at": str(release.get("published_at") or ""),
        "source_url": str(release.get("html_url") or ""),
        "filename": filename,
        "sha256": sha256,
        "size": size,
        "synced_at": synced_at,
    }

    manifest_path = release_dir / "manifest.json"
    fd, temp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=str(release_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, manifest_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    for old_apk in release_dir.glob("fdex-*.apk"):
        if old_apk.name != filename:
            try:
                old_apk.unlink()
            except OSError:
                pass

    print(f"已缓存 FDEX APK：{tag_name}，{size} bytes，sha256={sha256}")
    return manifest


def main() -> int:
    try:
        sync_latest_release()
        return 0
    except HTTPError as error:
        print(f"GitHub HTTP 错误：{error.code} {error.reason}", flush=True)
    except URLError as error:
        print(f"GitHub 连接失败：{error.reason}", flush=True)
    except Exception as error:  # keep timer failure isolated from the web service
        print(f"APK 同步失败：{error}", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
