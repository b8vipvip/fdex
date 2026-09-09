from __future__ import annotations

from typing import Any

from app import plugin_runtime
from app.plugin_cloudflare import cloudflare_connection_status


_installed = False


def install_cloudflare_runtime() -> None:
    global _installed
    if _installed:
        return
    native = plugin_runtime.PluginDefinition(
        "cloudflare",
        "Cloudflare",
        "部署与运维",
        "Cloudflare Pages 项目、部署、构建日志、重试与生产回滚。API Token 仅加密保存在当前 FDEX 账号。",
        "/account/plugins#plugin-cloudflare",
        "native",
        (
            plugin_runtime.PluginTool("cloudflare.pages.project.read", "read", "读取 Pages 项目"),
            plugin_runtime.PluginTool("cloudflare.pages.deployment.read", "read", "读取 Pages 部署与构建日志"),
            plugin_runtime.PluginTool("cloudflare.pages.deployment.retry", "write", "重试 Pages 部署"),
            plugin_runtime.PluginTool("cloudflare.pages.production.rollback", "dangerous", "回滚 Pages 生产部署"),
        ),
    )
    plugin_runtime.CATALOG = tuple(native if item.id == "cloudflare" else item for item in plugin_runtime.CATALOG)
    plugin_runtime.CATALOG_BY_ID = {item.id: item for item in plugin_runtime.CATALOG}
    original_status = plugin_runtime.plugin_connection_status

    def connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
        definition = plugin_runtime.plugin_definition(plugin_id)
        if definition.id == "cloudflare":
            return cloudflare_connection_status(owner_id)
        return original_status(owner_id, definition.id)

    plugin_runtime.plugin_connection_status = connection_status
    try:
        from app import plugin_mcp_gateway
        plugin_mcp_gateway.plugin_connection_status = plugin_runtime.plugin_connection_status
    except (ImportError, AttributeError):
        pass
    _installed = True
