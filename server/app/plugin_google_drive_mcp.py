from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_google_drive, plugin_mcp_gateway


_installed = False


def _tool_definitions() -> dict[str, dict[str, Any]]:
    return {
        "drive_search_files": {
            "plugin_id": "google-drive",
            "runtime_tool": "drive.file.search",
            "risk": "read",
            "description": "Search files in the connected Google Drive account, including shared accessible content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "optional name/full-text search text"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "page_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "drive_read_file": {
            "plugin_id": "google-drive",
            "runtime_tool": "drive.file.read",
            "risk": "read",
            "description": "Read one Google Drive file. Google Docs/Sheets/Slides and UTF-8 text files are projected to bounded plain text; other binary files return metadata only.",
            "inputSchema": {
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
                "required": ["file_id"],
                "additionalProperties": False,
            },
        },
        "drive_create_document": {
            "plugin_id": "google-drive",
            "runtime_tool": "drive.file.write",
            "risk": "write",
            "description": "Create a new Google Docs document in the connected user's My Drive root.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "drive_append_document_text": {
            "plugin_id": "google-drive",
            "runtime_tool": "drive.file.write",
            "risk": "write",
            "description": "Append plain text to the end of the first/default tab of an existing Google Docs document without deleting existing content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["file_id", "text"],
                "additionalProperties": False,
            },
        },
        "drive_rename_file": {
            "plugin_id": "google-drive",
            "runtime_tool": "drive.file.write",
            "risk": "write",
            "description": "Rename one existing Google Drive file without changing its content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["file_id", "name"],
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


def install_google_drive_mcp_tools() -> None:
    """Install Google Drive into the existing per-task Plugin MCP lease/approval/audit path."""
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    @wraps(original_executor)
    def drive_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        if tool_name == "drive_search_files":
            query = plugin_mcp_gateway._arg(arguments, "query", required=False, limit=500)
            page_size = _integer(arguments, "page_size", 50, 100)
            page_token = plugin_mcp_gateway._arg(arguments, "page_token", required=False, limit=2000)
            return lambda: plugin_google_drive.search_google_drive_files(
                owner_id,
                query,
                page_size=page_size,
                page_token=page_token,
            )
        if tool_name == "drive_read_file":
            file_id = plugin_mcp_gateway._arg(arguments, "file_id", limit=240)
            return lambda: plugin_google_drive.read_google_drive_file(owner_id, file_id)
        if tool_name == "drive_create_document":
            title = plugin_mcp_gateway._arg(arguments, "title", limit=1000)
            return lambda: plugin_google_drive.create_google_document(owner_id, title)
        if tool_name == "drive_append_document_text":
            file_id = plugin_mcp_gateway._arg(arguments, "file_id", limit=240)
            text = plugin_mcp_gateway._arg(arguments, "text", limit=30000)
            return lambda: plugin_google_drive.append_google_document_text(owner_id, file_id, text)
        if tool_name == "drive_rename_file":
            file_id = plugin_mcp_gateway._arg(arguments, "file_id", limit=240)
            name = plugin_mcp_gateway._arg(arguments, "name", limit=1000)
            return lambda: plugin_google_drive.rename_google_drive_file(owner_id, file_id, name)
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def drive_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "google-drive":
            allowed = {
                "plugin_id", "query", "files", "count", "next_page_token", "file", "content_type",
                "content", "truncated", "unsupported_reason", "file_id", "name", "mime_type",
                "insert_index", "reply_count", "modified_time", "web_view_link",
            }
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = drive_executor
    plugin_mcp_gateway._safe_result = drive_safe_result
    _installed = True
