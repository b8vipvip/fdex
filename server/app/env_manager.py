from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from app.config import ENV_FILE, SERVER_DIR

BACKUP_DIR = SERVER_DIR / "data" / "backups"

EDITABLE_KEYS = (
    "APP_NAME",
    "APP_VERSION",
    "ENVIRONMENT",
    "PUBLIC_BASE_URL",
    "API_PREFIX",
    "CORS_ORIGINS",
    "FDEX_HOST",
    "FDEX_PORT",
    "FDEX_WORKERS",
    "AI_PROVIDER",
    "AI_BASE_URL",
    "AI_API_KEY",
    "AI_MODEL",
    "AI_TIMEOUT_SECONDS",
    "ADMIN_USERNAME",
    "ADMIN_COOKIE_SECURE",
    "ADMIN_SESSION_HOURS",
)


def _decode_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        if value[0] == '"':
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value[1:-1]
    return value


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _decode_value(raw)
    return values


def _encode_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() for char in value) or any(char in value for char in "#'\""):
        return json.dumps(value, ensure_ascii=False)
    return value


def backup_env(path: Path = ENV_FILE) -> Path | None:
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUP_DIR / f"env-{stamp}.backup"
    suffix = 1
    while target.exists():
        target = BACKUP_DIR / f"env-{stamp}-{suffix}.backup"
        suffix += 1
    shutil.copy2(path, target)
    os.chmod(target, 0o600)
    return target


def write_env(updates: Mapping[str, str], path: Path = ENV_FILE) -> Path | None:
    """Atomically update selected keys while preserving comments and unknown settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_env(path)
    remaining = dict(updates)
    output: list[str] = []

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    output.append(f"{key}={_encode_value(str(remaining.pop(key)))}")
                    continue
            output.append(line)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Updated by FDEX admin dashboard")
        for key, value in remaining.items():
            output.append(f"{key}={_encode_value(str(value))}")

    content = "\n".join(output).rstrip() + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".env.", dir=str(path.parent), text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
    return backup


def mask_secret(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * min(12, len(value) - 6)}{value[-3:]}"
