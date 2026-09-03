from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent_loop import FdexAgentLoop
from app.agent_runtime import AgentRuntimeError, AgentTask, FdexAgentRuntime, agent_runtime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.codex_host_store import codex_host_store
from app.codex_retry_chain_store import codex_retry_chain_store
from app.codex_retry_controller import finalize_logical_root_failure, terminalize_task_failure

RECONCILE_INTERVAL_SECONDS = 15
CANDIDATE_LIMIT = 100

_reconciler_task: asyncio.Task[None] | None = None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _candidate_rows(limit: int = CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    """Return only durable automatic-retry tasks that may require reconciliation.

    This is intentionally an internal cross-owner scheduler query. User/API task listing remains
    owner-scoped and continues to hide ``auto_retry`` children by default.
    """

    store = agent_task_store()
    store.init()
    with store.db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM agent_tasks
            WHERE task_kind='auto_retry' AND status IN ('queued','running')
            ORDER BY updated_at ASC,id ASC
            LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        ).fetchall()
    return [store._row(row) for row in rows]


async def _prime(runtime: FdexAgentRuntime, row: dict[str, Any]) -> AgentTask:
    task = runtime._task_from_record(row)
    async with runtime._lock:
        runtime._tasks[task.id] = task
    return task


def _audit_due(audit: dict[str, Any]) -> bool | None:
    created = _parse_time(audit.get("created_at"))
    if created is None:
        return None
    try:
        delay = max(0.0, float(audit.get("backoff_seconds") or 0.0))
    except (TypeError, ValueError):
        return None
    return datetime.now(UTC) >= created + timedelta(seconds=delay)


def _attempt_turn_evidence(owner_id: str, task_id: str) -> list[dict[str, Any]]:
    """Return durable Codex Turn rows owned by this physical attempt only."""

    try:
        state = codex_host_store().task_state(owner_id, task_id, turn_limit=200, control_limit=1)
    except (KeyError, ValueError, RuntimeError):
        return []
    if not isinstance(state, dict):
        return []
    return [
        dict(row)
        for row in list(state.get("turns") or [])
        if isinstance(row, dict) and str(row.get("task_id") or "") == task_id
    ]


async def _record_reconcile_decision(
    child: AgentTask,
    *,
    code: str,
    reason: str,
    state: str = "failed",
) -> None:
    """Best-effort audit repair; task terminalization remains authoritative if audit is damaged."""

    ledger = codex_retry_chain_store()
    try:
        audit = await asyncio.to_thread(ledger.get_attempt, child.owner_id, child.id)
        if audit is None:
            await asyncio.to_thread(
                ledger.record_queued,
                owner_id=child.owner_id,
                root_task_id=child.logical_root_id,
                attempt_task_id=child.id,
                parent_task_id=child.parent_task_id,
                attempt_index=child.attempt_index,
                trigger_code="RECOVERY_METADATA_MISSING",
                trigger_reason="Phase 7.41 reconstructed a minimal audit row after worker loss",
            )
        await asyncio.to_thread(
            ledger.record_decision,
            owner_id=child.owner_id,
            attempt_task_id=child.id,
            state=state,
            decision_code=code,
            decision_reason=reason,
            error=reason,
        )
    except (KeyError, ValueError, RuntimeError):
        return


async def _fail_chain_closed(
    runtime: FdexAgentRuntime,
    root: AgentTask | None,
    child: AgentTask,
    *,
    code: str,
    reason: str,
) -> None:
    child.emit("retry.reconcile_blocked", f"{code}: {reason}"[:4000])
    await _record_reconcile_decision(child, code=code, reason=reason)
    if root is None:
        await terminalize_task_failure(runtime, child.id, reason)
        return
    await finalize_logical_root_failure(
        runtime,
        root,
        child,
        error=reason,
        code=code,
        retry_count=max(0, int(child.attempt_index or 0)),
    )


async def _cancel_stale_child(runtime: FdexAgentRuntime, root: AgentTask, child: AgentTask) -> None:
    reason = f"logical root is already terminal ({root.status}); orphan automatic retry will not execute"
    try:
        await runtime.request_cancel(child.owner_id, child.id, force_terminal=True)
    except AgentRuntimeError:
        pass
    await _record_reconcile_decision(child, code="LOGICAL_ROOT_TERMINAL", reason=reason, state="canceled")


