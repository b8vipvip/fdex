from __future__ import annotations

import asyncio

from app.agent_runtime import AgentRuntimeError, AgentTask, FdexAgentRuntime
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
    """Stable Codex-only execution facade with Phase 7.38 bounded recovery.

    There is still no legacy Agent core and no ordinary-AI fallback. A failed Codex attempt is
    eligible for replay only when Phase 7.37 produces a structured transient health code, the
    attempt has not crossed FDEX's commit/publish boundary, and the retry budget remains.

    Every replay is a new AgentTask/worktree. If a healthy fresh-full alternate Provider exists,
    the failed Provider may be excluded only for that new task; Provider identity never changes
    inside a started Host/Turn.
    """

    def __init__(self, runtime: FdexAgentRuntime) -> None:
        self.runtime = runtime

    async def _run_one_attempt(
        self,
        task_id: str,
        *,
        root_task_id: str,
        excluded_provider_ids: frozenset[int] = frozenset(),
    ) -> tuple[AgentTask, RetryAttemptCapture | None]:
        task = await self.runtime.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if task.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {task.status}")

        # Import at execution time so Phase 7.33's rollout installer has already rebound the
        # status/provider seams. Retry-scoped exclusions are active before preflight and Host
        # launch; they are never mutated after a Turn starts.
        from app.codex_engine import codex_runtime_status
        from app.codex_host_entry import run_codex_task

        with codex_retry_provider_exclusions(set(excluded_provider_ids)):
            status = codex_runtime_status()
            if not bool(status.get("ready")):
                reason = str(status.get("reason") or "Codex engine is not ready")
                failed = await terminalize_task_failure(self.runtime, task_id, reason)
                return failed, None

            provider_id = int(status.get("provider_id") or 0)
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
            return latest, capture

    async def _run_retry_child(
        self,
        child: AgentTask,
        *,
        root_task_id: str,
        excluded_provider_ids: frozenset[int],
    ) -> tuple[AgentTask, RetryAttemptCapture | None]:
        async def runner() -> tuple[AgentTask, RetryAttemptCapture | None]:
            return await self._run_one_attempt(
                child.id,
                root_task_id=root_task_id,
                excluded_provider_ids=excluded_provider_ids,
            )

        return await run_retry_child_locked(child, runner)

    async def run(self, task_id: str) -> AgentTask:
        root = await self.runtime.get_task(task_id)
        if root is None:
            raise AgentRuntimeError("task not found")
        if root.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {root.status}")

        current, capture = await self._run_one_attempt(root.id, root_task_id=root.id)
        retry_count = 0

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
            child = await create_auto_retry_child(
                self.runtime,
                current,
                retry_number=retry_count + 1,
                decision=decision,
            )
            retry_count += 1

            await asyncio.sleep(max(0.0, float(decision.delay_seconds)))
            if await asyncio.to_thread(self.runtime.task_store.cancel_requested, root.id):
                try:
                    await self.runtime.request_cancel(child.owner_id, child.id, force_terminal=True)
                except AgentRuntimeError:
                    pass
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
                    excluded_provider_ids=decision.excluded_provider_ids,
                )
            except RuntimeError as exc:
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    child,
                    error=str(exc),
                    code="RETRY_CHILD_BUSY",
                    retry_count=retry_count,
                )
