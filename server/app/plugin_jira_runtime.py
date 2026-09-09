from __future__ import annotations

from typing import Any

from app import plugin_runtime
from app.plugin_jira import jira_connection_status


_installed = False


def install_jira_runtime() -> None:
    """Promote Jira from roadmap metadata to a native owner-scoped OAuth adapter."""
    global _installed
    if _installed:
        return

    native = plugin_runtime.PluginDefinition(
        "jira",
        "Jira",
        "项目管理",
        "Jira Cloud 项目、Issue、工作流状态与评论。使用当前 FDEX 账号独立 Atlassian OAuth 2.0 (3LO) 授权。",
        "/account/plugins#plugin-jira",
        "native",
        (
            plugin_runtime.PluginTool("jira.workspace.read", "read", "读取 OAuth 可访问站点、项目与工作流状态"),
            plugin_runtime.PluginTool("jira.issue.read", "read", "使用 JQL 搜索并读取 Issue 与评论"),
            plugin_runtime.PluginTool("jira.issue.write", "write", "创建、编辑或流转 Issue"),
            plugin_runtime.PluginTool("jira.comment.write", "write", "向 Issue 添加评论"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "jira" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}

    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "jira":
            return jira_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status
    try:
        from app import plugin_mcp_gateway
        plugin_mcp_gateway.plugin_connection_status = plugin_runtime.plugin_connection_status
    except (ImportError, AttributeError):
        pass
    _installed = True
