from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Iterator

from app.remote_mcp_gateway import build_codex_remote_mcp_config, remote_mcp_lease_store

_current_servers: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "fdex_codex_remote_mcp_servers",
    default=None,
)
_installed = False


def install_codex_remote_mcp_runtime() -> None:
    """Add only FDEX-local capability URLs to the official per-Thread config.

    The Phase 7.21 Host already centralizes all thread config in one helper. Wrapping that helper's
    imported `_codex_thread_config` avoids duplicating the durable Thread/Turn runner. The ContextVar
    is task-local, so concurrent tasks owned by the same or different Center users never share
    leases or MCP configuration.
    """
    global _installed
    if _installed:
        return
    import app.codex_host_runtime as host

    original = host._codex_thread_config

    @wraps(original)
    def with_remote_mcp(*args: Any, **kwargs: Any) -> dict[str, object]:
        payload = dict(original(*args, **kwargs))
        servers = _current_servers.get()
        if servers:
            # Codex sees only loopback capability URLs. The original user URL, DNS answer and any
            # future credential material stay owned by the FDEX gateway/control plane.
            payload["mcp_servers"] = servers
        else:
            payload.pop("mcp_servers", None)
            payload.pop("mcpServers", None)
        return payload

    host._codex_thread_config = with_remote_mcp
    _installed = True


@contextmanager
def codex_remote_mcp_scope(owner_id: str, task_id: str) -> Iterator[dict[str, dict[str, Any]]]:
    install_codex_remote_mcp_runtime()
    servers = build_codex_remote_mcp_config(owner_id, task_id)
    token = _current_servers.set(servers)
    try:
        yield servers
    finally:
        _current_servers.reset(token)
        # The localhost capability dies with this FDEX task even if the official Codex Thread is
        # durable and later resumed. A continuation receives fresh capabilities bound to its own
        # task id, while a crashed worker's old leases also have a fixed six-hour expiry.
        remote_mcp_lease_store().revoke_task(owner_id, task_id)
