from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_mcp_gateway, plugin_notion


_installed = False


def _tool_definitions() -> dict[str, dict[str, Any]]:
    return {
        "notion_search": {
            "plugin_id": "notion",
            "runtime_tool": "notion.page.search",
            "risk": "read",
            "description": "Search pages and data sources shared with the connected Notion integration.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "optional title search text"},
                    "object_filter": {"type": "string", "enum": ["page", "data_source"]},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "start_cursor": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "notion_read_page": {
            "plugin_id": "notion",
            "runtime_tool": "notion.page.read",
            "risk": "read",
            "description": "Read a Notion page and recursively project a bounded plain-text representation of its blocks.",
            "inputSchema": {
                "type": "object",
                "properties": {"page_id": {"type": "string"}},
                "required": ["page_id"],
                "additionalProperties": False,
            },
        },
        "notion_query_data_source": {
            "plugin_id": "notion",
            "runtime_tool": "notion.data_source.read",
            "risk": "read",
            "description": "Query a bounded page of entries from one Notion data source.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_source_id": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "start_cursor": {"type": "string"},
                },
                "required": ["data_source_id"],
                "additionalProperties": False,
            },
        },
        "notion_create_page": {
            "plugin_id": "notion",
            "runtime_tool": "notion.page.write",
            "risk": "write",
            "description": "Create one child page under an existing Notion page, optionally with plain-text body content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_page_id": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["parent_page_id", "title"],
                "additionalProperties": False,
            },
        },
        "notion_append_page_text": {
            "plugin_id": "notion",
            "runtime_tool": "notion.page.write",
            "risk": "write",
            "description": "Append plain-text paragraph blocks to the end of an existing Notion page without deleting existing content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["page_id", "text"],
                "additionalProperties": False,
            },
        },
        "notion_update_page_title": {
            "plugin_id": "notion",
            "runtime_tool": "notion.page.write",
            "risk": "write",
            "description": "Update the title property of one existing Notion page after resolving its actual title property key.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["page_id", "title"],
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
        raise ValueError(f"参数 {name} 必须在 1-{maximum} 之间")
    return value


def install_notion_mcp_tools() -> None:
    """Install Notion into the same task lease, grant, approval and audit path as native plugins."""
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    @wraps(original_executor)
    def notion_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        if tool_name == "notion_search":
            query = plugin_mcp_gateway._arg(arguments, "query", required=False, limit=500)
            object_filter = plugin_mcp_gateway._arg(arguments, "object_filter", required=False, limit=40)
            page_size = _integer(arguments, "page_size", 50, 100)
            start_cursor = plugin_mcp_gateway._arg(arguments, "start_cursor", required=False, limit=200)
            return lambda: plugin_notion.search_notion(
                owner_id,
                query,
                object_filter=object_filter,
                page_size=page_size,
                start_cursor=start_cursor,
            )
        if tool_name == "notion_read_page":
            page_id = plugin_mcp_gateway._arg(arguments, "page_id", limit=100)
            return lambda: plugin_notion.read_notion_page(owner_id, page_id)
        if tool_name == "notion_query_data_source":
            data_source_id = plugin_mcp_gateway._arg(arguments, "data_source_id", limit=100)
            page_size = _integer(arguments, "page_size", 50, 100)
            start_cursor = plugin_mcp_gateway._arg(arguments, "start_cursor", required=False, limit=200)
            return lambda: plugin_notion.query_notion_data_source(
                owner_id,
                data_source_id,
                page_size=page_size,
                start_cursor=start_cursor,
            )
        if tool_name == "notion_create_page":
            parent_page_id = plugin_mcp_gateway._arg(arguments, "parent_page_id", limit=100)
            title = plugin_mcp_gateway._arg(arguments, "title", limit=1000)
            text = plugin_mcp_gateway._arg(arguments, "text", required=False, limit=30000)
            return lambda: plugin_notion.create_notion_page(
                owner_id,
                parent_page_id,
                title,
                text=text,
            )
        if tool_name == "notion_append_page_text":
            page_id = plugin_mcp_gateway._arg(arguments, "page_id", limit=100)
            text = plugin_mcp_gateway._arg(arguments, "text", limit=30000)
            return lambda: plugin_notion.append_notion_page_text(owner_id, page_id, text)
        if tool_name == "notion_update_page_title":
            page_id = plugin_mcp_gateway._arg(arguments, "page_id", limit=100)
            title = plugin_mcp_gateway._arg(arguments, "title", limit=1000)
            return lambda: plugin_notion.update_notion_page_title(owner_id, page_id, title)
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def notion_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "notion":
            allowed = {
                "plugin_id", "query", "object_filter", "results", "count", "has_more", "next_cursor",
                "data_source_id", "object", "id", "page_id", "parent_page_id", "title", "url",
                "created_time", "last_edited_time", "in_trash", "parent_type", "parent_id", "content",
                "block_count", "truncated", "created_blocks",
            }
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = notion_executor
    plugin_mcp_gateway._safe_result = notion_safe_result
    _installed = True
