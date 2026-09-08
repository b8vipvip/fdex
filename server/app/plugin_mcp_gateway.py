from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.responses import Response as StarletteResponse

from app.config import fresh_settings
from app.plugin_agent_principals import plugin_agent_principal_store
from app.plugin_code_hosts import (
    CodeHostPluginError,
    create_code_host_pull_request,
    list_code_host_repositories,
    read_code_host_file,
    write_code_host_file,
)
from app.plugin_runtime import effective_agent_grant, plugin_connection_status, run_plugin_tool
from app.remote_mcp_gateway import _direct_loopback_client

router = APIRouter(prefix="/internal/fdex-plugin-mcp", include_in_schema=False)

_CAPABILITY_HEADER = "X-FDEX-Plugin-Capability"
_MAX_BODY = 512 * 1024
_MAX_RESULT_TEXT = 512 * 1024
_LEASE_HOURS = 6
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-mcp-leases.db"


_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "gitlab_list_repositories": {
        "plugin_id": "gitlab",
        "runtime_tool": "gitlab.repositories.list",
        "risk": "read",
        "description": "List repositories/projects accessible through the user's connected GitLab.com plugin.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "gitlab_read_file": {
        "plugin_id": "gitlab",
        "runtime_tool": "gitlab.repository.read",
        "risk": "read",
        "description": "Read one UTF-8 project file from GitLab.com.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "namespace/project"},
                "path": {"type": "string"},
                "ref": {"type": "string", "description": "branch/tag/commit; defaults to HEAD"},
            },
            "required": ["repository", "path"],
            "additionalProperties": False,
        },
    },
    "gitlab_write_file": {
        "plugin_id": "gitlab",
        "runtime_tool": "gitlab.repository.write",
        "risk": "write",
        "description": "Create or update one UTF-8 project file on a GitLab branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "namespace/project"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "branch": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["repository", "path", "content", "branch", "message"],
            "additionalProperties": False,
        },
    },
    "gitlab_create_merge_request": {
        "plugin_id": "gitlab",
        "runtime_tool": "gitlab.merge_request.write",
        "risk": "write",
        "description": "Create a GitLab Merge Request between existing branches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "namespace/project"},
                "source_branch": {"type": "string"},
                "target_branch": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["repository", "source_branch", "target_branch", "title"],
            "additionalProperties": False,
        },
    },
    "gitee_list_repositories": {
        "plugin_id": "gitee",
        "runtime_tool": "gitee.repositories.list",
        "risk": "read",
        "description": "List repositories accessible through the user's connected Gitee plugin.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "gitee_read_file": {
        "plugin_id": "gitee",
        "runtime_tool": "gitee.repository.read",
        "risk": "read",
        "description": "Read one UTF-8 file from a Gitee repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "owner/repo"},
                "path": {"type": "string"},
                "ref": {"type": "string", "description": "branch/tag/commit; defaults to HEAD"},
            },
            "required": ["repository", "path"],
            "additionalProperties": False,
        },
    },
    "gitee_write_file": {
        "plugin_id": "gitee",
        "runtime_tool": "gitee.repository.write",
        "risk": "write",
        "description": "Create or update one UTF-8 file on a Gitee branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "owner/repo"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "branch": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["repository", "path", "content", "branch", "message"],
            "additionalProperties": False,
        },
    },
    "gitee_create_pull_request": {
        "plugin_id": "gitee",
        "runtime_tool": "gitee.pull_request.write",
        "risk": "write",
        "description": "Create a Gitee Pull Request between existing branches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "owner/repo"},
                "source_branch": {"type": "string"},
                "target_branch": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["repository", "source_branch", "target_branch", "title"],
            "additionalProperties": False,
        },
    },
}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PluginMcpLeaseStore:
    """Cross-worker, task-scoped capabilities for the local native-plugin MCP server."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path.resolve()
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS plugin_mcp_leases (
                       id TEXT PRIMARY KEY,
                       owner_id TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       employee_id INTEGER NOT NULL,
                       token_hash TEXT NOT NULL,
                       state TEXT NOT NULL DEFAULT 'active',
                       created_at TEXT NOT NULL,
                       expires_at TEXT NOT NULL,
                       last_used_at TEXT NOT NULL DEFAULT '',
                       revoked_at TEXT NOT NULL DEFAULT ''
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_plugin_mcp_task ON plugin_mcp_leases(owner_id,task_id,state,expires_at)"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._initialized = True

    def issue(self, owner_id: str, task_id: str, employee_id: int) -> tuple[dict[str, Any], str]:
        self.init()
        self.revoke_task(owner_id, task_id)
        raw = secrets.token_urlsafe(32)
        lease_id = f"pml_{secrets.token_hex(16)}"
        now = _now()
        expires = now + timedelta(hours=_LEASE_HOURS)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """INSERT INTO plugin_mcp_leases(
                       id,owner_id,task_id,employee_id,token_hash,state,created_at,expires_at,last_used_at,revoked_at
                   ) VALUES(?,?,?,?,?,'active',?,?,?,'')""",
                (lease_id, owner_id, task_id, int(employee_id), _token_hash(raw), _iso(now), _iso(expires), ""),
            )
        return {
            "id": lease_id,
            "owner_id": owner_id,
            "task_id": task_id,
            "employee_id": int(employee_id),
            "expires_at": _iso(expires),
        }, raw

    def resolve(self, lease_id: str, token: str) -> dict[str, Any] | None:
        self.init()
        if not lease_id.startswith("pml_") or len(token or "") < 32:
            return None
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM plugin_mcp_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None or str(row["state"]) != "active":
                return None
            now = _now()
            if _parse_time(str(row["expires_at"])) <= now:
                conn.execute(
                    "UPDATE plugin_mcp_leases SET state='expired',revoked_at=? WHERE id=? AND state='active'",
                    (_iso(now), lease_id),
                )
                return None
            if not hmac.compare_digest(str(row["token_hash"]), _token_hash(token)):
                return None
            owner_id = str(row["owner_id"])
            task_id = str(row["task_id"])
            employee_id = int(row["employee_id"])
            binding = plugin_agent_principal_store().resolve(owner_id, task_id)
            if binding is None or int(binding.get("employee_id") or 0) != employee_id:
                conn.execute(
                    "UPDATE plugin_mcp_leases SET state='revoked',revoked_at=? WHERE id=? AND state='active'",
                    (_iso(now), lease_id),
                )
                return None
            employee = plugin_agent_principal_store().active_employee(owner_id, task_id)
            if employee is None or int(employee.get("id") or 0) != employee_id:
                conn.execute(
                    "UPDATE plugin_mcp_leases SET state='revoked',revoked_at=? WHERE id=? AND state='active'",
                    (_iso(now), lease_id),
                )
                return None
            conn.execute(
                "UPDATE plugin_mcp_leases SET last_used_at=? WHERE id=? AND state='active'",
                (_iso(now), lease_id),
            )
        return {
            "id": lease_id,
            "owner_id": owner_id,
            "task_id": task_id,
            "employee_id": employee_id,
            "employee": employee,
            "expires_at": str(row["expires_at"]),
        }

    def revoke_task(self, owner_id: str, task_id: str) -> int:
        self.init()
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                """UPDATE plugin_mcp_leases SET state='revoked',revoked_at=?
                   WHERE owner_id=? AND task_id=? AND state='active'""",
                (_iso(_now()), owner_id, task_id),
            )
        return max(0, int(cur.rowcount or 0))

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute("DELETE FROM plugin_mcp_leases WHERE owner_id=?", (owner_id,))
        return max(0, int(cur.rowcount or 0))

    def purge_expired(self) -> int:
        self.init()
        now = _iso(_now())
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                """UPDATE plugin_mcp_leases SET state='expired',revoked_at=?
                   WHERE state='active' AND expires_at<=?""",
                (now, now),
            )
        return max(0, int(cur.rowcount or 0))


@lru_cache(maxsize=1)
def plugin_mcp_lease_store() -> PluginMcpLeaseStore:
    store = PluginMcpLeaseStore()
    store.init()
    return store


def _authorized_tool_names(owner_id: str, employee: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for name, spec in _TOOL_DEFINITIONS.items():
        plugin_id = str(spec["plugin_id"])
        status = plugin_connection_status(owner_id, plugin_id)
        if not bool(status.get("connected")):
            continue
        mode = str(effective_agent_grant(owner_id, employee, plugin_id).get("mode") or "none")
        if mode == "none":
            continue
        if str(spec["risk"]) == "write" and mode != "write":
            continue
        result.append(name)
    return result


def build_codex_plugin_mcp_config(owner_id: str, task_id: str) -> dict[str, dict[str, Any]]:
    """Create one loopback MCP server only when this task has a bound 智体 and usable grants."""
    principal = plugin_agent_principal_store().resolve(owner_id, task_id)
    if principal is None:
        plugin_mcp_lease_store().revoke_task(owner_id, task_id)
        return {}
    employee = plugin_agent_principal_store().active_employee(owner_id, task_id)
    if employee is None:
        plugin_mcp_lease_store().revoke_task(owner_id, task_id)
        return {}
    enabled_tools = _authorized_tool_names(owner_id, employee)
    if not enabled_tools:
        plugin_mcp_lease_store().revoke_task(owner_id, task_id)
        return {}
    port = int(fresh_settings().fdex_port)
    if not 1 <= port <= 65535:
        raise ValueError("FDEX internal HTTP port is invalid")
    lease, token = plugin_mcp_lease_store().issue(owner_id, task_id, int(employee["id"]))
    return {
        "fdex_plugins": {
            "url": f"http://127.0.0.1:{port}/internal/fdex-plugin-mcp/{lease['id']}",
            "http_headers": {_CAPABILITY_HEADER: token},
            "enabled": True,
            "required": False,
            "startup_timeout_sec": 10,
            "tool_timeout_sec": 60,
            "enabled_tools": enabled_tools,
            # Codex app-server owns the human approval flow for non-read-only MCP tools.
            "default_tools_approval_mode": "writes",
        }
    }


def revoke_codex_plugin_mcp_task(owner_id: str, task_id: str) -> int:
    return plugin_mcp_lease_store().revoke_task(owner_id, task_id)


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": int(code), "message": str(message)[:500]}}


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_catalog(owner_id: str, employee: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = set(_authorized_tool_names(owner_id, employee))
    rows: list[dict[str, Any]] = []
    for name, spec in _TOOL_DEFINITIONS.items():
        if name not in allowed:
            continue
        read_only = str(spec["risk"]) == "read"
        rows.append(
            {
                "name": name,
                "description": str(spec["description"]),
                "inputSchema": spec["inputSchema"],
                "annotations": {
                    "readOnlyHint": read_only,
                    "destructiveHint": False,
                    "idempotentHint": read_only,
                },
            }
        )
    return rows


def _arg(arguments: dict[str, Any], name: str, *, required: bool = True, limit: int = 2000000) -> str:
    value = str(arguments.get(name) or "")
    if required and not value.strip():
        raise ValueError(f"缺少参数：{name}")
    if len(value) > limit:
        raise ValueError(f"参数过长：{name}")
    return value


def _tool_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
    spec = _TOOL_DEFINITIONS[tool_name]
    plugin_id = str(spec["plugin_id"])
    if tool_name.endswith("_list_repositories"):
        return lambda: list_code_host_repositories(owner_id, plugin_id)
    if tool_name.endswith("_read_file"):
        repository = _arg(arguments, "repository", limit=400)
        path = _arg(arguments, "path", limit=1000)
        ref = _arg(arguments, "ref", required=False, limit=180)
        return lambda: read_code_host_file(owner_id, plugin_id, repository, path, ref=ref)
    if tool_name.endswith("_write_file"):
        repository = _arg(arguments, "repository", limit=400)
        path = _arg(arguments, "path", limit=1000)
        content = _arg(arguments, "content", required=False, limit=2 * 1024 * 1024)
        branch = _arg(arguments, "branch", limit=180)
        message = _arg(arguments, "message", limit=500)
        return lambda: write_code_host_file(
            owner_id,
            plugin_id,
            repository,
            path,
            content,
            branch=branch,
            message=message,
        )
    if tool_name in {"gitlab_create_merge_request", "gitee_create_pull_request"}:
        repository = _arg(arguments, "repository", limit=400)
        source = _arg(arguments, "source_branch", limit=180)
        target = _arg(arguments, "target_branch", limit=180)
        title = _arg(arguments, "title", limit=300)
        description = _arg(arguments, "description", required=False, limit=10000)
        return lambda: create_code_host_pull_request(
            owner_id,
            plugin_id,
            repository,
            source_branch=source,
            target_branch=target,
            title=title,
            description=description,
        )
    raise ValueError("未知 FDEX 插件 Tool")


def _safe_result(value: Any) -> Any:
    """Strip large/provider-specific write response bodies before returning data to Codex."""
    if isinstance(value, list):
        return [
            {
                key: item.get(key)
                for key in ("full_name", "private", "default_branch", "archived", "updated_at", "can_push", "can_pr")
            }
            for item in value[:2000]
            if isinstance(item, dict)
        ]
    if not isinstance(value, dict):
        return value
    if "content" in value and "path" in value:
        return {
            key: value.get(key)
            for key in ("plugin_id", "repository", "path", "ref", "sha", "size", "content")
        }
    return {
        key: value.get(key)
        for key in ("plugin_id", "repository", "path", "branch", "source_branch", "target_branch", "url", "number")
        if key in value
    }


def _tool_call(lease: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    employee = lease["employee"]
    owner_id = str(lease["owner_id"])
    allowed = set(_authorized_tool_names(owner_id, employee))
    if name not in allowed or name not in _TOOL_DEFINITIONS:
        return {
            "content": [{"type": "text", "text": f"FDEX plugin tool is not currently authorized: {name}"}],
            "isError": True,
        }
    spec = _TOOL_DEFINITIONS[name]
    try:
        value = run_plugin_tool(
            owner_id,
            employee,
            str(spec["plugin_id"]),
            str(spec["runtime_tool"]),
            _tool_executor(owner_id, name, arguments),
        )
        safe = _safe_result(value)
        text = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        if len(text) > _MAX_RESULT_TEXT:
            text = text[:_MAX_RESULT_TEXT] + "\n[FDEX MCP result truncated]"
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except (CodeHostPluginError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
        return {
            "content": [{"type": "text", "text": f"{type(exc).__name__}: {str(exc)[:1500]}"}],
            "isError": True,
        }


def _handle_one(lease: dict[str, Any], message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "invalid JSON-RPC request")
    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "2025-06-18")
        return _rpc_result(
            request_id,
            {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fdex-native-plugins", "version": "1.0.0"},
                "instructions": (
                    "Use only when the user's request requires an explicitly connected FDEX plugin. "
                    "Read tools may run directly; write tools remain subject to Codex/FDEX write approval."
                ),
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _tool_catalog(str(lease["owner_id"]), lease["employee"])})
    if method == "tools/call":
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not name:
            return _rpc_error(request_id, -32602, "tools/call missing name")
        return _rpc_result(request_id, _tool_call(lease, name, arguments))
    return _rpc_error(request_id, -32601, f"method not found: {method}")


@router.api_route("/{lease_id}", methods=["POST", "GET", "DELETE"], response_model=None)
async def fdex_plugin_mcp(lease_id: str, request: Request) -> StarletteResponse:
    # Same-host reverse proxies can make external traffic appear to originate at 127.0.0.1. Require
    # the hardened direct-loopback rule plus an unguessable per-task capability header.
    if not _direct_loopback_client(request):
        return PlainTextResponse("not found", status_code=404)
    token = str(request.headers.get(_CAPABILITY_HEADER) or "")
    lease = plugin_mcp_lease_store().resolve(lease_id, token)
    if lease is None:
        return PlainTextResponse("not found", status_code=404)
    if request.method == "DELETE":
        return Response(status_code=204)
    if request.method == "GET":
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})
    length = request.headers.get("content-length", "").strip()
    if length:
        try:
            if int(length) > _MAX_BODY:
                return PlainTextResponse("request too large", status_code=413)
        except ValueError:
            return PlainTextResponse("invalid content-length", status_code=400)
    body = await request.body()
    if len(body) > _MAX_BODY:
        return PlainTextResponse("request too large", status_code=413)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
    if isinstance(payload, list):
        if not payload:
            return JSONResponse(_rpc_error(None, -32600, "empty batch"), status_code=400)
        responses = [item for message in payload if (item := _handle_one(lease, message)) is not None]
        return JSONResponse(responses, headers={"Cache-Control": "no-store"}) if responses else Response(status_code=202)
    result = _handle_one(lease, payload)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
