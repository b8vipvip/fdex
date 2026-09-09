from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_jira, plugin_mcp_gateway


_installed = False


def _tool_definitions() -> dict[str, dict[str, Any]]:
    site = {"cloud_id": {"type": "string", "description": "Jira Cloud site id. Required when the OAuth grant has multiple sites."}}
    return {
        "jira_list_sites": {
            "plugin_id": "jira",
            "runtime_tool": "jira.workspace.read",
            "risk": "read",
            "description": "Refresh and list Jira Cloud sites currently accessible to the connected Atlassian OAuth grant.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "jira_list_projects": {
            "plugin_id": "jira",
            "runtime_tool": "jira.workspace.read",
            "risk": "read",
            "description": "List projects visible in one Jira Cloud site. If multiple sites are connected, pass cloud_id explicitly.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **site,
                    "query": {"type": "string"},
                    "start_at": {"type": "integer", "minimum": 0, "maximum": 1000000},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        },
        "jira_search_issues": {
            "plugin_id": "jira",
            "runtime_tool": "jira.issue.read",
            "risk": "read",
            "description": "Search one Jira Cloud site using JQL enhanced search. Defaults to recently updated issues.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **site,
                    "jql": {"type": "string", "description": "JQL, defaults to ORDER BY updated DESC"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                    "next_page_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "jira_read_issue": {
            "plugin_id": "jira",
            "runtime_tool": "jira.issue.read",
            "risk": "read",
            "description": "Read one Jira Issue plus a bounded first page of comments.",
            "inputSchema": {
                "type": "object",
                "properties": {**site, "issue_id_or_key": {"type": "string"}},
                "required": ["issue_id_or_key"],
                "additionalProperties": False,
            },
        },
        "jira_list_transitions": {
            "plugin_id": "jira",
            "runtime_tool": "jira.workspace.read",
            "risk": "read",
            "description": "List currently available workflow transitions for one Jira Issue. Use this before changing status.",
            "inputSchema": {
                "type": "object",
                "properties": {**site, "issue_id_or_key": {"type": "string"}},
                "required": ["issue_id_or_key"],
                "additionalProperties": False,
            },
        },
        "jira_create_issue": {
            "plugin_id": "jira",
            "runtime_tool": "jira.issue.write",
            "risk": "write",
            "description": "Create one Jira Issue in an explicitly selected project. Jira v3 description is converted to ADF by FDEX.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **site,
                    "summary": {"type": "string"},
                    "project_key": {"type": "string"},
                    "project_id": {"type": "string"},
                    "issue_type_name": {"type": "string"},
                    "issue_type_id": {"type": "string"},
                    "description": {"type": "string"},
                    "assignee_account_id": {"type": "string"},
                    "priority_name": {"type": "string"},
                    "priority_id": {"type": "string"},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
        "jira_update_issue": {
            "plugin_id": "jira",
            "runtime_tool": "jira.issue.write",
            "risk": "write",
            "description": "Update selected editable fields on one Jira Issue. Workflow transition is intentionally a separate tool.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **site,
                    "issue_id_or_key": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "assignee_account_id": {"type": "string"},
                    "priority_name": {"type": "string"},
                    "priority_id": {"type": "string"},
                },
                "required": ["issue_id_or_key"],
                "additionalProperties": False,
            },
        },
        "jira_transition_issue": {
            "plugin_id": "jira",
            "runtime_tool": "jira.issue.write",
            "risk": "write",
            "description": "Transition one Jira Issue using an explicit transition id obtained from jira_list_transitions.",
            "inputSchema": {
                "type": "object",
                "properties": {**site, "issue_id_or_key": {"type": "string"}, "transition_id": {"type": "string"}},
                "required": ["issue_id_or_key", "transition_id"],
                "additionalProperties": False,
            },
        },
        "jira_add_comment": {
            "plugin_id": "jira",
            "runtime_tool": "jira.comment.write",
            "risk": "write",
            "description": "Append a plain-text comment to one Jira Issue. FDEX converts it to Atlassian Document Format.",
            "inputSchema": {
                "type": "object",
                "properties": {**site, "issue_id_or_key": {"type": "string"}, "body": {"type": "string"}},
                "required": ["issue_id_or_key", "body"],
                "additionalProperties": False,
            },
        },
    }


def _integer(arguments: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = arguments.get(name, default)
    if isinstance(raw, bool):
        raise ValueError(f"参数 {name} 必须是整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {name} 必须是整数") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"参数 {name} 超出允许范围")
    return value


def _arg(arguments: dict[str, Any], name: str, *, required: bool = False, limit: int = 1000) -> str:
    return plugin_mcp_gateway._arg(arguments, name, required=required, limit=limit)


