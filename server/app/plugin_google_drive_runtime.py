from __future__ import annotations

from typing import Any

from app import plugin_runtime
from app.plugin_google_drive import google_drive_connection_status


_installed = False


def install_google_drive_runtime() -> None:
    """Promote Google Drive from roadmap metadata to a native owner-scoped OAuth adapter."""
    global _installed
    if _installed:
        return

    native = plugin_runtime.PluginDefinition(
        "google-drive",
        "Google Drive",
        "沟通与知识",
        "Google Drive、Docs、Sheets 与 Slides 知识源。使用当前 FDEX 账号独立 OAuth 授权，访问/刷新令牌加密保存。",
        "/account/plugins#plugin-google-drive",
        "native",
        (
            plugin_runtime.PluginTool("drive.file.search", "read", "搜索当前 Google Drive 可访问文件"),
            plugin_runtime.PluginTool("drive.file.read", "read", "读取 Google Docs/Sheets/Slides 与文本文件"),
            plugin_runtime.PluginTool("drive.file.write", "write", "创建 Google Docs、追加正文或重命名文件"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "google-drive" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}

    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "google-drive":
            return google_drive_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status

    # Plugin MCP imports plugin_connection_status by value. Rebind it when import order caused
    # the gateway to load before this native adapter was promoted.
    try:
        from app import plugin_mcp_gateway
        plugin_mcp_gateway.plugin_connection_status = plugin_runtime.plugin_connection_status
    except (ImportError, AttributeError):
        pass
    _installed = True
