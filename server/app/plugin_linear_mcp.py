from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_linear, plugin_mcp_gateway


_installed = False


def _tool_definitions() -> dict[str, dict[str, Any]]:
    return {
        "linear_list_teams": {
            "plugin_id": "linear",
            "runtime_tool": "linear.workspace.read",
            "risk": "read",
            "description": "List teams in the connected Linear workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "linear_list_workflow_states": {
            "plugin_id": "linear",
            "runtime_tool": "linear.workspace.read",
            "risk": "read",
            "description": "List workflow states available in the connected Linear workspace so an agent can safely choose a state id before updating an issue.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "linear_search_issues": {
            "plugin_id": "linear",
            "runtime_tool": "linear.issue.read",
            "risk": "read",
            "description": "Search recent Linear issues by title/description with bounded cursor pagination.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "linear_read_issue": {
            "plugin_id": "linear",
            "runtime_tool": "linear.issue.read",
            "risk": "read",
            "description": "Read one Linear issue by UUID or human identifier, including a bounded first page of comments.",
            "inputSchema": {
                "type": "object",
                "properties": {"issue_id": {"type": "string"}},
                "required": ["issue_id"],
                "additionalProperties": False,
            },
        },
        "linear_create_issue": {
            "plugin_id": "linear",
            "runtime_tool": "linear.issue.write",
            "risk": "write",
            "description": "Create one Linear issue in an explicitly selected team.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "team_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "state_id": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "project_id": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 0, "maximum": 4},
                },
                "required": ["team_id", "title"],
                "additionalProperties": False,
            },
        },
        "linear_update_issue": {
            "plugin_id": "linear",
            "runtime_tool": "linear.issue.write",
            "risk": "write",
            "description": "Update selected fields on one Linear issue. Read the issue and list workflow states first when changing status.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "state_id": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "project_id": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 0, "maximum": 4},
                },
                "required": ["issue_id"],
                "additionalProperties": False,
            },
        },
        "linear_add_comment": {
            "plugin_id": "linear",
            "runtime_tool": "linear.comment.write",
            "risk": "write",
            "description": "Append a Markdown comment to one Linear issue, suitable for posting Coding Agent progress or completion notes.",
            "inputSchema": {
                "type": "object",
                "properties": {"issue_id": {"type": "string"}, "body": {"type": "string"}},
                "required": ["issue_id", "body"],
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


def _optional_priority(arguments: dict[str, Any]) -> int | None:
    if "priority" not in arguments or arguments.get("priority") is None:
        return None
    raw = arguments.get("priority")
    if isinstance(raw, bool):
        raise ValueError("参数 priority 必须是 0-4 的整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("参数 priority 必须是 0-4 的整数") from exc
    if value < 0 or value > 4:
        raise ValueError("参数 priority 必须是 0-4 的整数")
    return value


def install_linear_mcp_tools() -> None:
    """Install Linear into the existing per-task Plugin MCP lease/approval/audit path."""
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    @wraps(original_executor)
    def linear_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        if tool_name == "linear_list_teams":
            page_size = _integer(arguments, "page_size", 50, 100)
            after = plugin_mcp_gateway._arg(arguments, "after", required=False, limit=500)
            return lambda: plugin_linear.list_linear_teams(owner_id, page_size=page_size, after=after)
        if tool_name == "linear_list_workflow_states":
            page_size = _integer(arguments, "page_size", 100, 100)
            after = plugin_mcp_gateway._arg(arguments, "after", required=False, limit=500)
            return lambda: plugin_linear.list_linear_workflow_states(owner_id, page_size=page_size, after=after)
        if tool_name == "linear_search_issues":
            query = plugin_mcp_gateway._arg(arguments, "query", required=False, limit=500)
            page_size = _integer(arguments, "page_size", 50, 100)
            after = plugin_mcp_gateway._arg(arguments, "after", required=False, limit=500)
            return lambda: plugin_linear.search_linear_issues(owner_id, query, page_size=page_size, after=after)
        if tool_name == "linear_read_issue":
            issue_id = plugin_mcp_gateway._arg(arguments, "issue_id", limit=160)
            return lambda: plugin_linear.read_linear_issue(owner_id, issue_id)
        if tool_name == "linear_create_issue":
            team_id = plugin_mcp_gateway._arg(arguments, "team_id", limit=160)
            title = plugin_mcp_gateway._arg(arguments, "title", limit=2000)
            description = plugin_mcp_gateway._arg(arguments, "description", required=False, limit=30000)
            state_id = plugin_mcp_gateway._arg(arguments, "state_id", required=False, limit=160)
            assignee_id = plugin_mcp_gateway._arg(arguments, "assignee_id", required=False, limit=160)
            project_id = plugin_mcp_gateway._arg(arguments, "project_id", required=False, limit=160)
            priority = _optional_priority(arguments)
            return lambda: plugin_linear.create_linear_issue(
                owner_id, team_id, title, description=description, state_id=state_id,
                assignee_id=assignee_id, project_id=project_id, priority=priority,
            )
        if tool_name == "linear_update_issue":
            issue_id = plugin_mcp_gateway._arg(arguments, "issue_id", limit=160)
            title = plugin_mcp_gateway._arg(arguments, "title", required=False, limit=2000)
            description = None if "description" not in arguments else plugin_mcp_gateway._arg(arguments, "description", required=False, limit=30000)
            state_id = plugin_mcp_gateway._arg(arguments, "state_id", required=False, limit=160)
            assignee_id = plugin_mcp_gateway._arg(arguments, "assignee_id", required=False, limit=160)
            project_id = plugin_mcp_gateway._arg(arguments, "project_id", required=False, limit=160)
            priority = _optional_priority(arguments)
            return lambda: plugin_linear.update_linear_issue(
                owner_id, issue_id, title=title, description=description, state_id=state_id,
                assignee_id=assignee_id, project_id=project_id, priority=priority,
            )
        if tool_name == "linear_add_comment":
            issue_id = plugin_mcp_gateway._arg(arguments, "issue_id", limit=160)
            body = plugin_mcp_gateway._arg(arguments, "body", limit=30000)
            return lambda: plugin_linear.create_linear_comment(owner_id, issue_id, body)
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def linear_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "linear":
            allowed = {
                "plugin_id", "teams", "states", "issues", "issue", "comments", "comments_truncated",
                "count", "has_more", "next_cursor", "query", "action", "comment",
            }
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = linear_executor
    plugin_mcp_gateway._safe_result = linear_safe_result
    _installed = True