def install_jira_mcp_tools() -> None:
    """Install Jira into the existing task lease, dynamic grant, approval and audit path."""
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    @wraps(original_executor)
    def jira_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        cloud_id = _arg(arguments, "cloud_id", required=False, limit=200)
        if tool_name == "jira_list_sites":
            return lambda: plugin_jira.list_jira_sites(owner_id, refresh=True)
        if tool_name == "jira_list_projects":
            query = _arg(arguments, "query", required=False, limit=500)
            start_at = _integer(arguments, "start_at", 0, 0, 1000000)
            max_results = _integer(arguments, "max_results", 50, 1, 100)
            return lambda: plugin_jira.list_jira_projects(
                owner_id, cloud_id=cloud_id, query=query, start_at=start_at, max_results=max_results
            )
        if tool_name == "jira_search_issues":
            jql = _arg(arguments, "jql", required=False, limit=5000) or "ORDER BY updated DESC"
            max_results = _integer(arguments, "max_results", 50, 1, 100)
            token = _arg(arguments, "next_page_token", required=False, limit=1000)
            return lambda: plugin_jira.search_jira_issues(
                owner_id, jql, cloud_id=cloud_id, max_results=max_results, next_page_token=token
            )
        if tool_name == "jira_read_issue":
            issue_ref = _arg(arguments, "issue_id_or_key", required=True, limit=120)
            return lambda: plugin_jira.read_jira_issue(owner_id, issue_ref, cloud_id=cloud_id)
        if tool_name == "jira_list_transitions":
            issue_ref = _arg(arguments, "issue_id_or_key", required=True, limit=120)
            return lambda: plugin_jira.list_jira_transitions(owner_id, issue_ref, cloud_id=cloud_id)
        if tool_name == "jira_create_issue":
            summary = _arg(arguments, "summary", required=True, limit=2000)
            project_key = _arg(arguments, "project_key", required=False, limit=120)
            project_id = _arg(arguments, "project_id", required=False, limit=120)
            issue_type_name = _arg(arguments, "issue_type_name", required=False, limit=240)
            issue_type_id = _arg(arguments, "issue_type_id", required=False, limit=120)
            description = _arg(arguments, "description", required=False, limit=30000)
            assignee = _arg(arguments, "assignee_account_id", required=False, limit=240)
            priority_name = _arg(arguments, "priority_name", required=False, limit=240)
            priority_id = _arg(arguments, "priority_id", required=False, limit=120)
            return lambda: plugin_jira.create_jira_issue(
                owner_id,
                summary,
                cloud_id=cloud_id,
                project_key=project_key,
                project_id=project_id,
                issue_type_name=issue_type_name,
                issue_type_id=issue_type_id,
                description=description,
                assignee_account_id=assignee,
                priority_name=priority_name,
                priority_id=priority_id,
            )
        if tool_name == "jira_update_issue":
            issue_ref = _arg(arguments, "issue_id_or_key", required=True, limit=120)
            summary = _arg(arguments, "summary", required=False, limit=2000)
            description = None if "description" not in arguments else _arg(arguments, "description", required=False, limit=30000)
            assignee = _arg(arguments, "assignee_account_id", required=False, limit=240)
            priority_name = _arg(arguments, "priority_name", required=False, limit=240)
            priority_id = _arg(arguments, "priority_id", required=False, limit=120)
            return lambda: plugin_jira.update_jira_issue(
                owner_id,
                issue_ref,
                cloud_id=cloud_id,
                summary=summary,
                description=description,
                assignee_account_id=assignee,
                priority_name=priority_name,
                priority_id=priority_id,
            )
        if tool_name == "jira_transition_issue":
            issue_ref = _arg(arguments, "issue_id_or_key", required=True, limit=120)
            transition_id = _arg(arguments, "transition_id", required=True, limit=120)
            return lambda: plugin_jira.transition_jira_issue(owner_id, issue_ref, transition_id, cloud_id=cloud_id)
        if tool_name == "jira_add_comment":
            issue_ref = _arg(arguments, "issue_id_or_key", required=True, limit=120)
            body = _arg(arguments, "body", required=True, limit=30000)
            return lambda: plugin_jira.add_jira_comment(owner_id, issue_ref, body, cloud_id=cloud_id)
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def jira_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "jira":
            allowed = {
                "plugin_id", "cloud_id", "sites", "projects", "issues", "issue", "comments", "comments_truncated",
                "transitions", "count", "start_at", "max_results", "total", "is_last", "next_page_token", "jql",
                "issue_id_or_key", "transition_id", "action", "comment", "updated_fields",
            }
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = jira_executor
    plugin_mcp_gateway._safe_result = jira_safe_result
    _installed = True
