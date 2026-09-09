from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app import plugin_feishu, plugin_mcp_gateway


_installed = False


def _tool_definitions() -> dict[str, dict[str, Any]]:
    return {
        "feishu_list_chats": {
            "plugin_id": "feishu",
            "runtime_tool": "feishu.chats.list",
            "risk": "read",
            "description": "List chats visible to the connected Feishu self-built app/bot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                    "page_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "feishu_list_messages": {
            "plugin_id": "feishu",
            "runtime_tool": "feishu.message.read",
            "risk": "read",
            "description": "Read a bounded newest-first page of messages from one Feishu chat the bot can access.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
                    "page_token": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        },
        "feishu_read_document": {
            "plugin_id": "feishu",
            "runtime_tool": "feishu.document.read",
            "risk": "read",
            "description": "Read the plain-text content of one Feishu Docx document accessible to the app.",
            "inputSchema": {
                "type": "object",
                "properties": {"document_id": {"type": "string"}},
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
        "feishu_send_text_message": {
            "plugin_id": "feishu",
            "runtime_tool": "feishu.message.send",
            "risk": "write",
            "description": "Send one Feishu text message. This is a write and remains subject to Codex/FDEX approval.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "receive_id": {"type": "string"},
                    "receive_id_type": {
                        "type": "string",
                        "enum": ["chat_id", "open_id", "user_id", "union_id", "email"],
                    },
                    "text": {"type": "string"},
                },
                "required": ["receive_id", "text"],
                "additionalProperties": False,
            },
        },
        "feishu_create_document": {
            "plugin_id": "feishu",
            "runtime_tool": "feishu.document.write",
            "risk": "write",
            "description": "Create an empty Feishu Docx document in the app's accessible space.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "folder_token": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "feishu_append_document_text": {
            "plugin_id": "feishu",
            "runtime_tool": "feishu.document.write",
            "risk": "write",
            "description": "Append plain text as paragraph blocks to an existing Feishu Docx document without deleting existing content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "parent_block_id": {"type": "string", "description": "optional parent block; defaults to document root"},
                    "text": {"type": "string"},
                },
                "required": ["document_id", "text"],
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


def install_feishu_mcp_tools() -> None:
    """Install Feishu into the same task lease, grant, approval and audit path as native plugins."""
    global _installed
    if _installed:
        return
    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())
    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    @wraps(original_executor)
    def feishu_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        if tool_name == "feishu_list_chats":
            page_size = _integer(arguments, "page_size", 50, 100)
            page_token = plugin_mcp_gateway._arg(arguments, "page_token", required=False, limit=2000)
            return lambda: plugin_feishu.list_feishu_chats(owner_id, page_size=page_size, page_token=page_token)
        if tool_name == "feishu_list_messages":
            chat_id = plugin_mcp_gateway._arg(arguments, "chat_id", limit=240)
            page_size = _integer(arguments, "page_size", 20, 50)
            page_token = plugin_mcp_gateway._arg(arguments, "page_token", required=False, limit=2000)
            return lambda: plugin_feishu.list_feishu_messages(
                owner_id, chat_id, page_size=page_size, page_token=page_token
            )
        if tool_name == "feishu_read_document":
            document_id = plugin_mcp_gateway._arg(arguments, "document_id", limit=240)
            return lambda: plugin_feishu.read_feishu_document(owner_id, document_id)
        if tool_name == "feishu_send_text_message":
            receive_id = plugin_mcp_gateway._arg(arguments, "receive_id", limit=320)
            receive_id_type = plugin_mcp_gateway._arg(
                arguments, "receive_id_type", required=False, limit=40
            ) or "chat_id"
            text = plugin_mcp_gateway._arg(arguments, "text", limit=30000)
            return lambda: plugin_feishu.send_feishu_text(
                owner_id, receive_id, text, receive_id_type=receive_id_type
            )
        if tool_name == "feishu_create_document":
            title = plugin_mcp_gateway._arg(arguments, "title", limit=500)
            folder_token = plugin_mcp_gateway._arg(arguments, "folder_token", required=False, limit=240)
            return lambda: plugin_feishu.create_feishu_document(owner_id, title, folder_token=folder_token)
        if tool_name == "feishu_append_document_text":
            document_id = plugin_mcp_gateway._arg(arguments, "document_id", limit=240)
            parent_block_id = plugin_mcp_gateway._arg(
                arguments, "parent_block_id", required=False, limit=240
            )
            text = plugin_mcp_gateway._arg(arguments, "text", limit=30000)
            return lambda: plugin_feishu.append_feishu_document_text(
                owner_id, document_id, text, parent_block_id=parent_block_id
            )
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def feishu_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and value.get("plugin_id") == "feishu":
            allowed = {
                "plugin_id", "items", "count", "has_more", "page_token", "chat_id",
                "document_id", "content", "truncated", "message_id", "create_time", "msg_type",
                "revision_id", "title", "parent_block_id", "created_blocks",
            }
            return {key: value.get(key) for key in allowed if key in value}
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = feishu_executor
    plugin_mcp_gateway._safe_result = feishu_safe_result
    _installed = True
