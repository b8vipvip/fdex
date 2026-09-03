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


@dataclass(slots=True)
class RetryAttemptCapture:
    task_id: str
    root_task_id: str
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
_ORIGINAL_RAISE_IF_CANCELLED = FdexAgentRuntime._raise_if_cancelled
_CAPTURE_INSTALLED = False


async def _root_cancel_requested(runtime: FdexAgentRuntime, capture: RetryAttemptCapture) -> bool:
    root_task_id = str(capture.root_task_id or "")
    if not root_task_id or root_task_id == capture.task_id:
        return False
    try:
        return bool(await asyncio.to_thread(runtime.task_store.cancel_requested, root_task_id))
    except (KeyError, ValueError):
        return False


def install_codex_retry_failure_capture() -> None:
    """Capture Host failures before terminalizing a logical task and propagate root cancellation.

    Existing Codex Host layers converge on ``FdexAgentRuntime.fail_task`` and regularly call
    ``_raise_if_cancelled``. Phase 7.38 wraps those two seams only while an explicit retry-attempt
    context is active. Outside that context the original fail-closed runtime behavior is unchanged.

    Human error text is kept for diagnostics, but retry eligibility is never inferred from it; the
    controller refreshes Phase 7.37 structured health after the failed Host exits.
    """

    global _CAPTURE_INSTALLED
    if _CAPTURE_INSTALLED:
        return

    async def captured_raise_if_cancelled(self: FdexAgentRuntime, task: AgentTask) -> None:
        capture = _CAPTURE.get()
        if capture is not None and capture.task_id == task.id:
            if await _root_cancel_requested(self, capture):
                # A user cancels the logical/root task, while automatic retries execute in child
                # tasks. Mirror that cancellation into the active child before delegating to the
                # runtime's normal safe-boundary cancellation machinery.
                task.cancel_requested = True
        await _ORIGINAL_RAISE_IF_CANCELLED(self, task)

    async def captured_fail_task(self: FdexAgentRuntime, task_id: str, error: str) -> AgentTask:
        capture = _CAPTURE.get()
        if capture is None or capture.task_id != task_id:
            return await _ORIGINAL_FAIL_TASK(self, task_id, error)

        task = await self.get_task(task_id)
        if task is None:
            return await _ORIGINAL_FAIL_TASK(self, task_id, error)
        if await _root_cancel_requested(self, capture):
            task.cancel_requested = True
        if task.cancel_requested or await asyncio.to_thread(self.task_store.cancel_requested, task.id):
            return await _ORIGINAL_FAIL_TASK(self, task_id, error)

        capture.failed = True
        capture.error = str(error or "Codex attempt failed").strip()[:2000]
        # Keep this attempt non-terminal until the bounded controller has made its structured
        # retry decision. This avoids ever reopening a terminal AgentTask.
        task.status = "running"
        task.error = capture.error
        task.emit(
            "retry.attempt_failed",
            "Codex attempt failed; Phase 7.38 is evaluating structured health before terminalization",
        )
        return task

    FdexAgentRuntime._raise_if_cancelled = captured_raise_if_cancelled  # type: ignore[assignment]
    FdexAgentRuntime.fail_task = captured_fail_task  # type: ignore[assignment]
    _CAPTURE_INSTALLED = True


@contextmanager
def capture_codex_attempt(
    task_id: str,
    *,
    root_task_id: str,
    provider_id: int = 0,
) -> Iterator[RetryAttemptCapture]:
    capture = RetryAttemptCapture(
        task_id=str(task_id),
        root_task_id=str(root_task_id or task_id),
        provider_id=max(0, int(provider_id or 0)),
    )
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


async def terminalize_task_failure(runtime: FdexAgentRuntime, task_id: str, error: str) -> AgentTask:
    """Bypass retry capture and durably terminalize one task."""

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
        # Phase 7.37 deliberately treats metadata-only /models 401/403 as advisory, so it is not
        # promoted into an automatic replay signal here.
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
    return not bool(task.commit_sha or task.pushed or task.pr_url or task.changed_files)


async def decide_codex_retry(
    task: AgentTask,
    *,
    retry_number: int,
    failed_provider_id: int = 0,
) -> RetryDecision:
    """Return a bounded retry decision using structured health only.

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


def _safe_retry_checkpoint(source: AgentTask) -> tuple[str, str] | None:
    """Walk the parent chain and find the nearest Thread with a completed Turn checkpoint."""

    store = codex_host_store()
    task_id = source.id
    owner_id = source.owner_id
    seen: set[str] = set()
    for _ in range(12):
        if not task_id or task_id in seen:
            break
        seen.add(task_id)
        try:
            binding = store.task_binding(owner_id, task_id)
        except (KeyError, ValueError, RuntimeError):
            binding = None
        if binding is not None:
            thread_id = str(binding.get("thread_id") or "")
            try:
                thread = store.get_thread(owner_id, thread_id) if thread_id else None
            except (KeyError, ValueError, RuntimeError):
                thread = None
            if thread is not None and str(thread.get("last_completed_turn_id") or ""):
                return task_id, thread_id
        try:
            row = agent_task_store().get(owner_id, task_id)
        except ValueError:
            row = None
        task_id = str((row or {}).get("parent_task_id") or "")
    return None


async def create_auto_retry_child(
    runtime: FdexAgentRuntime,
    source: AgentTask,
    *,
    retry_number: int,
    decision: RetryDecision,
) -> AgentTask:
    """Create a fresh AgentTask/worktree boundary and preserve context by safe fork."""

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

    # Fork only from a proven completed Turn. If the immediately failed child owns a new Thread
    # whose first Turn never completed, walk its parent chain and reuse the nearest safe checkpoint.
    checkpoint = await asyncio.to_thread(_safe_retry_checkpoint, source)
    if checkpoint is not None:
        checkpoint_task_id, thread_id = checkpoint
        try:
            await asyncio.to_thread(
                codex_host_store().bind_task,
                owner_id=source.owner_id,
                task_id=child.id,
                thread_id=thread_id,
                relation="fork",
                source_task_id=checkpoint_task_id,
            )
            child.emit(
                "retry.context_fork",
                f"Retry will fork Codex thread {thread_id} from its last completed Turn",
            )
        except (KeyError, ValueError, RuntimeError):
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
        # Leaving the failed worktree for operator inspection is safer than deleting anything when
        # cleanup itself cannot prove the configured worktree boundary.
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
    """Complete the non-terminal logical root from a successful child attempt.

    The logical root copies publication metadata but does not take ownership of the child's
    worktree. This keeps cleanup single-owner and prevents two terminal task rows from referencing
    the same filesystem path.
    """

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
        # Do not rebind the logical root to a failed child Thread. If the root was created as a
        # continuation, its existing binding remains the last stable completed context; if it was a
        # new Thread with no completed Turn, leaving that binding untouched is still safer than
        # making a failed retry child the future chat continuation source.
    root.emit(
        "retry.auto_exhausted" if code == "RETRY_LIMIT_REACHED" else "retry.auto_suppressed",
        f"Final retry decision={code}; logical task is now terminal",
    )
    return await terminalize_task_failure(runtime, root.id, clean_error)


async def run_retry_child_locked(child: AgentTask, runner: Any) -> Any:
    """Run a retry child under its own durable cross-worker execution lock."""

    try:
        with agent_task_store().run_lock(child.id):
            return await runner()
    except TaskRunBusy as exc:
        raise RuntimeError("automatic retry child is already owned by another worker") from exc
