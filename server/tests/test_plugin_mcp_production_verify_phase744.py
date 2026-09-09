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

    assert 'if clean_plugin == "feishu":' in routes
    assert 'tool_name = "feishu_list_chats"' in routes
    assert 'elif clean_plugin == "notion":' in routes
    assert 'tool_name = "notion_search"' in routes
    assert 'elif clean_plugin == "google-drive":' in routes
    assert 'tool_name = "drive_search_files"' in routes
    assert 'elif clean_plugin == "linear":' in routes
    assert 'tool_name = "linear_search_issues"' in routes
    assert 'tool_name = f"{clean_plugin}_list_repositories"' in routes
    assert "clean_plugin = _native_plugin(clean_plugin)" in routes
    assert "write_code_host_file" not in routes
    assert "create_code_host_pull_request" not in routes
    assert "send_feishu_text" not in routes
    assert "create_feishu_document" not in routes
    assert "append_feishu_document_text" not in routes
    assert "create_notion_page" not in routes
    assert "append_notion_page_text" not in routes
    assert "update_notion_page_title" not in routes
    assert "create_google_document" not in routes
    assert "append_google_document_text" not in routes
    assert "rename_google_drive_file" not in routes
    assert "create_linear_issue" not in routes
    assert "update_linear_issue" not in routes
    assert "create_linear_comment" not in routes


def test_mcp_error_text_never_requires_provider_specific_shape() -> None:
    assert plugin_portal_routes._mcp_error_text({"content": [{"type": "text", "text": "denied"}]}) == "denied"
    assert plugin_portal_routes._mcp_error_text({"content": []}) == "Plugin MCP 返回未知错误"
    assert plugin_portal_routes._mcp_error_text({}) == "Plugin MCP 返回未知错误"
