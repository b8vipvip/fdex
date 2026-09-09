from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_mcp_gateway, plugin_vercel
from app.plugin_runtime import effective_agent_grant


_installed = False


def _tool_definitions() -> dict[str, dict[str, Any]]:
    return {
        "vercel_list_projects": {
            "plugin_id": "vercel",
            "runtime_tool": "vercel.project.read",
            "risk": "read",
            "description": "List projects visible in the connected Vercel personal/team scope.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "cursor": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "vercel_list_deployments": {
            "plugin_id": "vercel",
            "runtime_tool": "vercel.deployment.read",
            "risk": "read",
            "description": "List Vercel deployments, optionally filtered by project, state, target or Git branch.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "state": {"type": "string"},
                    "target": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "vercel_read_deployment": {
            "plugin_id": "vercel",
            "runtime_tool": "vercel.deployment.read",
            "risk": "read",
            "description": "Read a sanitized Vercel deployment summary. FDEX intentionally strips environment variables and other private provider fields.",
            "inputSchema": {
                "type": "object",
                "properties": {"deployment_id": {"type": "string"}},
                "required": ["deployment_id"],
                "additionalProperties": False,
            },
        },
        "vercel_redeploy_preview": {
            "plugin_id": "vercel",
            "runtime_tool": "vercel.deployment.create",
            "risk": "write",
            "description": "Create a new Preview redeployment from an existing deployment in the same Vercel project. This never intentionally targets production.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "deployment_id": {"type": "string"},
                },
                "required": ["project_id", "deployment_id"],
                "additionalProperties": False,
            },
        },
        "vercel_promote_production": {
            "plugin_id": "vercel",
            "runtime_tool": "vercel.production.promote",
            "risk": "dangerous",
            "description": "HIGH RISK: point production traffic for a Vercel project to a READY deployment. Requires the task's human MCP write approval before FDEX executes it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "deployment_id": {"type": "string"},
                },
                "required": ["project_id", "deployment_id"],
                "additionalProperties": False,
            },
        },
        "vercel_rollback_production": {
            "plugin_id": "vercel",
            "runtime_tool": "vercel.production.rollback",
            "risk": "dangerous",
            "description": "HIGH RISK: point production traffic to a previous Vercel production deployment. Requires the task's human MCP write approval before FDEX executes it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "deployment_id": {"type": "string"},
                },
                "required": ["project_id", "deployment_id"],
                "additionalProperties": False,
            },
        },
    }


def _integer(arguments: dict[str, Any], name: str, default: int, maximum: int) -> int:
    raw = arguments.get(name, default)
    if isinstance(raw, bool):
        raise ValueError(f"参数 {name} 必须是整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {name} 必须是整数") from exc
    if value < 1 or value > maximum:
        raise ValueError(f"参数 {name} 超出允许范围")
    return value


