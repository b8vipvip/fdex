from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_EXCLUDED_PROVIDER_IDS: ContextVar[frozenset[int]] = ContextVar(
    "fdex_codex_retry_excluded_provider_ids",
    default=frozenset(),
)


def excluded_codex_provider_ids() -> frozenset[int]:
    """Return Provider ids temporarily excluded for the current retry attempt.

    This context is deliberately task-local. It is only set around a *new* retry
    AgentTask/worktree boundary, so it can never change the Provider of an already
    started Codex Host/Turn.
    """

    return _EXCLUDED_PROVIDER_IDS.get()


@contextmanager
def codex_retry_provider_exclusions(provider_ids: set[int] | frozenset[int]) -> Iterator[None]:
    clean = frozenset(int(item) for item in provider_ids if int(item) > 0)
    token = _EXCLUDED_PROVIDER_IDS.set(clean)
    try:
        yield
    finally:
        _EXCLUDED_PROVIDER_IDS.reset(token)
