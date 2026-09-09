from __future__ import annotations

from pathlib import Path

from app import plugin_portal_routes


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_plugin_center_exposes_per_agent_mcp_verification() -> None:
    routes = _read("server/app/plugin_portal_routes.py")
    page = _read("server/app/templates/user_plugins.html")

    assert '@router.post("/{plugin_id}/agents/{employee_id}/verify"' in routes
    assert "_tool_catalog(owner_id, employee)" in routes
    assert '_tool_call({"owner_id": owner_id, "employee": employee}, tool_name, {})' in routes
    assert "MCP 验证通过" in routes
    assert "/agents/{{ employee.id }}/verify" in page
    assert "验证 MCP" in page


def test_verification_is_read_only_and_limited_to_native_plugins() -> None:
    routes = _read("server/app/plugin_portal_routes.py")

    assert 'tool_name = "feishu_list_chats" if clean_plugin == "feishu" else f"{clean_plugin}_list_repositories"' in routes
    assert "clean_plugin = _native_plugin(clean_plugin)" in routes
    assert "write_code_host_file" not in routes
    assert "create_code_host_pull_request" not in routes
    assert "send_feishu_text" not in routes
    assert "create_feishu_document" not in routes
    assert "append_feishu_document_text" not in routes


def test_mcp_error_text_never_requires_provider_specific_shape() -> None:
    assert plugin_portal_routes._mcp_error_text({"content": [{"type": "text", "text": "denied"}]}) == "denied"
    assert plugin_portal_routes._mcp_error_text({"content": []}) == "Plugin MCP 返回未知错误"
    assert plugin_portal_routes._mcp_error_text({}) == "Plugin MCP 返回未知错误"