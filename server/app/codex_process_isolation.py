from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.config import Settings, fresh_settings

_REQUIRED_CONTROLLERS = {"cpu", "memory", "pids"}
_SAFE_UNIT = re.compile(r"^[A-Za-z0-9_.@:-]{1,180}$")


class CodexProcessIsolationError(RuntimeError):
    pass


def _minimal_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }


def _run(args: Iterable[str], *, timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            tuple(str(item) for item in args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(0.5, timeout),
            check=False,
            env=_minimal_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    return int(result.returncode), text[:4000]


def _parent_unit(settings: Settings) -> str:
    raw = str(settings.service_name or "fdex").strip()
    unit = raw if raw.endswith(".service") else f"{raw}.service"
    if not _SAFE_UNIT.fullmatch(unit):
        raise CodexProcessIsolationError("FDEX service_name is not a safe systemd unit name")
    return unit


def _controllers() -> set[str]:
    path = Path("/sys/fs/cgroup/cgroup.controllers")
    try:
        return {item.strip() for item in path.read_text(encoding="utf-8").split() if item.strip()}
    except OSError:
        return set()


def codex_process_isolation_status(settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or fresh_settings()
    required = bool(getattr(cfg, "fdex_agent_process_isolation_required", True))
    parent_unit = ""
    try:
        parent_unit = _parent_unit(cfg)
    except CodexProcessIsolationError as exc:
        return {
            "ready": False,
            "enforced": False,
            "required": required,
            "reason": str(exc),
            "parent_unit": "",
            "controllers": [],
        }

    if platform.system().lower() != "linux":
        reason = "Codex process-tree isolation requires Linux cgroup v2"
        return {
            "ready": not required,
            "enforced": False,
            "required": required,
            "reason": reason,
            "parent_unit": parent_unit,
            "controllers": [],
        }
    if not shutil.which("systemd-run") or not shutil.which("systemctl"):
        reason = "systemd-run/systemctl are unavailable"
        return {
            "ready": not required,
            "enforced": False,
            "required": required,
            "reason": reason,
            "parent_unit": parent_unit,
            "controllers": [],
        }

    controllers = _controllers()
    missing = sorted(_REQUIRED_CONTROLLERS - controllers)
    if missing:
        reason = "cgroup v2 controllers unavailable: " + ", ".join(missing)
        return {
            "ready": not required,
            "enforced": False,
            "required": required,
            "reason": reason,
            "parent_unit": parent_unit,
            "controllers": sorted(controllers),
        }

    code, output = _run(("systemctl", "is-active", parent_unit), timeout=3.0)
    if code != 0 or output.strip() != "active":
        reason = f"parent FDEX systemd unit is not active: {parent_unit} ({output or 'inactive'})"
        return {
            "ready": not required,
            "enforced": False,
            "required": required,
            "reason": reason,
            "parent_unit": parent_unit,
            "controllers": sorted(controllers),
        }

    return {
        "ready": True,
        "enforced": True,
        "required": required,
        "reason": "",
        "parent_unit": parent_unit,
        "controllers": sorted(controllers),
        "memory_mb": int(cfg.fdex_agent_sandbox_memory_mb),
        "cpu_percent": int(cfg.fdex_agent_sandbox_cpu_percent),
        "pids_max": int(cfg.fdex_agent_sandbox_pids_max),
    }


def _unit_name(owner_id: str, isolation_key: str) -> str:
    owner = str(owner_id or "").strip()
    key = str(isolation_key or "").strip()
    if not owner or not key:
        raise CodexProcessIsolationError("Codex isolation requires owner_id and isolation_key")
    digest = hashlib.sha256(f"{owner}\0{key}".encode("utf-8")).hexdigest()[:32]
    return f"fdex-codex-{digest}.service"


def _escape_systemd_exec_arg(value: str) -> str:
    # systemd expands both $ variables and % specifiers in transient ExecStart arguments.
    # Doubling keeps the original bytes when PID 1 executes the official Codex wrapper.
    return str(value).replace("$", "$$").replace("%", "%%")


@dataclass(frozen=True, slots=True)
class CodexProcessIsolationSpec:
    unit_name: str
    parent_unit: str
    memory_mb: int
    cpu_percent: int
    pids_max: int
    stop_grace_seconds: float

    def _unit_active(self) -> bool:
        code, output = _run(("systemctl", "is-active", self.unit_name), timeout=2.0)
        return code == 0 and output.strip() == "active"

    def terminate_tree(self) -> None:
        """Stop every process in the transient Codex service cgroup.

        `systemctl stop` applies KillMode=control-group. If the normal stop path itself wedges,
        FDEX escalates to an explicit SIGKILL for all members and verifies the unit is no longer
        active before returning.
        """
        if not self._unit_active():
            return
        _run(("systemctl", "stop", self.unit_name), timeout=self.stop_grace_seconds + 3.0)
        deadline = time.monotonic() + self.stop_grace_seconds
        while self._unit_active() and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._unit_active():
            _run(
                (
                    "systemctl",
                    "kill",
                    "--kill-who=all",
                    "--signal=SIGKILL",
                    self.unit_name,
                ),
                timeout=3.0,
            )
        deadline = time.monotonic() + 2.0
        while self._unit_active() and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._unit_active():
            raise CodexProcessIsolationError(
                f"failed to terminate the complete Codex process tree: {self.unit_name}"
            )

    def prepare(self) -> None:
        # The unit name is deterministic for task Hosts. If a Uvicorn worker was killed, a stale
        # transient service cannot be allowed to survive into the replacement Host lease.
        self.terminate_tree()

    def wrap_launch_args(self, launch_args: tuple[str, ...], env: dict[str, str]) -> tuple[str, ...]:
        if not launch_args:
            raise CodexProcessIsolationError("Codex launch args are empty")
        if not _SAFE_UNIT.fullmatch(self.unit_name):
            raise CodexProcessIsolationError("unsafe transient Codex unit name")
        safe_env_names = sorted(
            name
            for name in env
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", str(name))
        )
        args: list[str] = [
            "systemd-run",
            f"--unit={self.unit_name}",
            "--collect",
            "--quiet",
            "--pipe",
            "--wait",
            "--service-type=exec",
            "--same-dir",
            f"--property=BindsTo={self.parent_unit}",
            f"--property=After={self.parent_unit}",
            "--property=Restart=no",
            "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes",
            f"--property=TimeoutStopSec={self.stop_grace_seconds:g}s",
            f"--property=MemoryMax={self.memory_mb}M",
            f"--property=CPUQuota={self.cpu_percent}%",
            f"--property=TasksMax={self.pids_max}",
        ]
        # NAME-only setenv reads each value from systemd-run's inherited safe environment. Secrets
        # therefore never appear in argv/process listings, while the trusted wrapper still clears
        # any manager-provided variables before exec'ing Codex.
        for name in safe_env_names:
            args.append(f"--setenv={name}")
        args.append("--")
        args.extend(_escape_systemd_exec_arg(item) for item in launch_args)
        return tuple(args)


def build_codex_process_isolation(
    owner_id: str,
    isolation_key: str,
    *,
    settings: Settings | None = None,
) -> CodexProcessIsolationSpec | None:
    cfg = settings or fresh_settings()
    status = codex_process_isolation_status(cfg)
    if not bool(status.get("enforced")):
        if bool(status.get("required")):
            raise CodexProcessIsolationError(str(status.get("reason") or "Codex cgroup isolation unavailable"))
        return None
    return CodexProcessIsolationSpec(
        unit_name=_unit_name(owner_id, isolation_key),
        parent_unit=str(status["parent_unit"]),
        memory_mb=int(cfg.fdex_agent_sandbox_memory_mb),
        cpu_percent=int(cfg.fdex_agent_sandbox_cpu_percent),
        pids_max=int(cfg.fdex_agent_sandbox_pids_max),
        stop_grace_seconds=float(getattr(cfg, "fdex_agent_process_stop_grace_seconds", 3.0)),
    )
