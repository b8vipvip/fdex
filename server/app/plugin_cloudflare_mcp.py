from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_cloudflare, plugin_mcp_gateway


_installed = False


def install_cloudflare_mcp_tools() -> None:
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update({
        "cloudflare_pages_list_projects": {"plugin_id": "cloudflare", "runtime_tool": "cloudflare.pages.project.read", "risk": "read", "description": "List Cloudflare Pages projects in the connected account.", "inputSchema": {"type": "object", "properties": {"page": {"type": "integer"}, "per_page": {"type": "integer"}}, "additionalProperties": False}},
        "cloudflare_pages_list_deployments": {"plugin_id": "cloudflare", "runtime_tool": "cloudflare.pages.deployment.read", "risk": "read", "description": "List Cloudflare Pages deployments for a project.", "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}, "environment": {"type": "string"}, "page": {"type": "integer"}, "per_page": {"type": "integer"}}, "required": ["project_name"], "additionalProperties": False}},
        "cloudflare_pages_read_deployment": {"plugin_id": "cloudflare", "runtime_tool": "cloudflare.pages.deployment.read", "risk": "read", "description": "Read a sanitized Cloudflare Pages deployment summary.", "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}, "deployment_id": {"type": "string"}}, "required": ["project_name", "deployment_id"], "additionalProperties": False}},
        "cloudflare_pages_read_logs": {"plugin_id": "cloudflare", "runtime_tool": "cloudflare.pages.deployment.read", "risk": "read", "description": "Read up to 500 bounded Cloudflare Pages build/deployment log lines.", "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}, "deployment_id": {"type": "string"}}, "required": ["project_name", "deployment_id"], "additionalProperties": False}},
        "cloudflare_pages_retry_deployment": {"plugin_id": "cloudflare", "runtime_tool": "cloudflare.pages.deployment.retry", "risk": "write", "description": "Retry an existing Cloudflare Pages deployment after human MCP write approval.", "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}, "deployment_id": {"type": "string"}}, "required": ["project_name", "deployment_id"], "additionalProperties": False}},
        "cloudflare_pages_rollback_production": {"plugin_id": "cloudflare", "runtime_tool": "cloudflare.pages.production.rollback", "risk": "dangerous", "description": "HIGH RISK: rollback Cloudflare Pages production to a previous successful production deployment. Requires explicit human MCP approval.", "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}, "deployment_id": {"type": "string"}}, "required": ["project_name", "deployment_id"], "additionalProperties": False}},
    })
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    def integer(arguments: dict[str, Any], name: str, default: int, maximum: int) -> int:
        raw = arguments.get(name, default)
        if isinstance(raw, bool): raise ValueError(f"参数 {name} 必须是整数")
        try: value = int(raw)
        except (TypeError, ValueError) as exc: raise ValueError(f"参数 {name} 必须是整数") from exc
        if value < 1 or value > maximum: raise ValueError(f"参数 {name} 超出允许范围")
        return value

    @wraps(original_executor)
    def executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        project = lambda: plugin_mcp_gateway._arg(arguments, "project_name", limit=128)
        deployment = lambda: plugin_mcp_gateway._arg(arguments, "deployment_id", limit=200)
        if tool_name == "cloudflare_pages_list_projects":
            page = integer(arguments, "page", 1, 100000); per_page = integer(arguments, "per_page", 50, 100)
            return lambda: plugin_cloudflare.list_cloudflare_pages_projects(owner_id, page=page, per_page=per_page)
        if tool_name == "cloudflare_pages_list_deployments":
            name = project(); env = plugin_mcp_gateway._arg(arguments, "environment", required=False, limit=40); page = integer(arguments, "page", 1, 100000); per_page = integer(arguments, "per_page", 25, 100)
            return lambda: plugin_cloudflare.list_cloudflare_pages_deployments(owner_id, name, environment=env, page=page, per_page=per_page)
        if tool_name == "cloudflare_pages_read_deployment":
            name = project(); dep = deployment(); return lambda: plugin_cloudflare.read_cloudflare_pages_deployment(owner_id, name, dep)
        if tool_name == "cloudflare_pages_read_logs":
            name = project(); dep = deployment(); return lambda: plugin_cloudflare.read_cloudflare_pages_logs(owner_id, name, dep)
        if tool_name == "cloudflare_pages_retry_deployment":
            name = project(); dep = deployment(); return lambda: plugin_cloudflare.retry_cloudflare_pages_deployment(owner_id, name, dep)
        if tool_name == "cloudflare_pages_rollback_production":
            name = project(); dep = deployment(); return lambda: plugin_cloudflare.rollback_cloudflare_pages_production(owner_id, name, dep)
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "cloudflare":
            allowed = {"plugin_id", "projects", "deployments", "deployment", "logs", "count", "truncated", "action"}
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = executor
    plugin_mcp_gateway._safe_result = safe_result
    _installed = True
