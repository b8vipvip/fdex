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
from app.codex_retry_controller import (
    MAX_AUTO_RETRIES,
    RetryDecision,
    complete_logical_root_from_retry,
    create_auto_retry_child,
    discard_failed_attempt_worktree,
    finalize_logical_root_failure,
    terminalize_task_failure,
)

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


def _candidate_roots(limit: int = CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    """Return running logical roots that have entered the structured Codex retry state machine.

    This is an internal scheduler query across owners. It never changes user/API owner scope. A
    root is considered only after an attempt audit exists; a merely queued user task is therefore
    never picked up by the reconciler.
    """

    ledger = codex_retry_chain_store()
    ledger.init()
    store = agent_task_store()
    store.init()
    with store.db() as conn:
        rows = conn.execute(
            """
            SELECT root.*
            FROM agent_tasks AS root
            WHERE root.status='running'
              AND root.logical_root_id=root.id
              AND EXISTS (
                    SELECT 1 FROM codex_retry_attempts AS attempt
                    WHERE attempt.owner_id=root.owner_id AND attempt.root_task_id=root.id
              )
            ORDER BY root.updated_at ASC,root.id ASC
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


def _transition_due(transition: dict[str, Any]) -> bool | None:
    created = _parse_time(transition.get("created_at"))
    if created is None:
        return None
    try:
        delay = max(0.0, float(transition.get("backoff_seconds") or 0.0))
    except (TypeError, ValueError):
        return None
    return datetime.now(UTC) >= created + timedelta(seconds=delay)


def _side_effect_free(task: AgentTask) -> bool:
    # Match the accepted Phase 7.38 replay boundary exactly. Phase 7.41 must not broaden it.
    return not bool(task.commit_sha or task.pushed or task.pr_url or task.changed_files)


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
    task: AgentTask,
    *,
    code: str,
    reason: str,
    state: str = "failed",
) -> None:
    """Best-effort structured audit; AgentTask terminal state remains the execution authority."""

    ledger = codex_retry_chain_store()
    try:
        audit = await asyncio.to_thread(ledger.get_attempt, task.owner_id, task.id)
        if audit is None:
            await asyncio.to_thread(
                ledger.record_queued,
                owner_id=task.owner_id,
                root_task_id=task.logical_root_id or task.id,
                attempt_task_id=task.id,
                parent_task_id=task.parent_task_id,
                attempt_index=task.attempt_index,
            )
        await asyncio.to_thread(
            ledger.record_decision,
            owner_id=task.owner_id,
            attempt_task_id=task.id,
            state=state,
            decision_code=code,
            decision_reason=reason,
            error=reason,
        )
    except (KeyError, ValueError, RuntimeError):
        return


async def _fail_chain_closed(
    runtime: FdexAgentRuntime,
    root: AgentTask,
    current: AgentTask,
    *,
    code: str,
    reason: str,
    transition_source_id: str = "",
) -> None:
    current.emit("retry.reconcile_blocked", f"{code}: {reason}"[:4000])
    await _record_reconcile_decision(current, code=code, reason=reason)
    if transition_source_id:
        try:
            await asyncio.to_thread(
                codex_retry_chain_store().mark_transition_state,
                owner_id=root.owner_id,
                source_attempt_task_id=transition_source_id,
                state="blocked",
            )
        except (KeyError, ValueError, RuntimeError):
            pass
    await finalize_logical_root_failure(
        runtime,
        root,
        current,
        error=reason,
        code=code,
        retry_count=max(0, int(current.attempt_index or 0)),
    )


async def _cancel_root_and_children(
    runtime: FdexAgentRuntime,
    root: AgentTask,
    lineage: list[dict[str, Any]],
) -> None:
    for row in reversed(lineage):
        if str(row.get("task_kind") or "") != "auto_retry":
            continue
        if str(row.get("status") or "") not in {"queued", "running"}:
            continue
        try:
            child = await _prime(runtime, row)
            await runtime.request_cancel(child.owner_id, child.id, force_terminal=True)
            await _record_reconcile_decision(
                child,
                code="TASK_CANCELED",
                reason="logical root was canceled before orphan retry reconciliation",
                state="canceled",
            )
        except AgentRuntimeError:
            continue
    try:
        await runtime.request_cancel(root.owner_id, root.id, force_terminal=True)
    except AgentRuntimeError:
        pass


def _transition_decision(transition: dict[str, Any]) -> RetryDecision:
    return RetryDecision(
        True,
        str(transition.get("decision_code") or "RECOVERED_RETRY_PLAN"),
        str(transition.get("decision_reason") or "durable Phase 7.41 retry transition"),
        delay_seconds=max(0.0, float(transition.get("backoff_seconds") or 0.0)),
        excluded_provider_ids=frozenset(
            int(item)
            for item in list(transition.get("excluded_provider_ids") or [])
            if isinstance(item, int) and item > 0
        ),
    )


async def _adopt_pre741_child(
    root: AgentTask,
    latest: AgentTask,
) -> dict[str, Any] | None:
    """Create a transition journal entry for an in-flight queued Phase 7.40 retry child."""

    if latest.task_kind != "auto_retry" or latest.status != "queued" or latest.attempt_index < 1:
        return None
    ledger = codex_retry_chain_store()
    audit = await asyncio.to_thread(ledger.get_attempt, root.owner_id, latest.id)
    if audit is None:
        return None
    if (
        str(audit.get("state") or "").lower() != "queued"
        or str(audit.get("started_at") or "")
        or int(audit.get("provider_id") or 0) > 0
        or not str(audit.get("trigger_code") or "")
    ):
        return None
    try:
        return await asyncio.to_thread(
            ledger.record_transition_from_existing_child,
            owner_id=root.owner_id,
            root_task_id=root.id,
            source_attempt_task_id=latest.parent_task_id,
            source_attempt_index=latest.attempt_index - 1,
            child_task_id=latest.id,
            next_attempt_index=latest.attempt_index,
            decision_code=str(audit.get("trigger_code") or ""),
            decision_reason=str(audit.get("trigger_reason") or ""),
            backoff_seconds=float(audit.get("backoff_seconds") or 0.0),
            excluded_provider_ids=list(audit.get("excluded_provider_ids") or []),
        )
    except (KeyError, ValueError, RuntimeError):
        return None


async def _materialize_transition_child(
    runtime: FdexAgentRuntime,
    root: AgentTask,
    transition: dict[str, Any],
    lineage: list[dict[str, Any]],
) -> tuple[AgentTask | None, str, str]:
    """Return the exact child named by a durable transition, creating/repairing it when safe."""

    ledger = codex_retry_chain_store()
    store = agent_task_store()
    source_id = str(transition.get("source_attempt_task_id") or "")
    source_index = int(transition.get("source_attempt_index") or 0)
    next_index = int(transition.get("next_attempt_index") or 0)
    decision_code = str(transition.get("decision_code") or "")
    if not source_id or not decision_code:
        return None, "RETRY_TRANSITION_INVALID", "durable retry transition is missing source or decision identity"
    if next_index < 1 or next_index > MAX_AUTO_RETRIES or next_index != source_index + 1:
        return None, "RETRY_BUDGET_INVALID", "durable retry transition exceeds the accepted bounded retry budget"

    source_row = await asyncio.to_thread(store.get, root.owner_id, source_id)
    if source_row is None:
        return None, "RETRY_SOURCE_MISSING", "durable retry transition lost its source physical attempt"
    source = await _prime(runtime, source_row)
    if (
        source.attempt_index != source_index
        or (source.id != root.id and source.logical_root_id != root.id)
        or source.owner_id != root.owner_id
    ):
        return None, "RETRY_SOURCE_LINEAGE_MISMATCH", "retry transition source disagrees with immutable AgentTask lineage"
    if not _side_effect_free(source):
        return None, "SIDE_EFFECT_BOUNDARY_REACHED", "retry transition source crossed the accepted FDEX replay side-effect boundary"
    if source.id != root.id and source.status not in {"running", "failed"}:
        return None, "RETRY_SOURCE_STATE_INVALID", f"retry transition source has incompatible state {source.status}"

    child_id = str(transition.get("child_task_id") or "")
    index_rows = [
        row
        for row in lineage
        if str(row.get("task_kind") or "") == "auto_retry"
        and int(row.get("attempt_index") or 0) == next_index
    ]
    if child_id:
        child_row = await asyncio.to_thread(store.get, root.owner_id, child_id)
        if child_row is None:
            return None, "RETRY_CHILD_MISSING", "durable retry transition points to a missing child task"
        if any(str(row.get("id") or "") != child_id for row in index_rows):
            return None, "RETRY_CHILD_DUPLICATE", "multiple task rows claim the same retry attempt index"
        child = await _prime(runtime, child_row)
    else:
        exact = [
            row
            for row in index_rows
            if str(row.get("parent_task_id") or "") == source.id
        ]
        if len(index_rows) > 1 or len(exact) > 1:
            return None, "RETRY_CHILD_DUPLICATE", "multiple task rows claim the planned retry attempt index"
        if exact:
            child = await _prime(runtime, exact[0])
            try:
                transition = await asyncio.to_thread(
                    ledger.attach_transition_child,
                    owner_id=root.owner_id,
                    source_attempt_task_id=source.id,
                    child_task_id=child.id,
                )
            except (KeyError, ValueError, RuntimeError):
                return None, "RETRY_TRANSITION_CONFLICT", "could not attach existing child to durable retry transition"
        elif index_rows:
            return None, "RETRY_CHILD_LINEAGE_MISMATCH", "planned retry index already belongs to a different parent"
        else:
            # If the old worker died after planning but before source terminalization/cleanup,
            # complete exactly those normal Phase 7.38 steps before creating the planned child.
            if source.id != root.id and source.status == "running":
                await terminalize_task_failure(
                    runtime,
                    source.id,
                    source.error or str(transition.get("decision_reason") or "Codex retry source failed"),
                )
            await discard_failed_attempt_worktree(runtime, source)
            decision = _transition_decision(transition)
            child = await create_auto_retry_child(
                runtime,
                source,
                retry_number=next_index,
                decision=decision,
            )
            try:
                transition = await asyncio.to_thread(
                    ledger.attach_transition_child,
                    owner_id=root.owner_id,
                    source_attempt_task_id=source.id,
                    child_task_id=child.id,
                )
            except (KeyError, ValueError, RuntimeError):
                return None, "RETRY_TRANSITION_CONFLICT", "new retry child could not be attached to its durable transition"

    if (
        child.task_kind != "auto_retry"
        or child.logical_root_id != root.id
        or child.parent_task_id != source.id
        or child.attempt_index != next_index
        or child.owner_id != root.owner_id
    ):
        return None, "RETRY_CHILD_LINEAGE_MISMATCH", "retry child disagrees with immutable transition/AgentTask lineage"

    # A transition is the authority for why this child exists. If the worker died after child
    # creation but before the Phase 7.39 audit insert, reconstruct that audit exactly from the plan.
    audit = await asyncio.to_thread(ledger.get_attempt, root.owner_id, child.id)
    if audit is None:
        await asyncio.to_thread(
            ledger.record_queued,
            owner_id=root.owner_id,
            root_task_id=root.id,
            attempt_task_id=child.id,
            parent_task_id=source.id,
            attempt_index=next_index,
            trigger_code=decision_code,
            trigger_reason=str(transition.get("decision_reason") or ""),
            backoff_seconds=float(transition.get("backoff_seconds") or 0.0),
            excluded_provider_ids=list(transition.get("excluded_provider_ids") or []),
        )
    else:
        if (
            str(audit.get("root_task_id") or "") != root.id
            or str(audit.get("parent_task_id") or "") != source.id
            or int(audit.get("attempt_index") or -1) != next_index
        ):
            return None, "RECOVERY_METADATA_MISMATCH", "retry audit disagrees with transition and immutable task lineage"
        # Fill pre-transition blank trigger fields from the durable plan without changing already-started
        # Provider/Host evidence. record_queued's conflict clause preserves non-empty fields.
        await asyncio.to_thread(
            ledger.record_queued,
            owner_id=root.owner_id,
            root_task_id=root.id,
            attempt_task_id=child.id,
            parent_task_id=source.id,
            attempt_index=next_index,
            trigger_code=decision_code,
            trigger_reason=str(transition.get("decision_reason") or ""),
            backoff_seconds=float(transition.get("backoff_seconds") or 0.0),
            excluded_provider_ids=list(transition.get("excluded_provider_ids") or []),
        )

    audit = await asyncio.to_thread(ledger.get_attempt, root.owner_id, child.id)
    if audit is None:
        return None, "RECOVERY_METADATA_MISSING", "retry child audit could not be reconstructed from durable transition"
    plan_exclusions = sorted(int(item) for item in list(transition.get("excluded_provider_ids") or []))
    audit_exclusions = sorted(int(item) for item in list(audit.get("excluded_provider_ids") or []))
    try:
        backoff_matches = abs(
            float(audit.get("backoff_seconds") or 0.0)
            - float(transition.get("backoff_seconds") or 0.0)
        ) < 0.000001
    except (TypeError, ValueError):
        backoff_matches = False
    if (
        str(audit.get("root_task_id") or "") != root.id
        or str(audit.get("parent_task_id") or "") != source.id
        or int(audit.get("attempt_index") or -1) != next_index
        or str(audit.get("trigger_code") or "") != decision_code
        or audit_exclusions != plan_exclusions
        or not backoff_matches
    ):
        return None, "RECOVERY_METADATA_MISMATCH", "retry audit policy metadata disagrees with durable transition"
    return child, "", ""


async def _reconcile_root(candidate: dict[str, Any]) -> str:
    owner_id = str(candidate.get("owner_id") or "")
    root_id = str(candidate.get("id") or "")
    if not owner_id or not root_id:
        return "invalid"

    store = agent_task_store()
    runtime = agent_runtime()
    try:
        # Normal Web/API/employee execution owns this root flock for the complete Phase 7.38 chain.
        # Acquiring it proves no live worker still owns the logical task. Multiple reconcilers may
        # scan the same database; only one can enter this block for a root.
        with store.run_lock(root_id):
            root_row = await asyncio.to_thread(store.get, owner_id, root_id)
            if root_row is None:
                return "gone"
            root = await _prime(runtime, root_row)
            if root.logical_root_id != root.id or root.status != "running":
                return "settled"

            lineage = await asyncio.to_thread(store.list_execution_lineage, owner_id, root.id)
            if not lineage:
                await terminalize_task_failure(runtime, root.id, "orphan logical Agent task lost its execution lineage")
                return "blocked"
            if root.cancel_requested:
                await _cancel_root_and_children(runtime, root, lineage)
                return "canceled"

            latest_row = max(
                lineage,
                key=lambda row: (
                    int(row.get("attempt_index") or 0),
                    str(row.get("created_at") or ""),
                    str(row.get("id") or ""),
                ),
            )
            latest = await _prime(runtime, latest_row)
            ledger = codex_retry_chain_store()
            transition = await asyncio.to_thread(
                ledger.latest_open_transition_for_root,
                owner_id,
                root.id,
            )

            # Upgrade compatibility: Phase 7.40 may already have a fully audited queued child but
            # no transition journal because it predates 7.41. Adopt only that exact structured
            # audit; never infer a transition from error/event text.
            if transition is None and latest.task_kind == "auto_retry" and latest.status == "queued":
                transition = await _adopt_pre741_child(root, latest)

            if transition is None:
                turns = await asyncio.to_thread(_attempt_turn_evidence, owner_id, latest.id)
                code = "SIDE_EFFECT_UNKNOWN" if turns else "ORPHAN_ATTEMPT_NO_TRANSITION"
                reason = (
                    "orphan physical attempt has a durable Codex Turn but no committed next-retry transition"
                    if turns
                    else "logical Agent worker exited before a durable retry transition was committed; automatic replay is forbidden"
                )
                await _fail_chain_closed(runtime, root, latest, code=code, reason=reason)
                return "blocked"

            source_id = str(transition.get("source_attempt_task_id") or "")
            child, code, reason = await _materialize_transition_child(
                runtime,
                root,
                transition,
                lineage,
            )
            if child is None:
                source_row = await asyncio.to_thread(store.get, owner_id, source_id)
                current = await _prime(runtime, source_row) if source_row is not None else root
                await _fail_chain_closed(
                    runtime,
                    root,
                    current,
                    code=code or "RETRY_TRANSITION_INVALID",
                    reason=reason or "durable retry transition cannot be reconciled safely",
                    transition_source_id=source_id,
                )
                return "blocked"

            audit = await asyncio.to_thread(ledger.get_attempt, owner_id, child.id)
            assert audit is not None

            # A child can have reached a durable terminal state immediately before the worker died.
            # This is not a replay. Project the already-committed outcome to the still-running root.
            if child.status == "succeeded":
                await complete_logical_root_from_retry(
                    runtime,
                    root,
                    child,
                    retry_count=child.attempt_index,
                )
                try:
                    await asyncio.to_thread(
                        ledger.mark_transition_state,
                        owner_id=owner_id,
                        source_attempt_task_id=source_id,
                        state="settled",
                    )
                except (KeyError, ValueError, RuntimeError):
                    pass
                return "recovered"
            if child.status in {"failed", "canceled"}:
                terminal_code = str(audit.get("decision_code") or "") or (
                    "TASK_CANCELED" if child.status == "canceled" else "ORPHAN_RETRY_ATTEMPT_FAILED"
                )
                terminal_reason = (
                    child.error
                    or str(audit.get("error") or "")
                    or str(audit.get("decision_reason") or "")
                    or f"orphan retry child reached terminal state {child.status}"
                )
                try:
                    await asyncio.to_thread(
                        ledger.mark_transition_state,
                        owner_id=owner_id,
                        source_attempt_task_id=source_id,
                        state="canceled" if child.status == "canceled" else "blocked",
                    )
                except (KeyError, ValueError, RuntimeError):
                    pass
                await finalize_logical_root_failure(
                    runtime,
                    root,
                    child,
                    error=terminal_reason,
                    code=terminal_code,
                    retry_count=child.attempt_index,
                )
                return "canceled" if child.status == "canceled" else "blocked"

            turns = await asyncio.to_thread(_attempt_turn_evidence, owner_id, child.id)
            audit_started = bool(
                str(audit.get("started_at") or "")
                or int(audit.get("provider_id") or 0) > 0
                or str(audit.get("state") or "").lower() == "running"
                or str(transition.get("state") or "").lower() == "started"
            )
            if child.status != "queued" or audit_started or turns:
                code = "SIDE_EFFECT_UNKNOWN" if turns else "ATTEMPT_ALREADY_STARTED"
                reason = (
                    "orphan automatic retry already has a durable Codex Turn; side effects may have occurred"
                    if turns
                    else "orphan automatic retry already crossed the Provider/Host start boundary"
                )
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code=code,
                    reason=reason,
                    transition_source_id=source_id,
                )
                return "blocked"

            due = _transition_due(transition)
            if due is None:
                await _fail_chain_closed(
                    runtime,
                    root,
                    child,
                    code="RECOVERY_BACKOFF_INVALID",
                    reason="retry transition timestamp/backoff is invalid; automatic replay cannot be scheduled safely",
                    transition_source_id=source_id,
                )
                return "blocked"
            if not due:
                return "backoff"

            child.emit(
                "retry.reconcile_claimed",
                "Phase 7.41 reclaimed a planned queued automatic retry after the previous logical-task worker exited",
            )
            final = await FdexAgentLoop(runtime).run_from_retry_child(child.id)
            if final.status in {"succeeded", "failed", "canceled"}:
                try:
                    await asyncio.to_thread(
                        ledger.mark_transition_state,
                        owner_id=owner_id,
                        source_attempt_task_id=source_id,
                        state="settled" if final.status == "succeeded" else "blocked",
                    )
                except (KeyError, ValueError, RuntimeError):
                    pass
            return "recovered"
    except TaskRunBusy:
        return "busy"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Never turn a reconciler implementation error into another execution authority. The root
        # remains durable and a later pass/operator can inspect the explicit reconcile_error event.
        try:
            row = await asyncio.to_thread(store.get, owner_id, root_id)
            if row is not None and str(row.get("status") or "") == "running":
                root = await _prime(runtime, row)
                root.emit("retry.reconcile_error", str(exc)[:1000])
        except Exception:
            pass
        return "error"


async def reconcile_codex_retry_chains_once(*, limit: int = CANDIDATE_LIMIT) -> dict[str, int]:
    rows = await asyncio.to_thread(_candidate_roots, limit)
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
        action = await _reconcile_root(row)
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
            # Database-level failures are retried on the next bounded scheduler tick. Candidate
            # failures themselves are fail-closed inside _reconcile_root.
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
