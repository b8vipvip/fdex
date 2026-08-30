from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import httpx

from app.codex_process_isolation import (
    CodexProcessIsolationError,
    codex_process_isolation_status,
    terminate_all_codex_trees,
)
from app.codex_subagent_governance import codex_subagent_cli_overrides
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import read_env, write_env

_RELEASE_API = "https://api.github.com/repos/openai/codex/releases"
_RELEASE_DOWNLOAD_PREFIX = "https://github.com/openai/codex/releases/download/"
_ROOT = SERVER_DIR / "data" / "codex-runtimes"
_RELEASES = _ROOT / "releases"
_STATE = _ROOT / "state.json"
_TAG_RE = re.compile(r"^rust-v(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")
_DIGEST_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$")
_VERSION_OUTPUT_RE = re.compile(r"(?<!\d)(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)(?!\d)")
_MIN_ARCHIVE_BYTES = 8 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 180 * 1024 * 1024
_MAX_BINARY_BYTES = 420 * 1024 * 1024


class CodexRuntimeManagerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ensure_root() -> None:
    _RELEASES.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_ROOT, 0o700)
        os.chmod(_RELEASES, 0o700)
    except OSError:
        pass


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _arch_asset() -> tuple[str, str]:
    machine = platform.machine().strip().lower()
    if machine in {"x86_64", "amd64"}:
        target = "x86_64-unknown-linux-musl"
    elif machine in {"aarch64", "arm64"}:
        target = "aarch64-unknown-linux-musl"
    else:
        raise CodexRuntimeManagerError(f"unsupported Codex server architecture: {machine or 'unknown'}")
    return f"codex-{target}.tar.gz", f"codex-{target}"


def _http_client() -> httpx.Client:
    settings = fresh_settings()
    proxy = settings.fdex_github_http_proxy.strip() or None
    return httpx.Client(
        proxy=proxy,
        timeout=httpx.Timeout(
            connect=float(settings.fdex_github_connect_timeout_seconds),
            read=float(settings.fdex_github_read_timeout_seconds),
            write=30.0,
            pool=30.0,
        ),
        follow_redirects=True,
        trust_env=False,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "FDEX-Codex-Runtime-Manager",
        },
    )


def _release_url(tag: str | None) -> str:
    if tag is None:
        return f"{_RELEASE_API}/latest"
    if not _TAG_RE.fullmatch(tag):
        raise CodexRuntimeManagerError("invalid official Codex release tag")
    return f"{_RELEASE_API}/tags/{tag}"


