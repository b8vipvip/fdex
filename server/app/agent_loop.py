from __future__ import annotations

import asyncio

from app.agent_runtime import AgentRuntimeError, AgentTask, FdexAgentRuntime
from app.codex_retry_chain_store import codex_retry_chain_store
from app.codex_retry_controller import (
    RetryAttemptCapture,
    capture_codex_attempt,
    complete_logical_root_from_retry,
    create_auto_retry_child,
    decide_codex_retry,
    discard_failed_attempt_worktree,
    finalize_logical_root_failure,
    install_codex_retry_failure_capture,
    run_retry_child_locked,
    terminalize_task_failure,
)
from app.codex_retry_provider_context import codex_retry_provider_exclusions

install_codex_retry_failure_capture()


class FdexAgentLoop:
    """Stable Codex-only execution facade with bounded recovery and logical-task projection.

    Phase 7.38 owns retry safety. Phase 7.39 adds a structured attempt ledger without changing any
    retry decision or authority boundary. Phase 7.40 makes retry lineage atomic in AgentTask.
    Phase 7.41 can therefore resume only a durable, not-yet-started ``auto_retry`` child after the
    former logical-task worker dies, while continuing to use this exact same bounded retry driver.

    The legacy FDEX Agent core remains deleted: this facade never selects it and never falls back
    from the official Codex Host to an ordinary AI execution path.
    """

    def __init__(self, runtime: FdexAgentRuntime) -> None:
        self.runtime = runtime

    async def _record_attempt_decision(
        self,
        task: AgentTask,
        *,
        state: str,
        code: str,
        reason: str,
        error: str = "",
    ) -> None:
        await asyncio.to_thread(
            codex_retry_chain_store().record_decision,
            owner_id=task.owner_id,
            attempt_task_id=task.id,
            state=state,
            decision_code=code,
            decision_reason=reason,
            error=error,
        )

    async def _run_one_attempt(
        self,
        task_id: str,
        *,
        root_task_id: str,
        attempt_index: int,
        excluded_provider_ids: frozenset[int] = frozenset(),
    ) -> tuple[AgentTask, RetryAttemptCapture | None]:
        task = await self.runtime.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if task.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {task.status}")

        ledger = codex_retry_chain_store()
        await asyncio.to_thread(
            ledger.record_queued,
            owner_id=task.owner_id,
            root_task_id=root_task_id,
            attempt_task_id=task.id,
            parent_task_id=task.parent_task_id,
            attempt_index=attempt_index,
        )

        # Import at execution time so Phase 7.33's rollout installer has already rebound the
        # status/provider seams. Retry-scoped exclusions are active before preflight and Host
        # launch; they are never mutated after a Turn starts.
        from app.codex_engine import codex_runtime_status
        from app.codex_host_entry import run_codex_task

        with codex_retry_provider_exclusions(set(excluded_provider_ids)):
            status = codex_runtime_status()
            if not bool(status.get("ready")):
                reason = str(status.get("reason") or "Codex engine is not ready")
                await self._record_attempt_decision(
                    task,
                    state="blocked",
                    code="PREFLIGHT_NOT_READY",
                    reason=reason,
                    error=reason,
                )
                failed = await terminalize_task_failure(self.runtime, task_id, reason)
                return failed, None

            provider_id = int(status.get("provider_id") or 0)
            await asyncio.to_thread(
                ledger.record_started,
                owner_id=task.owner_id,
                root_task_id=root_task_id,
                attempt_task_id=task.id,
                parent_task_id=task.parent_task_id,
                attempt_index=attempt_index,
                provider_id=provider_id,
                provider_name=str(status.get("provider_name") or ""),
                model=str(status.get("model") or ""),
            )
            with capture_codex_attempt(
                task_id,
                root_task_id=root_task_id,
                provider_id=provider_id,
            ) as capture:
                try:
                    await run_codex_task(self.runtime, task_id)
                except Exception as exc:
                    # Host layers normally converge on fail_task themselves. This preserves the
                    # same capture semantics for an unexpected exception escaping a Host wrapper.
                    await self.runtime.fail_task(task_id, str(exc))

            latest = await self.runtime.get_task(task_id)
            if latest is None:
                raise AgentRuntimeError("task disappeared after Codex execution")
            if latest.status == "succeeded":
                await asyncio.to_thread(
                    ledger.record_terminal,
                    owner_id=latest.owner_id,
                    attempt_task_id=latest.id,
                    state="succeeded",
                )
            elif latest.status == "canceled":
                await self._record_attempt_decision(
                    latest,
                    state="canceled",
                    code="TASK_CANCELED",
                    reason="task cancellation reached a Codex safe boundary",
                    error=latest.error,
                )
            elif not capture.failed and latest.status == "failed":
                await self._record_attempt_decision(
                    latest,
                    state="failed",
                    code="ATTEMPT_FAILED_UNCAPTURED",
                    reason="Codex attempt terminalized outside the bounded retry capture seam",
                    error=latest.error,
                )
            return latest, capture

    async def _run_retry_child(
        self,
        child: AgentTask,
        *,
        root_task_id: str,
        attempt_index: int,
        excluded_provider_ids: frozenset[int],
    ) -> tuple[AgentTask, RetryAttemptCapture | None]:
        async def runner() -> tuple[AgentTask, RetryAttemptCapture | None]:
            return await self._run_one_attempt(
                child.id,
                root_task_id=root_task_id,
                attempt_index=attempt_index,
                excluded_provider_ids=excluded_provider_ids,
            )

        return await run_retry_child_locked(child, runner)

    async def _drive_chain(
        self,
        root: AgentTask,
        current: AgentTask,
        capture: RetryAttemptCapture | None,
        *,
        retry_count: int,
    ) -> AgentTask:
        """Drive the remainder of one bounded logical retry chain from an existing attempt."""

        while True:
            if current.status == "succeeded":
                if current.id == root.id:
                    return current
                return await complete_logical_root_from_retry(
                    self.runtime,
                    root,
                    current,
                    retry_count=retry_count,
                )

            if current.status == "canceled":
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    current,
                    error=current.error or "Coding Agent task canceled",
                    code="TASK_CANCELED",
                    retry_count=retry_count,
                )

            if capture is None or not capture.failed:
                # Preflight readiness failures are already terminal. Never turn them into retries
                # because an older health snapshot happened to look transient.
                if current.id == root.id:
                    return current
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    current,
                    error=current.error or "Codex retry attempt failed before Host start",
                    code="RETRY_PREFLIGHT_BLOCKED",
                    retry_count=retry_count,
                )

            error = capture.error or current.error or "Codex attempt failed"
            decision = await decide_codex_retry(
                current,
                retry_number=retry_count + 1,
                failed_provider_id=capture.provider_id,
            )
            await self._record_attempt_decision(
                current,
                state="failed",
                code=decision.code,
                reason=decision.reason,
                error=error,
            )
            if not decision.retry:
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    current,
                    error=error,
                    code=decision.code,
                    retry_count=retry_count,
                )

            # Child attempts are durable audit records. The logical root deliberately remains
            # non-terminal until the bounded chain has one final success/failure outcome.
            if current.id != root.id:
                await terminalize_task_failure(self.runtime, current.id, error)

            await discard_failed_attempt_worktree(self.runtime, current)
            next_index = retry_count + 1
            child = await create_auto_retry_child(
                self.runtime,
                current,
                retry_number=next_index,
                decision=decision,
            )
            await asyncio.to_thread(
                codex_retry_chain_store().record_queued,
                owner_id=child.owner_id,
                root_task_id=root.id,
                attempt_task_id=child.id,
                parent_task_id=current.id,
                attempt_index=next_index,
                trigger_code=decision.code,
                trigger_reason=decision.reason,
                backoff_seconds=decision.delay_seconds,
                excluded_provider_ids=decision.excluded_provider_ids,
            )
            retry_count = next_index

            await asyncio.sleep(max(0.0, float(decision.delay_seconds)))
            if await asyncio.to_thread(self.runtime.task_store.cancel_requested, root.id):
                try:
                    await self.runtime.request_cancel(child.owner_id, child.id, force_terminal=True)
                except AgentRuntimeError:
                    pass
                await self._record_attempt_decision(
                    child,
                    state="canceled",
                    code="TASK_CANCELED",
                    reason="logical root was canceled during automatic retry backoff",
                    error="Coding Agent task canceled during automatic retry backoff",
                )
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    child,
                    error="Coding Agent task canceled during automatic retry backoff",
                    code="TASK_CANCELED",
                    retry_count=retry_count,
                )

            try:
                current, capture = await self._run_retry_child(
                    child,
                    root_task_id=root.id,
                    attempt_index=retry_count,
                    excluded_provider_ids=decision.excluded_provider_ids,
                )
            except RuntimeError as exc:
                await self._record_attempt_decision(
                    child,
                    state="blocked",
                    code="RETRY_CHILD_BUSY",
                    reason=str(exc),
                    error=str(exc),
                )
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    child,
                    error=str(exc),
                    code="RETRY_CHILD_BUSY",
                    retry_count=retry_count,
                )

    async def run(self, task_id: str) -> AgentTask:
        root = await self.runtime.get_task(task_id)
        if root is None:
            raise AgentRuntimeError("task not found")
        if root.task_kind == "auto_retry":
            raise AgentRuntimeError(
                "internal automatic retry tasks cannot be run as logical roots; Phase 7.41 owns orphan recovery"
            )
        if root.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {root.status}")

        current, capture = await self._run_one_attempt(
            root.id,
            root_task_id=root.id,
            attempt_index=0,
        )
        return await self._drive_chain(root, current, capture, retry_count=0)

    async def run_from_retry_child(self, task_id: str) -> AgentTask:
        """Resume a durable queued auto-retry after an external root execution lease is reclaimed.

        This is deliberately stricter than ``run``. The child must still be queued, its immutable
        Phase 7.40 lineage must match a running logical root, and its Phase 7.39 audit row must prove
        that Provider/Host execution never started. The background reconciler enforces backoff and
        owns the root flock before calling this method.
        """

        child = await self.runtime.get_task(task_id)
        if child is None:
            raise AgentRuntimeError("retry child not found")
        if child.task_kind != "auto_retry":
            raise AgentRuntimeError("recovery requires an automatic retry task")
        if child.status != "queued":
            raise AgentRuntimeError("only a queued automatic retry can be recovered")
        if not child.logical_root_id or child.logical_root_id == child.id or child.attempt_index < 1:
            raise AgentRuntimeError("automatic retry lineage is invalid")

        root = await self.runtime.get_task(child.logical_root_id)
        if root is None or root.owner_id != child.owner_id:
            raise AgentRuntimeError("logical retry root not found")
        if root.status != "running" or root.cancel_requested:
            raise AgentRuntimeError("logical retry root is not recoverable")

        ledger = codex_retry_chain_store()
        audit = await asyncio.to_thread(ledger.get_attempt, child.owner_id, child.id)
        if audit is None:
            raise AgentRuntimeError("automatic retry audit metadata is missing")
        if (
            str(audit.get("root_task_id") or "") != root.id
            or str(audit.get("parent_task_id") or "") != child.parent_task_id
            or int(audit.get("attempt_index") or -1) != child.attempt_index
        ):
            raise AgentRuntimeError("automatic retry audit metadata does not match primary lineage")
        if (
            str(audit.get("state") or "").lower() != "queued"
            or str(audit.get("started_at") or "")
            or int(audit.get("provider_id") or 0) > 0
        ):
            raise AgentRuntimeError("automatic retry already crossed the Provider/Host start boundary")

        excluded = frozenset(
            int(item)
            for item in list(audit.get("excluded_provider_ids") or [])
            if isinstance(item, int) and item > 0
        )
        current, capture = await self._run_retry_child(
            child,
            root_task_id=root.id,
            attempt_index=child.attempt_index,
            excluded_provider_ids=excluded,
        )
        return await self._drive_chain(
            root,
            current,
            capture,
            retry_count=child.attempt_index,
        )
