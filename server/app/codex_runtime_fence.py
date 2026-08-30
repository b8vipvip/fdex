from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import SERVER_DIR

try:
    import fcntl
except ImportError:  # pragma: no cover - production Phase 7.32 requires Linux
    fcntl = None  # type: ignore[assignment]

_LOCK_PATH = SERVER_DIR / "data" / "codex-runtime-switch.lock"
_ACTIVE_PATH = SERVER_DIR / "data" / "codex-runtime-switch.active"


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


def _recorded_runtime_path() -> Path | None:
    try:
        raw = _ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CodexRuntimeFenceError(f"cannot read Codex Runtime fence state: {exc}") from exc
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def record_switched_runtime(runtime_path: str | Path) -> None:
    """Record the effective target of a successful managed switch while the EX lock is held.

    The file is deliberately absent before the first Runtime Manager switch. This preserves
    deployments that supply FDEX_AGENT_CODEX_BIN through systemd Environment rather than `.env`.
    Once FDEX performs a managed switch, subsequent Host starts are fenced against that exact
    effective executable until another managed switch updates the record.
    """
    target = Path(runtime_path).expanduser().resolve()
    _ACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = _ACTIVE_PATH.with_name(f".{_ACTIVE_PATH.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(str(target) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, _ACTIVE_PATH)
        try:
            os.chmod(_ACTIVE_PATH, 0o600)
        except OSError:
            pass
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _assert_current(expected_runtime: str | Path) -> None:
    expected = Path(expected_runtime).expanduser().resolve()
    current = _recorded_runtime_path()
    if current is not None and expected != current:
        raise CodexRuntimeFenceError(
            "Codex Runtime changed before Host exec; stale launch was rejected "
            f"(expected={expected}, current={current})"
        )


@contextmanager
def runtime_launch_fence(expected_runtime: str | Path) -> Iterator[None]:
    """Hold a shared launch fence while a transient service validates its Runtime.

    The wrapper itself is already ExecStart of the transient service when this runs, so an
    exclusive switch that begins after this lock is released will enumerate and kill that unit
    before changing the active pin. If a managed switch won first, the wrapper observes the
    recorded target and rejects its stale Runtime path before exec'ing Codex.
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
