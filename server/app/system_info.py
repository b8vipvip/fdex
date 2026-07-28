from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings

_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9_.@-]+$")


def _run(args: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or result.stderr).strip()
        return result.returncode, output
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _system_uptime() -> str:
    try:
        seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return "未知"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}天 {hours}小时 {minutes}分钟"


def _memory_summary() -> str:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        total = values["MemTotal"]
        available = values.get("MemAvailable", values.get("MemFree", 0))
        used = max(total - available, 0)
        return f"{_format_bytes(used)} / {_format_bytes(total)}"
    except (OSError, ValueError, KeyError):
        return "未知"


def service_status(settings: Settings) -> dict[str, str]:
    if not _SAFE_SERVICE.fullmatch(settings.service_name):
        return {"state": "invalid", "detail": "服务名称无效"}
    code, state = _run(["systemctl", "is-active", settings.service_name], timeout=3)
    enabled_code, enabled = _run(["systemctl", "is-enabled", settings.service_name], timeout=3)
    return {
        "state": state or ("active" if code == 0 else "unknown"),
        "enabled": enabled or ("enabled" if enabled_code == 0 else "unknown"),
    }


def git_info(settings: Settings) -> dict[str, str | bool]:
    app_dir = Path(settings.app_dir)
    if not (app_dir / ".git").exists():
        return {"sha": "未知", "branch": "未知", "dirty": False}
    _, sha = _run(["git", "-C", str(app_dir), "rev-parse", "--short", "HEAD"])
    _, branch = _run(["git", "-C", str(app_dir), "branch", "--show-current"])
    _, dirty_output = _run(["git", "-C", str(app_dir), "status", "--porcelain"])
    return {"sha": sha or "未知", "branch": branch or "未知", "dirty": bool(dirty_output)}


def system_snapshot(settings: Settings) -> dict[str, Any]:
    app_dir = Path(settings.app_dir)
    try:
        disk = shutil.disk_usage(app_dir if app_dir.exists() else "/")
        disk_text = f"{_format_bytes(disk.used)} / {_format_bytes(disk.total)}"
    except OSError:
        disk_text = "未知"
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "pid": os.getpid(),
        "uptime": _system_uptime(),
        "memory": _memory_summary(),
        "disk": disk_text,
        "port": f"{settings.fdex_host}:{settings.fdex_port}",
        "workers": settings.fdex_workers,
        "service": service_status(settings),
        "git": git_info(settings),
    }


def service_logs(settings: Settings, lines: int | None = None) -> str:
    if not _SAFE_SERVICE.fullmatch(settings.service_name):
        return "服务名称无效"
    count = max(20, min(lines or settings.admin_log_lines, 2000))
    _, output = _run(
        [
            "journalctl",
            "-u",
            settings.service_name,
            "-n",
            str(count),
            "--no-pager",
            "-o",
            "short-iso",
        ],
        timeout=12,
    )
    return output or "暂无日志"


def schedule_service_restart(settings: Settings) -> str:
    if not _SAFE_SERVICE.fullmatch(settings.service_name):
        raise ValueError("服务名称无效")
    unit = f"fdex-admin-restart-{int(time.time())}"
    args = [
        "systemd-run",
        f"--unit={unit}",
        "--on-active=2s",
        "/bin/systemctl",
        "restart",
        settings.service_name,
    ]
    code, output = _run(args, timeout=8)
    if code != 0:
        raise RuntimeError(output or "无法安排服务重启")
    return output or unit


def schedule_server_update(settings: Settings) -> str:
    script = Path(settings.app_dir) / "scripts" / "update_server.sh"
    if not script.is_file():
        raise FileNotFoundError(f"更新脚本不存在：{script}")
    unit = f"fdex-admin-update-{int(time.time())}"
    args = [
        "systemd-run",
        f"--unit={unit}",
        "/bin/bash",
        str(script),
    ]
    code, output = _run(args, timeout=8)
    if code != 0:
        raise RuntimeError(output or "无法启动更新任务")
    return output or unit
