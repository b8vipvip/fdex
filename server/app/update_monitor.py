from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.config import Settings

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_UNIT = re.compile(r"^fdex-admin-update-[0-9]+(?:\.service)?$")


def _run(args: list[str], timeout: float = 6.0) -> tuple[int, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, (result.stdout or result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _state_path(settings: Settings) -> Path:
    return Path(settings.app_dir) / "server" / "data" / "admin-update-status.json"


def _read_state(settings: Settings) -> dict[str, Any]:
    path = _state_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_update_unit() -> str:
    code, output = _run(
        ["systemctl", "list-units", "--all", "--plain", "--no-legend", "fdex-admin-update-*"],
        timeout=5,
    )
    if code != 0:
        return ""
    names: list[str] = []
    for line in output.splitlines():
        name = line.split(maxsplit=1)[0].strip() if line.strip() else ""
        if _UNIT.fullmatch(name):
            names.append(name)
    return max(names, key=_unit_number, default="")


def _unit_number(name: str) -> int:
    match = re.search(r"([0-9]+)(?:\.service)?$", name)
    return int(match.group(1)) if match else 0


def _unit_properties(unit: str) -> dict[str, str]:
    if not unit or not _UNIT.fullmatch(unit):
        return {}
    code, output = _run(
        [
            "systemctl",
            "show",
            unit,
            "--property=ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp",
        ],
        timeout=5,
    )
    if code != 0:
        return {}
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _unit_logs(unit: str, limit: int = 120) -> list[str]:
    if not unit or not _UNIT.fullmatch(unit):
        return []
    code, output = _run(
        ["journalctl", "-u", unit, "-n", str(max(20, min(limit, 300))), "--no-pager", "-o", "cat"],
        timeout=8,
    )
    if code != 0 and not output:
        return []
    lines = [_ANSI.sub("", line).rstrip() for line in output.splitlines()]
    return [line for line in lines if line][-limit:]


def update_task_status(settings: Settings) -> dict[str, Any]:
    state = _read_state(settings)
    unit = _latest_update_unit()
    properties = _unit_properties(unit)
    logs = _unit_logs(unit)

    status = str(state.get("status", "idle"))
    exit_status = properties.get("ExecMainStatus", "")
    result = properties.get("Result", "")
    active = properties.get("ActiveState", "")
    if unit:
        if result and result not in {"success", ""}:
            status = "failed"
        elif exit_status not in {"", "0"}:
            status = "failed"
        elif active in {"active", "activating", "reloading"} and status not in {"succeeded", "failed"}:
            status = "running"

    return {
        "status": status,
        "stage": str(state.get("stage", "")),
        "percent": max(0, min(int(state.get("percent", 0) or 0), 100)),
        "message": str(state.get("message", "")),
        "updated_at": str(state.get("updated_at", "")),
        "started_at": str(state.get("started_at", "")),
        "completed_at": str(state.get("completed_at", "")),
        "unit": unit,
        "active_state": active,
        "result": result,
        "exit_code": exit_status,
        "logs": logs,
    }
