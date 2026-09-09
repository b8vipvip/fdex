from __future__ import annotations

import sys
from typing import Any

from app import plugin_runtime
from app.plugin_vercel import (
    VercelPluginError,
    connect_vercel,
    disconnect_vercel,
    list_vercel_projects,
    vercel_connection_status,
)


_installed = False


def _patch_loaded_portal() -> None:
    """Reuse the mature portal's CSRF/grant routes without duplicating an auth surface."""
    portal = sys.modules.get("app.plugin_portal_routes")
    if portal is None or bool(getattr(portal, "_vercel_portal_bridge_installed", False)):
        return

    original_native = portal._native_plugin
    original_code_host = portal._code_host
    original_connect = portal.connect_code_host
    original_disconnect = portal.disconnect_code_host
    original_list = portal.list_code_host_repositories

    def native_plugin(plugin_id: str) -> str:
        clean = str(plugin_id or "").strip().lower()
        return "vercel" if clean == "vercel" else original_native(clean)

    def code_host(plugin_id: str) -> str:
        clean = str(plugin_id or "").strip().lower()
        return "vercel" if clean == "vercel" else original_code_host(clean)

    def connect(owner_id: str, plugin_id: str, access_token: str, *, base_url: str = "") -> dict[str, Any]:
        if str(plugin_id or "").strip().lower() != "vercel":
            return original_connect(owner_id, plugin_id, access_token, base_url=base_url)
        try:
            return connect_vercel(owner_id, access_token, team_id=base_url)
        except VercelPluginError as exc:
            raise portal.CodeHostPluginError(str(exc)) from exc

    def disconnect(owner_id: str, plugin_id: str) -> bool:
        if str(plugin_id or "").strip().lower() != "vercel":
            return original_disconnect(owner_id, plugin_id)
        try:
            return disconnect_vercel(owner_id)
        except VercelPluginError as exc:
            raise portal.CodeHostPluginError(str(exc)) from exc

    def list_projects(owner_id: str, plugin_id: str) -> list[dict[str, Any]]:
        if str(plugin_id or "").strip().lower() != "vercel":
            return original_list(owner_id, plugin_id)
        try:
            payload = list_vercel_projects(owner_id, limit=100)
            rows = payload.get("projects") if isinstance(payload.get("projects"), list) else []
            return [item for item in rows if isinstance(item, dict)]
        except VercelPluginError as exc:
            raise portal.CodeHostPluginError(str(exc)) from exc

    portal._native_plugin = native_plugin
    portal._code_host = code_host
    portal.connect_code_host = connect
    portal.disconnect_code_host = disconnect
    portal.list_code_host_repositories = list_projects
    # plugin_portal_routes imported this function by value before Phase 7.51 installs; refresh that
    # local binding so grant save and MCP verification see Vercel's live connection state.
    portal.plugin_connection_status = plugin_runtime.plugin_connection_status
    portal._vercel_portal_bridge_installed = True


def install_vercel_runtime() -> None:
    """Promote Vercel from roadmap metadata to a native owner-scoped encrypted adapter."""
    global _installed
    if _installed:
        _patch_loaded_portal()
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
    _patch_loaded_portal()
