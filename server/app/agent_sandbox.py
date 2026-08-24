from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


class AgentSandboxError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    memory_mb: int
    cpu_percent: int
    pids_max: int
    allow_network: bool


class SystemdExecutionSandbox:
    """Ephemeral Linux execution sandbox for build/test commands.

    No per-account process is kept alive. systemd creates a transient scope/service for
    each command, enforces cgroup limits and namespace protections, then collects it.
    MemoryMax is a ceiling, not a reservation, so idle accounts consume no sandbox RAM.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.sandbox_root = Path(settings.fdex_agent_sandbox_root).expanduser().resolve()
        self.default_memory_mb = settings.fdex_agent_sandbox_memory_mb
        self.default_cpu_percent = settings.fdex_agent_sandbox_cpu_percent
        self.default_pids_max = settings.fdex_agent_sandbox_pids_max
        self._slots = threading.BoundedSemaphore(settings.fdex_agent_sandbox_max_concurrent)

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("systemd-run")) and Path("/run/systemd/system").exists()

    def account_cache_root(self, owner_id: str) -> Path:
        root = (self.sandbox_root / "owners" / owner_id / "cache").resolve()
        owner_root = (self.sandbox_root / "owners" / owner_id).resolve()
        if owner_root not in root.parents:
            raise AgentSandboxError("account cache escaped sandbox root")
        root.mkdir(parents=True, exist_ok=True)
        for name in ("home", "gradle", "npm", "pip"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return root

    def build_systemd_command(
        self,
        *,
        owner_id: str,
        task_id: str,
        worktree: Path,
        cwd: Path,
        argv: tuple[str, ...],
        limits: SandboxLimits,
    ) -> list[str]:
        worktree = worktree.resolve()
        cwd = cwd.resolve()
        if cwd != worktree and worktree not in cwd.parents:
            raise AgentSandboxError("sandbox cwd escaped task worktree")
        cache = self.account_cache_root(owner_id)
        unit = f"fdex-agent-{task_id[:20]}"
        command = [
            "systemd-run", "--quiet", "--pipe", "--wait", "--collect",
            f"--unit={unit}",
            "-p", f"MemoryMax={max(128, limits.memory_mb)}M",
            "-p", f"CPUQuota={max(10, limits.cpu_percent)}%",
            "-p", f"TasksMax={max(32, limits.pids_max)}",
            "-p", "PrivateTmp=yes",
            "-p", "PrivateDevices=yes",
            "-p", "NoNewPrivileges=yes",
            "-p", "ProtectSystem=strict",
            "-p", "ProtectHome=yes",
            "-p", "ProtectKernelTunables=yes",
            "-p", "ProtectKernelModules=yes",
            "-p", "ProtectKernelLogs=yes",
            "-p", "ProtectControlGroups=yes",
            "-p", "RestrictSUIDSGID=yes",
            "-p", "LockPersonality=yes",
            "-p", "RestrictRealtime=yes",
            "-p", "CapabilityBoundingSet=",
            "-p", f"ReadWritePaths={worktree}",
            "-p", f"ReadWritePaths={cache}",
            "-p", f"WorkingDirectory={cwd}",
            f"--setenv=HOME={cache / 'home'}",
            f"--setenv=GRADLE_USER_HOME={cache / 'gradle'}",
            f"--setenv=npm_config_cache={cache / 'npm'}",
            f"--setenv=PIP_CACHE_DIR={cache / 'pip'}",
            "--setenv=CI=true",
            "--setenv=GIT_TERMINAL_PROMPT=0",
        ]
        if not limits.allow_network:
            command += ["-p", "PrivateNetwork=yes"]
        command += ["--", *argv]
        return command

    def run(
        self,
        *,
        owner_id: str,
        task_id: str,
        worktree: Path,
        cwd: Path,
        argv: tuple[str, ...],
        timeout: float,
        max_output_chars: int,
        memory_mb: int | None = None,
        cpu_percent: int | None = None,
        pids_max: int | None = None,
        allow_network: bool = False,
    ) -> str:
        if not self.available():
            raise AgentSandboxError("systemd execution sandbox is unavailable on this server")
        limits = SandboxLimits(
            memory_mb=memory_mb or self.default_memory_mb,
            cpu_percent=cpu_percent or self.default_cpu_percent,
            pids_max=pids_max or self.default_pids_max,
            allow_network=allow_network,
        )
        command = self.build_systemd_command(
            owner_id=owner_id,
            task_id=task_id,
            worktree=worktree,
            cwd=cwd,
            argv=argv,
            limits=limits,
        )
        acquired = self._slots.acquire(timeout=max(1.0, timeout))
        if not acquired:
            raise AgentSandboxError("sandbox concurrency limit is busy")
        try:
            completed = subprocess.run(
                command,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "LANG": os.environ.get("LANG", "C.UTF-8"),
                    "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentSandboxError(f"sandbox command timed out after {int(timeout)}s") from exc
        finally:
            self._slots.release()
        output = completed.stdout or ""
        if len(output) > max_output_chars:
            output = output[:max_output_chars] + "\n... output truncated ..."
        return f"exit_code={completed.returncode}\nsandbox=systemd\n{output}".strip()


def describe_command(argv: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in argv)
