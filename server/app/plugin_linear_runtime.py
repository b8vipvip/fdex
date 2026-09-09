from __future__ import annotations

from typing import Any

from app import plugin_runtime
from app.plugin_linear import linear_connection_status


_installed = False


def install_linear_runtime() -> None:
    """Promote Linear from roadmap metadata to a native owner-scoped OAuth adapter."""
    global _installed
    if _installed:
        return

    native = plugin_runtime.PluginDefinition(
        "linear",
        "Linear",
        "项目管理",
        "Linear Issue、团队、工作流状态与研发任务回写。使用当前 FDEX 账号独立 OAuth 授权，令牌加密保存。",
        "/account/plugins#plugin-linear",
        "native",
        (
            plugin_runtime.PluginTool("linear.workspace.read", "read", "读取团队与工作流状态"),
            plugin_runtime.PluginTool("linear.issue.read", "read", "搜索和读取 Issue 及评论"),
            plugin_runtime.PluginTool("linear.issue.write", "write", "创建或更新 Issue"),
            plugin_runtime.PluginTool("linear.comment.write", "write", "向 Issue 添加评论"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "linear" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}

    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "linear":
            return linear_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status
    try:
        from app import plugin_mcp_gateway
        plugin_mcp_gateway.plugin_connection_status = plugin_runtime.plugin_connection_status
    except (ImportError, AttributeError):
        pass
    _installed = True
