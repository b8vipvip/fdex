from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.agent_runtime import AgentTask, FdexAgentRuntime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.codex_agent_health import run_codex_agent_health_check
from app.codex_host_store import codex_host_store

MAX_AUTO_RETRIES = 2
RETRY_BACKOFF_SECONDS = (2.0, 8.0)
RETRYABLE_HEALTH_CODES = frozenset(
    {
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_UNREACHABLE",
        "HOST_UNAVAILABLE",
    }
)
HARD_BLOCK_CODES = frozenset(
    {
        "AGENT_DISABLED",
        "RUNTIME_UNAVAILABLE",
        "PROCESS_ISOLATION_UNAVAILABLE",
        "PROVIDER_CONFIG_INVALID",
        "SMOKE_MISSING",
        "SMOKE_EXPIRED",
        "FINGERPRINT_MISMATCH",
        "COMPATIBILITY_INSUFFICIENT",
        "SMOKE_FAILED",
    }
)
_HEALTHY_LIVE_STATES = frozenset({"ok", "reachable"})
_TRANSIENT_LIVE_STATES = frozenset({"rate_limited", "unreachable", "upstream_error"})


@dataclass(slots=True)
class RetryAttemptCapture:
    task_id: str
    provider_id: int = 0
    failed: bool = False
    error: str = ""


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    code: str
    reason: str
    delay_seconds: float = 0.0
    failed_provider_id: int = 0
    excluded_provider_ids: frozenset[int] = field(default_factory=frozenset)
    health: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


_CAPTURE: ContextVar[RetryAttemptCapture | None] = ContextVar(
    "fdex_codex_retry_attempt_capture",
    default=None,
)
_ORIGINAL_FAIL_TASK = FdexAgentRuntime.fail_task
_CAPTURE_INSTALLED = False


def install_codex_retry_failure_capture() -> None:
    """Capture Host failures before they terminalize a logical root task.

    Existing Codex Host runners already converge on ``FdexAgentRuntime.fail_task``.  Instead of
    adding error-string classifiers to every Host layer, Phase 7.38 intercepts that single seam
    *only while an AgentLoop attempt is running*.  The human error text is retained for display,
    but retry eligibility is decided later from the structured Phase 7.37 health snapshot.

    Outside an explicit retry-attempt scope, ``fail_task`` keeps its original fail-closed behavior.
    """

    global _CAPTURE_INSTALLED
    if _CAPTURE_INSTALLED:
        return

    async def captured_fail_task(self: FdexAgentRuntime, task_id: str, error: str) -> AgentTask:
        capture = _CAPTURE.get()
        if capture is None or capture.task_id != task_id:
            return await _ORIGINAL_FAIL_TASK(self, task_id, error)

        task = await self.get_task(task_id)
        if task is None:
            return await _ORIGINAL_FAIL_TASK(self, task_id, error)
        if task.cancel_requested or await asyncio.to_thread(self.task_store.cancel_requested, task.id):
            return await _ORIGINAL_FAIL_TASK(self, task_id, error)

        capture.failed = True
        capture.error = str(error or "Codex attempt failed").strip()[:2000]
        # Keep this attempt non-terminal until the bounded controller has made its structured
        # retry decision.  This avoids reopening a terminal AgentTask later.
        task.status = "running"
        task.error = capture.error
        task.emit(
            "retry.attempt_failed",
            "Codex attempt failed; Phase 7.38 is evaluating structured health before terminalization",
        )
        return task

    FdexAgentRuntime.fail_task = captured_fail_task  # type: ignore[assignment]
    _CAPTURE_INSTALLED = True


@contextmanager
def capture_codex_attempt(task_id: str, provider_id: int = 0) -> Iterator[RetryAttemptCapture]:
    capture = RetryAttemptCapture(task_id=str(task_id), provider_id=max(0, int(provider_id or 0)))
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


async def terminalize_task_failure(runtime: FdexAgentRuntime, task_id: str, error: str) -> AgentTask:
    """Bypass capture and durably terminalize one task as failed."""

    return await _ORIGINAL_FAIL_TASK(runtime, task_id, error)


def _provider_live_code(health: dict[str, Any], provider_id: int) -> str:
    if provider_id <= 0:
        return ""
    for row in list(health.get("providers") or []):
        if not isinstance(row, dict) or int(row.get("provider_id") or 0) != provider_id:
            continue
        state = str(row.get("state") or "").strip().lower()
        if state == "rate_limited":
            return "PROVIDER_RATE_LIMITED"
        if state in {"unreachable", "upstream_error"}:
            return "PROVIDER_UNREACHABLE"
        # A metadata-only 401/403 remains advisory by Phase 7.37 policy and therefore does not
        # become an automatic retry signal here.
        return ""
    return ""