def install_vercel_mcp_tools() -> None:
    """Install Vercel into the task-scoped MCP authorization, approval and audit path."""
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())

    original_authorized = plugin_mcp_gateway._authorized_tool_names
    original_catalog = plugin_mcp_gateway._tool_catalog
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result
    original_run_plugin_tool = plugin_mcp_gateway.run_plugin_tool

    @wraps(original_authorized)
    def authorized(owner_id: str, employee: dict[str, Any]) -> list[str]:
        # Harden the generic gateway now that Phase 7.51 introduces its first dangerous MCP tools.
        # Read grants must never inherit a dangerous tool merely because the older gateway only
        # special-cased the literal "write" risk.
        names = original_authorized(owner_id, employee)
        result: list[str] = []
        for name in names:
            spec = plugin_mcp_gateway._TOOL_DEFINITIONS.get(name, {})
            risk = str(spec.get("risk") or "read")
            plugin_id = str(spec.get("plugin_id") or "")
            mode = str(effective_agent_grant(owner_id, employee, plugin_id).get("mode") or "none") if plugin_id else "none"
            if risk in {"write", "dangerous"} and mode != "write":
                continue
            result.append(name)
        return result

    @wraps(original_catalog)
    def catalog(owner_id: str, employee: dict[str, Any]) -> list[dict[str, Any]]:
        rows = original_catalog(owner_id, employee)
        for row in rows:
            name = str(row.get("name") or "")
            spec = plugin_mcp_gateway._TOOL_DEFINITIONS.get(name, {})
            if str(spec.get("risk") or "") == "dangerous":
                annotations = row.get("annotations") if isinstance(row.get("annotations"), dict) else {}
                row["annotations"] = {
                    **annotations,
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": False,
                }
        return rows

    @wraps(original_run_plugin_tool)
    def mcp_run_plugin_tool(
        owner_id: str,
        employee: dict[str, Any],
        plugin_id: str,
        tool_name: str,
        executor: Callable[[], Any],
        **kwargs: Any,
    ) -> Any:
        # The local Plugin MCP capability is issued only to the official Codex app-server with
        # default_tools_approval_mode="writes". A dangerous tool reaches tools/call only after that
        # human approval. Convert that already-completed approval into Plugin Runtime's confirmed
        # bit instead of weakening authorize_plugin_tool globally.
        definition = plugin_mcp_gateway._TOOL_DEFINITIONS
        dangerous = any(
            str(spec.get("plugin_id") or "") == plugin_id
            and str(spec.get("runtime_tool") or "") == tool_name
            and str(spec.get("risk") or "") == "dangerous"
            for spec in definition.values()
        )
        return original_run_plugin_tool(
            owner_id,
            employee,
            plugin_id,
            tool_name,
            executor,
            confirmed=bool(dangerous),
            **kwargs,
        )

    @wraps(original_executor)
    def vercel_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        if tool_name == "vercel_list_projects":
            search = plugin_mcp_gateway._arg(arguments, "search", required=False, limit=240)
            limit = _integer(arguments, "limit", 50, 100)
            cursor = plugin_mcp_gateway._arg(arguments, "cursor", required=False, limit=240)
            return lambda: plugin_vercel.list_vercel_projects(owner_id, search, limit=limit, cursor=cursor)
        if tool_name == "vercel_list_deployments":
            project_id = plugin_mcp_gateway._arg(arguments, "project_id", required=False, limit=240)
            limit = _integer(arguments, "limit", 20, 100)
            state = plugin_mcp_gateway._arg(arguments, "state", required=False, limit=80)
            target = plugin_mcp_gateway._arg(arguments, "target", required=False, limit=80)
            branch = plugin_mcp_gateway._arg(arguments, "branch", required=False, limit=300)
            return lambda: plugin_vercel.list_vercel_deployments(
                owner_id, project_id=project_id, limit=limit, state=state, target=target, branch=branch,
            )
        if tool_name == "vercel_read_deployment":
            deployment_id = plugin_mcp_gateway._arg(arguments, "deployment_id", limit=240)
            return lambda: plugin_vercel.read_vercel_deployment(owner_id, deployment_id)
        if tool_name == "vercel_redeploy_preview":
            project_id = plugin_mcp_gateway._arg(arguments, "project_id", limit=240)
            deployment_id = plugin_mcp_gateway._arg(arguments, "deployment_id", limit=240)
            return lambda: plugin_vercel.redeploy_vercel_preview(owner_id, project_id, deployment_id)
        if tool_name == "vercel_promote_production":
            project_id = plugin_mcp_gateway._arg(arguments, "project_id", limit=240)
            deployment_id = plugin_mcp_gateway._arg(arguments, "deployment_id", limit=240)
            return lambda: plugin_vercel.promote_vercel_production(owner_id, project_id, deployment_id)
        if tool_name == "vercel_rollback_production":
            project_id = plugin_mcp_gateway._arg(arguments, "project_id", limit=240)
            deployment_id = plugin_mcp_gateway._arg(arguments, "deployment_id", limit=240)
            return lambda: plugin_vercel.rollback_vercel_production(owner_id, project_id, deployment_id)
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def vercel_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "vercel":
            allowed = {
                "plugin_id", "projects", "deployments", "deployment", "count", "pagination",
                "action", "project_id", "deployment_id",
            }
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._authorized_tool_names = authorized
    plugin_mcp_gateway._tool_catalog = catalog
    plugin_mcp_gateway.run_plugin_tool = mcp_run_plugin_tool
    plugin_mcp_gateway._tool_executor = vercel_executor
    plugin_mcp_gateway._safe_result = vercel_safe_result
    _installed = True
