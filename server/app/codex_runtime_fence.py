from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import SERVER_DIR
from app.env_manager import read_env

try:
    import fcntl
except ImportError:  # pragma: no cover - production Phase 7.32 requires Linux
    fcntl = None  # type: ignore[assignment]

_LOCK_PATH = SERVER_DIR / "data" / "codex-runtime-switch.lock"


class CodexRuntimeFenceError(RuntimeError):
    pass


def _open_lock() -> int:
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(_LOCK_PATH, 0o600)
    except OSError:
        pass
    return fd


def _require_flock() -> None:
    if fcntl is None:
        raise CodexRuntimeFenceError("Codex Runtime launch/switch fence requires Linux flock")


def effective_runtime_path() -> Path:
    """Resolve the effective binary with the same precedence as codex_engine.

    This intentionally reads `.env` directly rather than a cached Settings object. A Runtime
    switch must be observable by a newly starting systemd Host even while an older Uvicorn worker
    still has cached configuration.
    """
    configured = str(read_env().get("FDEX_AGENT_CODEX_BIN") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    system_codex = shutil.which("codex")
    if system_codex:
        return Path(system_codex).resolve()
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError as exc:
        raise CodexRuntimeFenceError(
            "no configured, system, or bundled official Codex Runtime is available"
        ) from exc
    return Path(bundled_codex_path()).resolve()


def _assert_current(expected_runtime: str | Path) -> None:
    expected = Path(expected_runtime).expanduser().resolve()
    current = effective_runtime_path()
    if expected != current:
        raise CodexRuntimeFenceError(
            "Codex Runtime changed before Host exec; stale launch was rejected "
            f"(expected={expected}, current={current})"
        )


@contextmanager
def runtime_launch_fence(expected_runtime: str | Path) -> Iterator[None]:
    """Hold a shared launch fence while a transient service validates its Runtime.

    The wrapper itself is already ExecStart of the transient service when this runs, so an
    exclusive switch that begins after this lock is released will enumerate and kill that unit
    before changing the active pin. If a switch wins first, the wrapper observes the new pin and
    rejects its stale Runtime path before exec'ing Codex.
    """
    _require_flock()
    fd = _open_lock()
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        _assert_current(expected_runtime)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def runtime_switch_fence() -> Iterator[None]:
    """Serialize Runtime activation/rollback against every new transient Host exec."""
    _require_flock()
    fd = _open_lock()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
