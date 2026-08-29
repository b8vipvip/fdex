from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Iterator

from app.agent_runtime import AgentTask
from app.codex_interactive_client import InteractiveCodexAppServerClient
from app.codex_interactions import CodexInteractionBroker
from app.codex_mcp_elicitation import install_mcp_elicitation_compat

_current_broker: ContextVar[CodexInteractionBroker | None] = ContextVar(
    "fdex_codex_interaction_broker",
    default=None,
)
_installed = False


class ContextInteractiveCodexAppServerClient(InteractiveCodexAppServerClient):
    """Use the task-scoped broker while retaining the generic app-server constructor shape."""

    def __init__(self, *args: Any, server_request_handler: Any = None, **kwargs: Any) -> None:
        del server_request_handler
        broker = _current_broker.get()
        if broker is None:
            async def deny(_request_id: int | str, method: str, _params: dict[str, Any]) -> Any:
                from app.codex_app_server import CodexServerRequestDenied
                raise CodexServerRequestDenied(f"FDEX has no interaction scope for {method}")

            handler = deny
        else:
            handler = broker.handle
        super().__init__(*args, interactive_request_handler=handler, **kwargs)
        if broker is not None:
            broker.transport_alive = lambda: self.process is None or self.process.returncode is None


def install_codex_interaction_runtime() -> None:
    """Install the durable owner-scoped interactive Host behavior at the Phase 7.21 seam.

    Phase 7.21 intentionally isolated the official Host in ``codex_host_runtime``. Rather than
    duplicate that large lifecycle runner, Phase 7.23 replaces only its app-server client class
    and approval parameter helpers. Phase 7.24 extends the same durable broker with the official
    ``mcpServer/elicitation/request`` method while keeping unsupported server requests fail-closed.
    The replacement class is ContextVar-backed, so concurrent FDEX tasks in the same Uvicorn
    process never share a broker or owner scope.
    """
    global _installed
    # This registration is idempotent and intentionally runs before the Host patch guard so a
    # hot-reloaded worker cannot retain the Phase 7.23 method allow-list after Phase 7.24 loads.
    install_mcp_elicitation_compat()
    if _installed:
        return
    import app.codex_host_runtime as host

    original_common = host._thread_common_params
    original_turn_start = host.turn_start_params

    @wraps(original_common)
    def interactive_common(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = dict(original_common(*args, **kwargs))
        payload["approvalPolicy"] = "on-request"
        return payload

    @wraps(original_turn_start)
    def interactive_turn_start(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = dict(original_turn_start(*args, **kwargs))
        payload["approvalPolicy"] = "on-request"
        return payload

    host._thread_common_params = interactive_common
    host.turn_start_params = interactive_turn_start
    host.CodexAppServerClient = ContextInteractiveCodexAppServerClient
    _installed = True


@contextmanager
def codex_interaction_scope(task: AgentTask) -> Iterator[CodexInteractionBroker]:
    install_codex_interaction_runtime()
    broker = CodexInteractionBroker(task=task)
    token = _current_broker.set(broker)
    try:
        yield broker
    finally:
        _current_broker.reset(token)
        # Any still-pending answer belongs to a stdio Host that is now gone. Clearing encrypted
        # answer material here prevents a later Host session from consuming an obsolete decision.
        broker.store.interrupt_host(
            owner_id=task.owner_id,
            host_session_id=broker.host_session_id,
            reason="Codex Host session ended before the interaction response was consumed",
        )
