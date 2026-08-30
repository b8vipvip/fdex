from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Iterator

from app.codex_task_inputs import codex_task_input_store

_current_task: ContextVar[Any | None] = ContextVar("fdex_codex_input_task", default=None)
_installed = False


@contextmanager
def codex_task_input_scope(task: Any) -> Iterator[None]:
    token: Token[Any | None] = _current_task.set(task)
    try:
        yield
    finally:
        _current_task.reset(token)


def install_codex_task_input_runtime() -> None:
    """Install one schema-light UserInput bridge at the stable turn_start_params seam."""
    global _installed
    if _installed:
        return
    import app.codex_host_runtime as host
    from app.codex_engine import _codex_home

    original = host.turn_start_params

    def wrapped(thread_id: str, prompt: str) -> dict[str, Any]:
        payload = original(thread_id, prompt)
        task = _current_task.get()
        if task is None:
            return payload
        owner_id = str(getattr(task, "owner_id", "") or "")
        task_id = str(getattr(task, "id", "") or "")
        worktree_text = str(getattr(task, "worktree", "") or "")
        if not owner_id or not task_id or not worktree_text:
            return payload
        worktree = Path(worktree_text).expanduser().resolve()
        codex_home = _codex_home(owner_id)
        payload["input"] = codex_task_input_store().build_user_inputs(
            owner_id,
            task_id,
            prompt=prompt,
            worktree=worktree,
            codex_home=codex_home,
        )
        return payload

    host.turn_start_params = wrapped
    _installed = True