def _validated_release(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CodexRuntimeManagerError("official Codex release metadata is invalid")
    if bool(raw.get("draft")) or bool(raw.get("prerelease")):
        raise CodexRuntimeManagerError("refusing draft/prerelease Codex runtime")
    tag = str(raw.get("tag_name") or "").strip()
    match = _TAG_RE.fullmatch(tag)
    if match is None:
        raise CodexRuntimeManagerError("official Codex release tag does not match rust-v<semver>")
    version = match.group("version")
    expected_asset, expected_binary = _arch_asset()
    assets = raw.get("assets")
    if not isinstance(assets, list):
        raise CodexRuntimeManagerError("official Codex release contains no asset list")
    candidates = [item for item in assets if isinstance(item, dict) and str(item.get("name") or "") == expected_asset]
    if len(candidates) != 1:
        raise CodexRuntimeManagerError(f"official Codex release must contain exactly one {expected_asset}")
    asset = candidates[0]
    url = str(asset.get("browser_download_url") or "").strip()
    exact_prefix = f"{_RELEASE_DOWNLOAD_PREFIX}{tag}/"
    if not url.startswith(exact_prefix) or url != exact_prefix + expected_asset:
        raise CodexRuntimeManagerError("Codex asset URL escaped the official openai/codex release path")
    digest_match = _DIGEST_RE.fullmatch(str(asset.get("digest") or "").strip().lower())
    if digest_match is None:
        raise CodexRuntimeManagerError("official Codex asset has no usable GitHub sha256 digest")
    try:
        size = int(asset.get("size") or 0)
        asset_id = int(asset.get("id") or 0)
    except (TypeError, ValueError) as exc:
        raise CodexRuntimeManagerError("official Codex asset metadata has invalid size/id") from exc
    if not _MIN_ARCHIVE_BYTES <= size <= _MAX_ARCHIVE_BYTES or asset_id <= 0:
        raise CodexRuntimeManagerError("official Codex asset size/id is outside the accepted bounds")
    return {
        "tag": tag,
        "version": version,
        "published_at": str(raw.get("published_at") or ""),
        "asset_id": asset_id,
        "asset_name": expected_asset,
        "binary_member": expected_binary,
        "asset_url": url,
        "asset_size": size,
        "asset_sha256": digest_match.group("digest"),
    }


def fetch_release(tag: str | None = None) -> dict[str, Any]:
    try:
        with _http_client() as client:
            response = client.get(_release_url(tag))
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CodexRuntimeManagerError(f"failed to read official OpenAI Codex release metadata: {type(exc).__name__}") from exc
    return _validated_release(data)


def _download_release(release: dict[str, Any], destination: Path) -> str:
    expected_size = int(release["asset_size"])
    expected_digest = str(release["asset_sha256"])
    digest = hashlib.sha256()
    received = 0
    try:
        with _http_client() as client, client.stream("GET", str(release["asset_url"])) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > _MAX_ARCHIVE_BYTES or received > expected_size + 1024:
                        raise CodexRuntimeManagerError("Codex download exceeded the signed release asset size")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
    except httpx.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise CodexRuntimeManagerError(f"failed to download official Codex runtime: {type(exc).__name__}") from exc
    if received != expected_size:
        destination.unlink(missing_ok=True)
        raise CodexRuntimeManagerError(f"Codex asset size mismatch: expected {expected_size}, received {received}")
    actual = digest.hexdigest()
    if actual != expected_digest:
        destination.unlink(missing_ok=True)
        raise CodexRuntimeManagerError("Codex asset SHA-256 does not match GitHub immutable release metadata")
    return actual


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(str(name or ""))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise CodexRuntimeManagerError("Codex archive contains an unsafe path")
    return path


def _copy_member(source: BinaryIO, destination: Path, expected_size: int) -> None:
    if not 1 <= expected_size <= _MAX_BINARY_BYTES:
        raise CodexRuntimeManagerError("Codex binary size is outside the accepted bounds")
    written = 0
    with destination.open("wb") as target:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            written += len(block)
            if written > expected_size or written > _MAX_BINARY_BYTES:
                raise CodexRuntimeManagerError("Codex archive member exceeded its declared size")
            target.write(block)
        target.flush()
        os.fsync(target.fileno())
    if written != expected_size:
        raise CodexRuntimeManagerError("Codex binary archive member is truncated")


def _extract_verified_binary(archive: Path, expected_member: str, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            candidates: list[tarfile.TarInfo] = []
            for member in bundle.getmembers():
                safe = _safe_member_path(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise CodexRuntimeManagerError("Codex archive contains links/devices and was rejected")
                if member.isfile() and safe.name == expected_member:
                    candidates.append(member)
            if len(candidates) != 1:
                raise CodexRuntimeManagerError(f"Codex archive must contain exactly one {expected_member} executable")
            member = candidates[0]
            source = bundle.extractfile(member)
            if source is None:
                raise CodexRuntimeManagerError("Codex executable could not be read from archive")
            with source:
                _copy_member(source, destination, int(member.size))
    except (tarfile.TarError, OSError) as exc:
        raise CodexRuntimeManagerError(f"invalid Codex release archive: {type(exc).__name__}") from exc
    os.chmod(destination, 0o755)


def _run_binary(binary: Path, args: list[str], timeout: float = 15.0) -> tuple[int, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CI": "true",
    }
    try:
        result = subprocess.run(
            [str(binary), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return int(result.returncode), f"{result.stdout}\n{result.stderr}".strip()[:8000]


def validate_runtime_binary(binary: Path, expected_version: str | None = None) -> dict[str, str]:
    resolved = binary.expanduser().resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise CodexRuntimeManagerError(f"Codex runtime is not executable: {resolved}")
    code, version_output = _run_binary(resolved, ["--version"], timeout=10.0)
    if code != 0:
        raise CodexRuntimeManagerError(f"Codex --version failed: {version_output[:600]}")
    versions = [match.group("version") for match in _VERSION_OUTPUT_RE.finditer(version_output)]
    detected = versions[0] if versions else ""
    if expected_version and expected_version not in versions:
        raise CodexRuntimeManagerError(
            f"Codex runtime version mismatch: expected {expected_version}, got {detected or 'unknown'}"
        )

    governance_args: list[str] = []
    for override in codex_subagent_cli_overrides():
        governance_args.extend(("--config", override))
    code, help_output = _run_binary(resolved, [*governance_args, "app-server", "--help"], timeout=15.0)
    if code != 0 or not any(marker in help_output.lower() for marker in ("app-server", "stdio", "listen")):
        raise CodexRuntimeManagerError(f"Codex app-server/governance validation failed: {help_output[:1200]}")
    return {
        "path": str(resolved),
        "version": expected_version or detected,
        "version_output": version_output.splitlines()[0][:240] if version_output else "",
        "binary_sha256": _sha256_file(resolved),
    }


def _manifest_path(version: str) -> Path:
    return (_RELEASES / version / "manifest.json").resolve()


def _binary_path(version: str) -> Path:
    return (_RELEASES / version / "codex").resolve()


def _managed_path(path: Path) -> bool:
    root = _RELEASES.resolve()
    resolved = path.expanduser().resolve()
    return resolved == root or root in resolved.parents


def verify_installed_release(version: str) -> dict[str, Any]:
    if not _TAG_RE.fullmatch(f"rust-v{version}"):
        raise CodexRuntimeManagerError("invalid installed Codex version")
    manifest = _load_json(_manifest_path(version))
    binary = _binary_path(version)
    if not manifest or str(manifest.get("version") or "") != version:
        raise CodexRuntimeManagerError(f"managed Codex {version} has no valid manifest")
    expected = str(manifest.get("binary_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or _sha256_file(binary) != expected:
        raise CodexRuntimeManagerError(f"managed Codex {version} binary hash verification failed")
    validation = validate_runtime_binary(binary, version)
    return {**manifest, **validation}


def install_release(release: dict[str, Any]) -> dict[str, Any]:
    _ensure_root()
    version = str(release["version"])
    target = _RELEASES / version
    if target.exists():
        return verify_installed_release(version)

    staging = Path(tempfile.mkdtemp(prefix=f".stage-{version}-", dir=str(_ROOT)))
    try:
        archive = staging / str(release["asset_name"])
        downloaded_sha = _download_release(release, archive)
        binary = staging / "codex"
        _extract_verified_binary(archive, str(release["binary_member"]), binary)
        validation = validate_runtime_binary(binary, version)
        manifest = {
            "source": "https://github.com/openai/codex",
            "tag": str(release["tag"]),
            "version": version,
            "published_at": str(release.get("published_at") or ""),
            "asset_id": int(release["asset_id"]),
            "asset_name": str(release["asset_name"]),
            "asset_size": int(release["asset_size"]),
            "asset_sha256": downloaded_sha,
            "binary_sha256": str(validation["binary_sha256"]),
            "installed_at": _now(),
        }
        archive.unlink(missing_ok=True)
        _atomic_json(staging / "manifest.json", manifest)
        if target.exists():
            shutil.rmtree(staging, ignore_errors=True)
            return verify_installed_release(version)
        os.replace(staging, target)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        return verify_installed_release(version)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _state() -> dict[str, Any]:
    _ensure_root()
    return _load_json(_STATE)


def _activate_pin(pin: str, current: dict[str, Any] | None, *, action: str) -> dict[str, Any]:
    isolation = codex_process_isolation_status()
    if not bool(isolation.get("enforced")):
        raise CodexRuntimeManagerError(
            f"Codex Runtime activation requires Phase 7.32 cgroup isolation: {isolation.get('reason') or 'unavailable'}"
        )
    values = read_env()
    old_pin = str(values.get("FDEX_AGENT_CODEX_BIN") or "").strip()
    if old_pin == pin:
        return runtime_manager_status()
    try:
        terminate_all_codex_trees()
    except CodexProcessIsolationError as exc:
        raise CodexRuntimeManagerError(f"cannot switch Codex Runtime while old process trees remain: {exc}") from exc
    write_env({"FDEX_AGENT_CODEX_BIN": pin})
    get_settings.cache_clear()
    state = _state()
    state.update(
        {
            "active_pin": pin,
            "active": current or {},
            "previous_pin": old_pin,
            "updated_at": _now(),
            "last_action": action,
        }
    )
    _atomic_json(_STATE, state)
    return runtime_manager_status()


def upgrade_runtime(tag: str | None = None) -> dict[str, Any]:
    release = fetch_release(tag)
    installed = install_release(release)
    binary = Path(str(installed["path"]))
    return _activate_pin(
        str(binary),
        {
            "tag": str(installed.get("tag") or release["tag"]),
            "version": str(installed["version"]),
            "path": str(binary),
            "binary_sha256": str(installed["binary_sha256"]),
        },
        action="upgrade",
    )


def _validate_pin(pin: str) -> dict[str, str]:
    clean = pin.strip()
    if clean:
        binary = Path(clean).expanduser().resolve()
        if _managed_path(binary):
            try:
                version = binary.parent.name
                verified = verify_installed_release(version)
                return {"path": str(binary), "version": str(verified.get("version") or version)}
            except CodexRuntimeManagerError:
                raise
        return validate_runtime_binary(binary)
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError as exc:
        raise CodexRuntimeManagerError("cannot rollback to fallback: bundled official Codex Runtime is unavailable") from exc
    return validate_runtime_binary(Path(bundled_codex_path()))


def rollback_runtime() -> dict[str, Any]:
    state = _state()
    if "previous_pin" not in state:
        raise CodexRuntimeManagerError("no previous Codex Runtime pin is available for rollback")
    previous = str(state.get("previous_pin") or "").strip()
    validation = _validate_pin(previous)
    values = read_env()
    current_pin = str(values.get("FDEX_AGENT_CODEX_BIN") or "").strip()
    current = {
        "tag": "",
        "version": str(validation.get("version") or "fallback"),
        "path": previous,
        "binary_sha256": _sha256_file(Path(validation["path"])) if validation.get("path") else "",
    }
    result = _activate_pin(previous, current, action="rollback")
    # Make rollback reversible: _activate_pin captured the former current pin as previous_pin.
    state = _state()
    if str(state.get("previous_pin") or "") != current_pin:
        state["previous_pin"] = current_pin
        _atomic_json(_STATE, state)
    return result


def installed_releases() -> list[dict[str, Any]]:
    _ensure_root()
    rows: list[dict[str, Any]] = []
    for child in sorted(_RELEASES.iterdir(), reverse=True):
        if not child.is_dir() or not _TAG_RE.fullmatch(f"rust-v{child.name}"):
            continue
        manifest = _load_json(child / "manifest.json")
        binary = child / "codex"
        if not manifest or not binary.is_file():
            continue
        rows.append(
            {
                "version": child.name,
                "path": str(binary.resolve()),
                "binary_sha256": str(manifest.get("binary_sha256") or ""),
                "installed_at": str(manifest.get("installed_at") or ""),
                "tag": str(manifest.get("tag") or ""),
            }
        )
    return rows


def runtime_manager_status() -> dict[str, Any]:
    values = read_env()
    pin = str(values.get("FDEX_AGENT_CODEX_BIN") or "").strip()
    state = _state()
    isolation = codex_process_isolation_status()
    active_validation: dict[str, str] = {}
    active_error = ""
    if pin:
        try:
            active_validation = _validate_pin(pin)
        except Exception as exc:
            active_error = str(exc)[:1200]
    return {
        "root": str(_ROOT.resolve()),
        "active_pin": pin,
        "active_version": str(active_validation.get("version") or ""),
        "active_path": str(active_validation.get("path") or pin),
        "active_valid": bool(active_validation) if pin else True,
        "active_error": active_error,
        "previous_pin": str(state.get("previous_pin") or ""),
        "rollback_available": "previous_pin" in state,
        "last_action": str(state.get("last_action") or ""),
        "updated_at": str(state.get("updated_at") or ""),
        "installed": installed_releases(),
        "process_isolation": isolation,
        "managed_runtime_selected": bool(pin and _managed_path(Path(pin))),
    }
