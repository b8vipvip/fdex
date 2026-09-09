from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Iterator

from app.plugin_feishu_runtime import install_feishu_runtime

# Feishu must be promoted from roadmap to native before Plugin MCP imports its connection-status
# function by value. This keeps catalog, authorization and MCP availability on one runtime truth.
install_feishu_runtime()

from app.plugin_code_host_workflow import install_code_host_workflow_tools
from app.plugin_feishu_mcp import install_feishu_mcp_tools
from app.plugin_mcp_gateway import build_codex_plugin_mcp_config, revoke_codex_plugin_mcp_task
from app.remote_mcp_gateway import build_codex_remote_mcp_config, remote_mcp_lease_store

install_feishu_mcp_tools()
install_code_host_workflow_tools()

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

    Phase 7.43 also merges the native Plugin Runtime MCP surface. Phase 7.45 extends that same
    capability path with bounded repository-tree reads plus an enforced fdex/* work-branch flow for
    GitLab/Gitee writes and MR/PR creation. Phase 7.46 adds the native Feishu adapter for bounded
    chat/message/document reads plus approved message/document writes. Codex still sees only
    loopback capability URLs: third-party credentials stay in FDEX, and the plugin server
    dynamically re-checks the initiating 智体's current connection/grant before every tools/list and
    tools/call.
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
            # credential material stay owned by the FDEX gateway/control plane.
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
    try:
        plugin_servers = build_codex_plugin_mcp_config(owner_id, task_id)
    except ValueError as exc:
        # Phase 7.26 predates AgentTask's production 32-hex id contract and some callers/tests use
        # opaque task labels solely to exercise Remote MCP. Plugin MCP is an optional augmentation:
        # an id that cannot possibly resolve to a plugin principal must mean "no plugin capability",
        # not break an otherwise valid Remote MCP scope. Preserve every other configuration error.
        if str(exc) != "Agent task id is invalid":
            raise
        plugin_servers = {}
    collision = set(servers).intersection(plugin_servers)
    if collision:
        raise RuntimeError(f"FDEX MCP server name collision: {sorted(collision)}")
    servers.update(plugin_servers)
    token = _current_servers.set(servers)
    try:
        yield servers
    finally:
        _current_servers.reset(token)
        # Localhost capabilities die with this FDEX task even if the official Codex Thread is
        # durable and later resumed. A continuation receives fresh capabilities bound to its own
        # task id. Crashed-worker leases also have fixed six-hour expiries and startup cleanup.
        remote_mcp_lease_store().revoke_task(owner_id, task_id)
        revoke_codex_plugin_mcp_task(owner_id, task_id)
