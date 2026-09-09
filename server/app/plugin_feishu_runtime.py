from __future__ import annotations

import sys
from typing import Any

from app import plugin_runtime
from app.plugin_feishu import feishu_connection_status


_installed = False


def install_feishu_runtime() -> None:
    """Promote the catalog's Feishu roadmap entry to a native, owner-scoped adapter."""
    global _installed
    if _installed:
        return

    native = plugin_runtime.PluginDefinition(
        "feishu",
        "飞书",
        "沟通与知识",
        "飞书群消息与新版云文档。使用当前 FDEX 账号独立配置的企业自建应用凭据，凭据加密保存。",
        "/account/plugins#plugin-feishu",
        "native",
        (
            plugin_runtime.PluginTool("feishu.chats.list", "read", "列出机器人可访问的群聊"),
            plugin_runtime.PluginTool("feishu.message.read", "read", "读取授权范围内群聊消息"),
            plugin_runtime.PluginTool("feishu.document.read", "read", "读取新版云文档纯文本内容"),
            plugin_runtime.PluginTool("feishu.message.send", "write", "发送文本消息"),
            plugin_runtime.PluginTool("feishu.document.write", "write", "创建文档或追加文本块"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "feishu" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}

    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "feishu":
            return feishu_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status
    # main.py can import Plugin MCP before the portal installs native adapters. Rebind the gateway's
    # compatibility import if it is already loaded so live tools/list never keeps a stale roadmap
    # connection-status function.
    gateway = sys.modules.get("app.plugin_mcp_gateway")
    if gateway is not None:
        setattr(gateway, "plugin_connection_status", connection_status)
    _installed = True
