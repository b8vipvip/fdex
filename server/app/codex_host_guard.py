from __future__ import annotations

import asyncio
import errno
import hashlib
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from app.agent_runtime import AgentRuntimeError, FdexAgentRuntime
from app.codex_host_store import CodexHostStore, codex_host_store

try:
    import fcntl
except ImportError:  # pragma: no cover - FDEX production server is Linux/systemd.
    fcntl = None  # type: ignore[assignment]


class CodexThreadBusy(AgentRuntimeError):
    """Raised when another FDEX worker already owns the same official Codex Thread."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _lock_path(store: CodexHostStore, owner_id: str, thread_id: str) -> Path:
    digest = hashlib.sha256(f"{owner_id}\0{thread_id}".encode("utf-8")).hexdigest()
    root = (store.path.parent / "codex-host-locks").resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root / f"{digest}.lock"


@contextmanager
def thread_lock(store: CodexHostStore, owner_id: str, thread_id: str) -> Iterator[None]:
    """Hold a crash-safe cross-process lock for one owner-scoped Codex Thread.

    SQLite serializes metadata transactions but must not be held while an app-server Turn is
    running.  A Linux flock is therefore the execution lease: it is visible to every Uvicorn
    worker and the kernel releases it automatically if a worker crashes or is killed.
    """
    if fcntl is None:
        raise AgentRuntimeError("Codex Thread locking requires Linux flock support")
    path = _lock_path(store, owner_id, thread_id)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise CodexThreadBusy("another FDEX worker is already using this Codex Thread") from exc
            raise
        try:
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"pid={os.getpid()}\nthread={thread_id}\n".encode("utf-8"))
            os.fsync(descriptor)
        except OSError:
            pass
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def reconcile_orphaned_thread(store: CodexHostStore, owner_id: str, thread_id: str) -> None:
    """Repair state left behind by a dead Host after this worker acquired the kernel lock."""
    now = _now()
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        thread = conn.execute(
            "SELECT status,current_turn_id FROM codex_threads WHERE owner_id=? AND thread_id=?",
            (owner_id, thread_id),
        ).fetchone()
        if thread is None:
            raise AgentRuntimeError("persisted Codex Thread disappeared")
        if str(thread["status"] or "") not in {"running", "compacting"}:
            return
        error = "previous Codex Host worker exited before this operation reached a terminal notification"
        conn.execute(
            """
            UPDATE codex_turns
            SET status='interrupted',error=CASE WHEN error='' THEN ? ELSE error END,
                completed_at=CASE WHEN completed_at='' THEN ? ELSE completed_at END,updated_at=?
            WHERE owner_id=? AND thread_id=? AND status='inProgress'
            """,
            (error, now, now, owner_id, thread_id),
        )
        conn.execute(
            """
            UPDATE codex_controls
            SET state='failed',error=CASE WHEN error='' THEN ? ELSE error END,
                completed_at=CASE WHEN completed_at='' THEN ? ELSE completed_at END,updated_at=?
            WHERE owner_id=? AND thread_id=? AND state IN ('pending','processing')
            """,
            (error, now, now, owner_id, thread_id),
        )
        conn.execute(
            """
            UPDATE codex_threads
            SET status='interrupted',current_turn_id='',updated_at=?
            WHERE owner_id=? AND thread_id=?
            """,
            (now, owner_id, thread_id),
        )


def settle_orphaned_controls(
    store: CodexHostStore,
    owner_id: str,
    thread_id: str,
    *,
    reason: str,
) -> int:
    """Terminalize controls that no live stdio Host can consume anymore."""
    now = _now()
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE codex_controls
            SET state=CASE WHEN action='steer' THEN 'rejected' ELSE 'failed' END,
                error=CASE WHEN error='' THEN ? ELSE error END,
                completed_at=CASE WHEN completed_at='' THEN ? ELSE completed_at END,updated_at=?
            WHERE owner_id=? AND thread_id=? AND state IN ('pending','processing')
            """,
            (reason[:10000], now, now, owner_id, thread_id),
        )
        return int(cursor.rowcount or 0)


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    """Guard the Phase 7.21 core runner with a Thread execution lease when reusing a Thread."""
    from app.codex_host_runtime import run_codex_task as core_run_codex_task

    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    store = codex_host_store()
    binding = await asyncio.to_thread(store.task_binding, task.owner_id, task.id)
    source_thread_id = str(binding.get("thread_id") or "") if binding else ""

    if not source_thread_id:
        # A brand-new task has no shared Thread identity yet.  Its per-task run_lock is already
        # held by the caller, and no other task can reference the new Thread until it is bound.
        await core_run_codex_task(runtime, task_id)
        final_binding = await asyncio.to_thread(store.task_binding, task.owner_id, task.id)
        if final_binding:
            final_thread_id = str(final_binding.get("thread_id") or "")
            if final_thread_id:
                await asyncio.to_thread(
                    settle_orphaned_controls,
                    store,
                    task.owner_id,
                    final_thread_id,
                    reason="Codex Host process ended before the queued control could be consumed",
                )
        return

    try:
        with thread_lock(store, task.owner_id, source_thread_id):
            await asyncio.to_thread(reconcile_orphaned_thread, store, task.owner_id, source_thread_id)
            await core_run_codex_task(runtime, task_id)
            final_binding = await asyncio.to_thread(store.task_binding, task.owner_id, task.id)
            final_thread_id = str(final_binding.get("thread_id") or "") if final_binding else source_thread_id
            for thread_id in dict.fromkeys([source_thread_id, final_thread_id]):
                if thread_id:
                    await asyncio.to_thread(
                        settle_orphaned_controls,
                        store,
                        task.owner_id,
                        thread_id,
                        reason="Codex Host process ended before the queued control could be consumed",
                    )
    except CodexThreadBusy as exc:
        await runtime.fail_task(task_id, str(exc))


async def compact_codex_thread(
    runtime: FdexAgentRuntime,
    *,
    owner_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Queue active compaction or lease an idle shared Thread before starting a short Host."""
    from app.codex_host_runtime import compact_codex_thread as core_compact_codex_thread

    store = codex_host_store()
    binding = await asyncio.to_thread(store.task_binding, owner_id, task_id)
    if binding is None:
        raise AgentRuntimeError("task has no persisted Codex thread")
    thread_id = str(binding.get("thread_id") or "")
    thread = await asyncio.to_thread(store.get_thread, owner_id, thread_id)
    if thread is None:
        raise AgentRuntimeError("Codex thread not found")

    if str(thread.get("status") or "") in {"running", "compacting"}:
        # The active lease holder will claim this control after the model Turn.  No direct
        # stdio access is attempted from this HTTP worker.
        return await asyncio.to_thread(
            store.enqueue_control,
            owner_id=owner_id,
            task_id=task_id,
            thread_id=thread_id,
            action="compact",
            payload={},
        )

    try:
        with thread_lock(store, owner_id, thread_id):
            await asyncio.to_thread(reconcile_orphaned_thread, store, owner_id, thread_id)
            return await core_compact_codex_thread(runtime, owner_id=owner_id, task_id=task_id)
    except CodexThreadBusy:
        # Status can change between the read above and flock.  Treat that race exactly like an
        # active Thread: persist one compact request for the real lease holder to consume.
        return await asyncio.to_thread(
            store.enqueue_control,
            owner_id=owner_id,
            task_id=task_id,
            thread_id=thread_id,
            action="compact",
            payload={},
        )