async def _reconcile_candidate(candidate: dict[str, Any]) -> str:
    """Reconcile one physical retry attempt after proving the logical-root execution lease is free."""

    owner_id = str(candidate.get("owner_id") or "")
    child_id = str(candidate.get("id") or "")
    root_id = str(candidate.get("logical_root_id") or "")
    if not owner_id or not child_id or not root_id or child_id == root_id:
        return "invalid"

    store = agent_task_store()
    runtime = agent_runtime()
    try:
        # Every production logical Agent execution holds the root task flock for the complete
        # bounded retry chain. Acquiring it proves the former chain owner is gone. Multiple Uvicorn
        # workers may run this reconciler; only one can claim a root at a time.
        with store.run_lock(root_id):
            child_row = await asyncio.to_thread(store.get, owner_id, child_id)
            if child_row is None:
                return "gone"
            child = await _prime(runtime, child_row)
            if child.task_kind != "auto_retry" or child.status not in {"queued", "running"}:
                return "settled"

            root_row = await asyncio.to_thread(store.get, owner_id, root_id)
            root = await _prime(runtime, root_row) if root_row is not None else None
            if root is None:
                await _fail_chain_closed(
                    runtime,
                    None,
                    child,
                    code="LOGICAL_ROOT_MISSING",
                    reason="automatic retry lost its durable logical root; replay is forbidden",
                )
                return "blocked"
            if root.id != child.logical_root_id:
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code="LINEAGE_MISMATCH",
                    reason="automatic retry lineage no longer matches its durable logical root",
                )
                return "blocked"
            if root.status in {"succeeded", "failed", "canceled"}:
                await _cancel_stale_child(runtime, root, child)
                return "canceled"
            if root.cancel_requested:
                try:
                    await runtime.request_cancel(child.owner_id, child.id, force_terminal=True)
                except AgentRuntimeError:
                    pass
                try:
                    await runtime.request_cancel(root.owner_id, root.id, force_terminal=True)
                except AgentRuntimeError:
                    pass
                await _record_reconcile_decision(
                    child,
                    code="TASK_CANCELED",
                    reason="logical root was canceled before orphan retry reconciliation",
                    state="canceled",
                )
                return "canceled"
            if root.status != "running":
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code="ROOT_STATE_INCONSISTENT",
                    reason=f"logical root has unexpected non-terminal state {root.status}; replay is forbidden",
                )
                return "blocked"

            ledger = codex_retry_chain_store()
            audit = await asyncio.to_thread(ledger.get_attempt, owner_id, child.id)
            if audit is None:
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code="RECOVERY_METADATA_MISSING",
                    reason=(
                        "automatic retry exists in primary task lineage but its structured retry audit "
                        "was never committed; Provider/backoff intent cannot be proven"
                    ),
                )
                return "blocked"
            if (
                str(audit.get("root_task_id") or "") != root.id
                or int(audit.get("attempt_index") or -1) != child.attempt_index
                or str(audit.get("parent_task_id") or "") != child.parent_task_id
            ):
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code="RECOVERY_METADATA_MISMATCH",
                    reason="retry audit metadata disagrees with immutable primary task lineage",
                )
                return "blocked"

            turns = await asyncio.to_thread(_attempt_turn_evidence, owner_id, child.id)
            audit_started = bool(
                str(audit.get("started_at") or "")
                or int(audit.get("provider_id") or 0) > 0
                or str(audit.get("state") or "").lower() == "running"
            )
            if child.status != "queued" or audit_started or turns:
                code = "SIDE_EFFECT_UNKNOWN" if turns else "ATTEMPT_ALREADY_STARTED"
                reason = (
                    "orphan automatic retry already has a durable Codex Turn; side effects may have occurred"
                    if turns
                    else "orphan automatic retry already crossed the Provider/Host start boundary"
                )
                await _fail_chain_closed(runtime, root, child, code=code, reason=reason)
                return "blocked"

            due = _audit_due(audit)
            if due is None:
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code="RECOVERY_BACKOFF_INVALID",
                    reason="retry backoff timestamp is invalid; automatic replay cannot be scheduled safely",
                )
                return "blocked"
            if not due:
                return "backoff"

            child.emit(
                "retry.reconcile_claimed",
                "Phase 7.41 reclaimed a queued automatic retry after the previous logical-task worker exited",
            )
            await FdexAgentLoop(runtime).run_from_retry_child(child.id)
            return "recovered"
    except TaskRunBusy:
        return "busy"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Reconciliation itself must never silently convert an uncertain task into a replay. If we
        # cannot prove and execute the recovery transaction, leave it durable for the next pass or
        # an operator rather than inventing a second execution authority.
        try:
            row = await asyncio.to_thread(store.get, owner_id, child_id)
            if row is not None and str(row.get("status") or "") in {"queued", "running"}:
                child = await _prime(runtime, row)
                child.emit("retry.reconcile_error", str(exc)[:1000])
        except Exception:
            pass
        return "error"


async def reconcile_codex_retry_chains_once(*, limit: int = CANDIDATE_LIMIT) -> dict[str, int]:
    rows = await asyncio.to_thread(_candidate_rows, limit)
    stats: dict[str, int] = {
        "scanned": len(rows),
        "recovered": 0,
        "blocked": 0,
        "canceled": 0,
        "busy": 0,
        "backoff": 0,
        "other": 0,
    }
    for row in rows:
        action = await _reconcile_candidate(row)
        if action in stats:
            stats[action] += 1
        else:
            stats["other"] += 1
    return stats


async def _reconciler_loop() -> None:
    while True:
        try:
            await reconcile_codex_retry_chains_once()
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Individual candidates fail closed inside _reconcile_candidate. A database-level
            # failure is retried on the next bounded scheduler tick.
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)


async def start_codex_retry_chain_reconciler() -> None:
    global _reconciler_task
    if _reconciler_task is None or _reconciler_task.done():
        _reconciler_task = asyncio.create_task(
            _reconciler_loop(),
            name="fdex-codex-retry-chain-reconciler",
        )


async def stop_codex_retry_chain_reconciler() -> None:
    global _reconciler_task
    task = _reconciler_task
    _reconciler_task = None
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
