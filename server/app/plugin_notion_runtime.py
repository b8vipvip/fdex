from __future__ import annotations

from typing import Any

from app import plugin_runtime
from app.plugin_notion import notion_connection_status


_installed = False


def install_notion_runtime() -> None:
    """Promote the catalog's Notion roadmap entry to a native, owner-scoped adapter."""
    global _installed
    if _installed:
        return

    native = plugin_runtime.PluginDefinition(
        "notion",
        "Notion",
        "沟通与知识",
        "Notion 页面、Data Source 与知识内容。使用当前 FDEX 账号独立的 Integration Token，凭据加密保存。",
        "/account/plugins#plugin-notion",
        "native",
        (
            plugin_runtime.PluginTool("notion.page.search", "read", "搜索已共享给 Integration 的页面与 Data Source"),
            plugin_runtime.PluginTool("notion.page.read", "read", "读取页面元数据与递归块内容"),
            plugin_runtime.PluginTool("notion.data_source.read", "read", "查询 Data Source 中的页面"),
            plugin_runtime.PluginTool("notion.page.write", "write", "创建页面、追加正文或更新页面标题"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "notion" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}

    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "notion":
            return notion_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status
    _installed = True
