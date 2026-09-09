from __future__ import annotations

from typing import Any

from app import plugin_runtime
from app.plugin_vercel import vercel_connection_status


_installed = False


def install_vercel_runtime() -> None:
    """Promote Vercel from roadmap metadata to a native owner-scoped encrypted adapter."""
    global _installed
    if _installed:
        return

    native = plugin_runtime.PluginDefinition(
        "vercel",
        "Vercel",
        "部署与运维",
        "项目、部署状态、Preview 重新部署与生产流量切换。Access Token 仅加密保存在当前 FDEX 账号。",
        "/account/plugins#plugin-vercel",
        "native",
        (
            plugin_runtime.PluginTool("vercel.project.read", "read", "读取项目列表"),
            plugin_runtime.PluginTool("vercel.deployment.read", "read", "读取部署状态与安全元数据"),
            plugin_runtime.PluginTool("vercel.deployment.create", "write", "从已有部署创建新的 Preview 部署"),
            plugin_runtime.PluginTool("vercel.production.promote", "dangerous", "把 READY 部署提升为当前生产部署"),
            plugin_runtime.PluginTool("vercel.production.rollback", "dangerous", "把生产流量回滚到指定历史生产部署"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "vercel" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}

    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "vercel":
            return vercel_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status
    try:
        from app import plugin_mcp_gateway
        plugin_mcp_gateway.plugin_connection_status = plugin_runtime.plugin_connection_status
    except (ImportError, AttributeError):
        pass
    _installed = True
