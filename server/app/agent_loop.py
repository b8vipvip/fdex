from __future__ import annotations

import asyncio

from app.agent_runtime import AgentRuntimeError, AgentTask, FdexAgentRuntime
from app.codex_retry_controller import (
    MAX_AUTO_RETRIES,
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
    eligible for automatic replay only when Phase 7.37 produces a structured transient health
    code, the attempt has not crossed FDEX's commit/publish boundary, and the retry budget remains.

    Every replay is a new AgentTask/worktree. If a healthy fresh-full alternate Provider exists,
    the failed Provider may be excluded *only for that new task*; no Provider is ever switched
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
    ) -> tuple[AgentTask, object | None]:
        task = await self.runtime.get_task(task_id)
        if task is None:
            raise AgentRuntimeError("task not found")
        if task.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {task.status}")

        # Import at execution time so Phase 7.33's rollout installer has already rebound the
        # status/provider seams. Retry-scoped exclusions are active before this preflight and the
        # Host launch, never after a Turn starts.
        from app.codex_engine import codex_runtime_status
        from app.codex_host_entry import run_codex_task

        with codex_retry_provider_exclusions(set(excluded_provider_ids)):
            status = codex_runtime_status()
            if not bool(status.get("ready")):
                reason = str(status.get("reason") or "Codex engine is not ready")
                failed = await terminalize_task_failure(self.runtime, task_id, reason)
                return failed, None

            provider_id = int(status.get("provider_id") or 0)
            with capture_codex_attempt(task_id, provider_id=provider_id) as capture:
                try:
                    await run_codex_task(self.runtime, task_id)
                except Exception as exc:
                    # Host layers normally converge on fail_task themselves. This catch keeps the
                    # same capture semantics for unexpected errors that escape a Host wrapper.
                    await self.runtime.fail_task(task_id, str(exc))

            latest = await self.runtime.get_task(task_id)
            if latest is None:
                raise AgentRuntimeError("task disappeared after Codex execution")
            return latest, capture

    async def run(self, task_id: str) -> AgentTask:
        root = await self.runtime.get_task(task_id)
        if root is None:
            raise AgentRuntimeError("task not found")
        if root.status not in {"queued", "running"}:
            raise AgentRuntimeError(f"task cannot run from status: {root.status}")

        current = root
        retry_count = 0
        exclusions: frozenset[int] = frozenset()

        while True:
            current, capture_obj = await self._run_one_attempt(
                current.id,
                root_task_id=root.id,
                excluded_provider_ids=exclusions,
            )

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

            capture = capture_obj
            captured_failure = bool(getattr(capture, "failed", False))
            if not captured_failure:
                # Preflight readiness failures are already terminal and must never be converted into
                # retries merely because a previous health snapshot looked transient.
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

            error = str(getattr(capture, "error", "") or current.error or "Codex attempt failed")
            failed_provider_id = int(getattr(capture, "provider_id", 0) or 0)
            decision = await decide_codex_retry(
                current,
                retry_number=retry_count + 1,
                failed_provider_id=failed_provider_id,
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

            # A non-root attempt is an audit record and may become terminal now. The logical root
            # stays non-terminal until the whole bounded chain succeeds or is exhausted.
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
            exclusions = decision.excluded_provider_ids

            # Cancellation requested against the logical root during backoff must prevent a queued
            # replay from starting.
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

            async def run_child() -> tuple[AgentTask, object | None]:
                return await self._run_one_attempt(
                    child.id,
                    root_task_id=root.id,
                    excluded_provider_ids=exclusions,
                )

            try:
                current, capture_obj = await run_retry_child_locked(child, run_child)
            except RuntimeError as exc:
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    child,
                    error=str(exc),
                    code="RETRY_CHILD_BUSY",
                    retry_count=retry_count,
                )

            # The child was executed above so the next loop iteration must evaluate its result,
            # not run it a second time. Keep the evaluation inline to preserve the task lock model.
            if current.status == "succeeded":
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
            if not bool(getattr(capture_obj, "failed", False)):
                return await finalize_logical_root_failure(
                    self.runtime,
                    root,
                    current,
                    error=current.error or "Codex retry attempt failed before Host start",
                    code="RETRY_PREFLIGHT_BLOCKED",
                    retry_count=retry_count,
                )

            # Evaluate the already-run child on the next pass without replaying it. We do that by
            # handling the failure decision here and only continue when another child is created.
            while True:
                error = str(getattr(capture_obj, "error", "") or current.error or "Codex attempt failed")
                failed_provider_id = int(getattr(capture_obj, "provider_id", 0) or 0)
                decision = await decide_codex_retry(
                    current,
                    retry_number=retry_count + 1,
                    failed_provider_id=failed_provider_id,
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
                await terminalize_task_failure(self.runtime, current.id, error)
                await discard_failed_attempt_worktree(self.runtime, current)
                child = await create_auto_retry_child(
                    self.runtime,
                    current,
                    retry_number=retry_count + 1,
                    decision=decision,
                )
                retry_count += 1
                exclusions = decision.excluded_provider_ids
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
                    current, capture_obj = await run_retry_child_locked(
                        child,
                        lambda: self._run_one_attempt(
                            child.id,
                            root_task_id=root.id,
                            excluded_provider_ids=exclusions,
                        ),
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
                if current.status == "succeeded":
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
                if not bool(getattr(capture_obj, "failed", False)):
                    return await finalize_logical_root_failure(
                        self.runtime,
                        root,
                        current,
                        error=current.error or "Codex retry attempt failed before Host start",
                        code="RETRY_PREFLIGHT_BLOCKED",
                        retry_count=retry_count,
                    )
                if retry_count >= MAX_AUTO_RETRIES:
                    # One final structured decision makes the terminal reason explicit instead of
                    # relying on a human error-string parser.
                    final_decision = await decide_codex_retry(
                        current,
                        retry_number=retry_count + 1,
                        failed_provider_id=int(getattr(capture_obj, "provider_id", 0) or 0),
                    )
                    return await finalize_logical_root_failure(
                        self.runtime,
                        root,
                        current,
                        error=str(getattr(capture_obj, "error", "") or current.error or "Codex attempt failed"),
                        code=final_decision.code,
                        retry_count=retry_count,
                    )