def _alternate_provider_exclusions(health: dict[str, Any], failed_provider_id: int) -> frozenset[int]:
    """Exclude the failed Provider only when a healthy fresh-full alternative exists."""

    if failed_provider_id <= 0:
        return frozenset()
    live_by_id = {
        int(row.get("provider_id") or 0): str(row.get("state") or "").strip().lower()
        for row in list(health.get("providers") or [])
        if isinstance(row, dict) and int(row.get("provider_id") or 0) > 0
    }
    for row in list(health.get("compatibility") or []):
        if not isinstance(row, dict) or not bool(row.get("eligible")):
            continue
        provider_id = int(row.get("provider_id") or 0)
        if provider_id <= 0 or provider_id == failed_provider_id:
            continue
        if live_by_id.get(provider_id, "") in _HEALTHY_LIVE_STATES:
            return frozenset({failed_provider_id})
    return frozenset()


def _side_effect_free(task: AgentTask) -> bool:
    return not bool(
        task.commit_sha
        or task.pushed
        or task.pr_url
        or task.changed_files
    )


async def decide_codex_retry(
    task: AgentTask,
    *,
    retry_number: int,
    failed_provider_id: int = 0,
) -> RetryDecision:
    """Return a bounded retry decision from structured health only.

    ``retry_number`` is 1-based: 1 means the first automatic retry after the original attempt.
    Human-readable task errors are intentionally not parsed.
    """

    if retry_number < 1 or retry_number > MAX_AUTO_RETRIES:
        return RetryDecision(False, "RETRY_LIMIT_REACHED", "automatic retry budget exhausted")
    if task.cancel_requested:
        return RetryDecision(False, "TASK_CANCELED", "task cancellation disables automatic retry")
    if not _side_effect_free(task):
        return RetryDecision(
            False,
            "SIDE_EFFECT_BOUNDARY_REACHED",
            "task already crossed the FDEX commit/publish boundary; automatic replay is forbidden",
            failed_provider_id=failed_provider_id,
        )

    try:
        health = await run_codex_agent_health_check(force_host=True)
    except Exception:
        return RetryDecision(
            False,
            "HEALTH_CHECK_UNAVAILABLE",
            "structured health refresh failed; fail closed instead of blind retry",
            failed_provider_id=failed_provider_id,
        )

    overall_code = str(health.get("code") or "UNKNOWN").strip().upper()
    if overall_code in HARD_BLOCK_CODES or str(health.get("state") or "").upper() == "BLOCKED":
        return RetryDecision(
            False,
            overall_code,
            str(health.get("reason") or "Codex health is hard-blocked")[:700],
            failed_provider_id=failed_provider_id,
            health=health,
        )

    provider_code = _provider_live_code(health, failed_provider_id)
    code = provider_code or overall_code
    if code not in RETRYABLE_HEALTH_CODES:
        return RetryDecision(
            False,
            code or "NOT_RETRYABLE",
            "failure is not backed by a structured transient Codex health signal",
            failed_provider_id=failed_provider_id,
            health=health,
        )

    delay = RETRY_BACKOFF_SECONDS[min(retry_number - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
    exclusions = _alternate_provider_exclusions(health, failed_provider_id)
    reason = str(health.get("reason") or code)[:700]
    if exclusions:
        reason += "; a healthy fresh-full alternative exists, so the failed Provider is excluded only for the new retry task"
    else:
        reason += "; no healthy fresh-full alternative was proven, so the new task may retry the same verified Provider after backoff"
    return RetryDecision(
        True,
        code,
        reason,
        delay_seconds=delay,
        failed_provider_id=failed_provider_id,
        excluded_provider_ids=exclusions,
        health=health,
    )


async def create_auto_retry_child(
    runtime: FdexAgentRuntime,
    source: AgentTask,
    *,
    retry_number: int,
    decision: RetryDecision,
) -> AgentTask:
    """Create a fresh AgentTask/worktree boundary and preserve Codex context by safe fork."""

    child = await runtime.create_task(
        source.prompt,
        owner_id=source.owner_id,
        project_id=source.project_id,
        parent_task_id=source.id,
    )
    child.emit(
        "retry.auto_attempt",
        f"Automatic Codex retry {retry_number}/{MAX_AUTO_RETRIES} · health={decision.code} · source={source.id}",
    )
    source.emit(
        "retry.auto_scheduled",
        f"Scheduled automatic retry child {child.id} after {decision.delay_seconds:.1f}s · health={decision.code}",
    )

    # If the failed task had durable context before the failed Turn, fork from the last completed
    # Turn.  This preserves conversation context while giving the retry a new Thread/task/worktree
    # boundary and therefore permits Provider reselection.  A brand-new failed Thread with no
    # completed Turn is intentionally not reused.
    try:
        store = codex_host_store()
        binding = await asyncio.to_thread(store.task_binding, source.owner_id, source.id)
        if binding is not None:
            thread_id = str(binding.get("thread_id") or "")
            thread = await asyncio.to_thread(store.get_thread, source.owner_id, thread_id)
            if thread is not None and str(thread.get("last_completed_turn_id") or ""):
                await asyncio.to_thread(
                    store.bind_task,
                    owner_id=source.owner_id,
                    task_id=child.id,
                    thread_id=thread_id,
                    relation="fork",
                    source_task_id=source.id,
                )
                child.emit("retry.context_fork", f"Retry will fork Codex thread {thread_id} from its last completed Turn")
    except (KeyError, ValueError, RuntimeError):
        # Context reuse is an optimization.  The retry task remains valid and will start a fresh
        # Thread when no safely completed checkpoint can be proven.
        pass
    return child


async def discard_failed_attempt_worktree(runtime: FdexAgentRuntime, task: AgentTask) -> None:
    """Discard only a pre-commit failed attempt before replaying it in a new worktree."""

    if not task.worktree or not _side_effect_free(task):
        return
    branch = str(task.branch or "")
    try:
        source, _worktrees, _base = await asyncio.to_thread(runtime._source_and_worktrees, task)
    except Exception:
        source = None
    try:
        await asyncio.to_thread(runtime._release_worktree, task)
    except Exception:
        # Leaving the failed worktree for operator inspection is safer than blocking a retry solely
        # because cleanup could not prove success.
        return
    if source is not None and branch:
        try:
            await asyncio.to_thread(runtime._run_command, ("git", "branch", "-D", branch), cwd=source)
            task.branch = ""
            task.emit("retry.attempt_branch_removed", "Removed discarded local retry-attempt branch")
        except Exception:
            pass


async def bind_logical_root_to_final_attempt(root: AgentTask, final_attempt: AgentTask) -> None:
    if root.id == final_attempt.id:
        return
    try:
        store = codex_host_store()
        binding = await asyncio.to_thread(store.task_binding, final_attempt.owner_id, final_attempt.id)
        if binding is None:
            return
        await asyncio.to_thread(
            store.bind_task,
            owner_id=root.owner_id,
            task_id=root.id,
            thread_id=str(binding.get("thread_id") or ""),
            relation="resume",
            source_task_id=final_attempt.id,
        )
    except (KeyError, ValueError, RuntimeError):
        return


async def complete_logical_root_from_retry(
    runtime: FdexAgentRuntime,
    root: AgentTask,
    final_attempt: AgentTask,
    *,
    retry_count: int,
) -> AgentTask:
    """Complete the non-terminal logical root from a successful child attempt."""

    if root.id == final_attempt.id:
        return final_attempt
    root.changed_files.update(final_attempt.changed_files)
    root.branch = final_attempt.branch
    root.commit_sha = final_attempt.commit_sha
    root.pushed = final_attempt.pushed
    root.pr_url = final_attempt.pr_url
    root.error = ""
    root.emit(
        "retry.auto_recovered",
        f"Recovered through {retry_count} bounded automatic retry attempt(s); final execution task={final_attempt.id}",
    )
    await bind_logical_root_to_final_attempt(root, final_attempt)
    return await runtime.complete_task(root.id, final_attempt.result)


async def finalize_logical_root_failure(
    runtime: FdexAgentRuntime,
    root: AgentTask,
    current: AgentTask,
    *,
    error: str,
    code: str,
    retry_count: int,
) -> AgentTask:
    clean_error = str(error or "Codex task failed").strip()[:2000]
    current.emit(
        "retry.auto_stopped",
        f"Automatic retry stopped after {retry_count} retry attempt(s) · code={code}",
    )
    if current.id != root.id:
        await terminalize_task_failure(runtime, current.id, clean_error)
        await bind_logical_root_to_final_attempt(root, current)
    root.emit(
        "retry.auto_exhausted" if retry_count >= MAX_AUTO_RETRIES else "retry.auto_suppressed",
        f"Final retry decision={code}; logical task is now terminal",
    )
    return await terminalize_task_failure(runtime, root.id, clean_error)


async def run_retry_child_locked(
    child: AgentTask,
    runner: Any,
) -> Any:
    """Run a retry child under its own durable cross-worker execution lock."""

    try:
        with agent_task_store().run_lock(child.id):
            return await runner()
    except TaskRunBusy:
        raise RuntimeError("automatic retry child is already owned by another worker")
