from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app import plugin_mcp_gateway, plugin_notion, plugin_notion_mcp, plugin_notion_runtime, plugin_runtime


OWNER = "usr_plugin_notion_747"
ROOT = Path(__file__).resolve().parents[2]
PAGE_ID = "11111111-1111-1111-1111-111111111111"
CHILD_BLOCK_ID = "22222222-2222-2222-2222-222222222222"
DATA_SOURCE_ID = "33333333-3333-3333-3333-333333333333"


def test_notion_credentials_are_verified_with_current_api_and_encrypted_at_rest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            calls.append({"client": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            calls.append({"method": method, "url": url, **kwargs})
            if url.endswith("/v1/users/me"):
                return httpx.Response(
                    200,
                    json={
                        "object": "user",
                        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "name": "FDEX Notion",
                        "type": "bot",
                        "bot": {
                            "workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                            "workspace_name": "FDEX Workspace",
                            "owner": {"type": "workspace", "workspace": True},
                        },
                    },
                )
            if url.endswith("/v1/search"):
                return httpx.Response(200, json={"object": "list", "results": [], "has_more": False, "next_cursor": None})
            raise AssertionError(url)

    monkeypatch.setattr(plugin_notion.httpx, "Client", FakeClient)
    token = "ntn_test_secret_token_747"
    bot = plugin_notion.verify_notion_token(token)
    assert bot["bot"]["workspace_name"] == "FDEX Workspace"
    assert calls[0]["client"]["follow_redirects"] is False
    assert calls[0]["client"]["trust_env"] is False
    assert calls[1]["url"] == "https://api.notion.com/v1/users/me"
    assert calls[1]["headers"]["Authorization"] == f"Bearer {token}"
    assert calls[1]["headers"]["Notion-Version"] == "2026-03-11"
    assert calls[3]["url"] == "https://api.notion.com/v1/search"
    assert calls[3]["json"]["page_size"] == 1

    store = plugin_notion.NotionCredentialStore(tmp_path / "notion.db", tmp_path / "notion.key")
    saved = store.save_verified(OWNER, token, bot)
    assert saved["workspace_name"] == "FDEX Workspace"
    raw = (tmp_path / "notion.db").read_bytes()
    assert token.encode() not in raw
    secret = store.get(OWNER, secret=True)
    assert secret is not None
    assert secret["access_token"] == token
    assert secret["token_configured"] is True


def test_notion_search_and_data_source_query_are_bounded_and_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(owner_id: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert owner_id == OWNER
        calls.append({"method": method, "path": path, **kwargs})
        if path == "/v1/search":
            body = kwargs["json_body"]
            assert body["query"] == "runbook"
            assert body["filter"] == {"property": "object", "value": "page"}
            assert body["sort"] == {"direction": "descending", "timestamp": "last_edited_time"}
            assert body["page_size"] == 2
            return {
                "object": "list",
                "results": [
                    {
                        "object": "page",
                        "id": PAGE_ID,
                        "url": "https://www.notion.so/page",
                        "last_edited_time": "2026-09-09T00:00:00Z",
                        "in_trash": False,
                        "parent": {"type": "workspace", "workspace": True},
                        "properties": {
                            "title": {
                                "id": "title",
                                "type": "title",
                                "title": [{"plain_text": "Runbook"}],
                            }
                        },
                    }
                ],
                "has_more": True,
                "next_cursor": "next-search",
            }
        if path.endswith("/query"):
            assert path == f"/v1/data_sources/{DATA_SOURCE_ID}/query"
            assert kwargs["json_body"] == {"page_size": 1, "result_type": "page"}
            return {
                "object": "list",
                "results": [
                    {
                        "object": "page",
                        "id": PAGE_ID,
                        "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
                        "properties": {
                            "Name": {
                                "id": "title",
                                "type": "title",
                                "title": [{"plain_text": "Row A"}],
                            }
                        },
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            }
        raise AssertionError(path)

    monkeypatch.setattr(plugin_notion, "_request", fake_request)
    result = plugin_notion.search_notion(OWNER, "runbook", object_filter="page", page_size=2)
    assert result["count"] == 1
    assert result["results"][0]["title"] == "Runbook"
    assert result["results"][0]["in_trash"] is False
    assert result["next_cursor"] == "next-search"

    rows = plugin_notion.query_notion_data_source(OWNER, DATA_SOURCE_ID, page_size=1)
    assert rows["count"] == 1
    assert rows["results"][0]["title"] == "Row A"
    assert rows["results"][0]["parent_id"] == DATA_SOURCE_ID


def test_notion_page_read_recurses_blocks_without_returning_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(owner_id: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert owner_id == OWNER
        if path == f"/v1/pages/{PAGE_ID}":
            return {
                "object": "page",
                "id": PAGE_ID,
                "url": "https://www.notion.so/runbook",
                "in_trash": False,
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {"id": "title", "type": "title", "title": [{"plain_text": "Runbook"}]}
                },
            }
        if path == f"/v1/blocks/{PAGE_ID}/children":
            return {
                "results": [
                    {
                        "object": "block",
                        "id": "44444444-4444-4444-4444-444444444444",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {"rich_text": [{"plain_text": "hello notion"}]},
                    },
                    {
                        "object": "block",
                        "id": CHILD_BLOCK_ID,
                        "type": "toggle",
                        "has_children": True,
                        "toggle": {"rich_text": [{"plain_text": "details"}]},
                    },
                ],
                "has_more": False,
                "next_cursor": None,
            }
        if path == f"/v1/blocks/{CHILD_BLOCK_ID}/children":
            return {
                "results": [
                    {
                        "object": "block",
                        "id": "55555555-5555-5555-5555-555555555555",
                        "type": "heading_2",
                        "has_children": False,
                        "heading_2": {"rich_text": [{"plain_text": "Nested"}]},
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            }
        raise AssertionError(path)

    monkeypatch.setattr(plugin_notion, "_request", fake_request)
    page = plugin_notion.read_notion_page(OWNER, PAGE_ID)
    assert page["title"] == "Runbook"
    assert page["block_count"] == 3
    assert "hello notion" in page["content"]
    assert "details" in page["content"]
    assert "## Nested" in page["content"]
    assert page["truncated"] is False
    assert "access_token" not in page


def test_notion_writes_use_2026_03_11_page_and_block_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(owner_id: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert owner_id == OWNER
        calls.append({"method": method, "path": path, **kwargs})
        if method == "POST" and path == "/v1/pages":
            return {
                "object": "page",
                "id": PAGE_ID,
                "parent": {"type": "page_id", "page_id": CHILD_BLOCK_ID},
                "properties": {
                    "title": {"id": "title", "type": "title", "title": [{"plain_text": "Created"}]}
                },
            }
        if method == "PATCH" and path == f"/v1/blocks/{PAGE_ID}/children":
            return {"results": [{"object": "block", "id": "66666666-6666-6666-6666-666666666666"}]}
        if method == "GET" and path == f"/v1/pages/{PAGE_ID}":
            return {
                "object": "page",
                "id": PAGE_ID,
                "properties": {
                    "Name": {"id": "title", "type": "title", "title": [{"plain_text": "Old"}]}
                },
            }
        if method == "PATCH" and path == f"/v1/pages/{PAGE_ID}":
            return {
                "object": "page",
                "id": PAGE_ID,
                "properties": {
                    "Name": {"id": "title", "type": "title", "title": [{"plain_text": "New title"}]}
                },
            }
        raise AssertionError((method, path))

    monkeypatch.setattr(plugin_notion, "_request", fake_request)

    created = plugin_notion.create_notion_page(OWNER, CHILD_BLOCK_ID, "Created", text="first line")
    assert created["id"] == PAGE_ID
    create_body = calls[-1]["json_body"]
    assert create_body["parent"] == {"page_id": CHILD_BLOCK_ID}
    assert create_body["properties"]["title"]["title"][0]["text"]["content"] == "Created"
    assert create_body["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "first line"

    appended = plugin_notion.append_notion_page_text(OWNER, PAGE_ID, "append me")
    assert appended["count"] == 1
    append_body = calls[-1]["json_body"]
    assert append_body["position"] == {"type": "end"}
    assert "after" not in append_body
    assert append_body["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "append me"

    updated = plugin_notion.update_notion_page_title(OWNER, PAGE_ID, "New title")
    assert updated["title"] == "New title"
    update_body = calls[-1]["json_body"]
    assert list(update_body["properties"]) == ["Name"]
    assert update_body["properties"]["Name"]["title"][0]["text"]["content"] == "New title"


def test_phase747_promotes_notion_and_installs_mcp_tools() -> None:
    plugin_notion_runtime.install_notion_runtime()
    plugin_notion_mcp.install_notion_mcp_tools()
    definition = plugin_runtime.plugin_definition("notion")
    assert definition.implementation == "native"
    assert definition.connect_path == "/account/plugins#plugin-notion"
    assert {tool.name for tool in definition.tools} >= {
        "notion.page.search",
        "notion.page.read",
        "notion.data_source.read",
        "notion.page.write",
    }
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["notion_search"]["risk"] == "read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["notion_read_page"]["runtime_tool"] == "notion.page.read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["notion_query_data_source"]["risk"] == "read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["notion_create_page"]["risk"] == "write"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["notion_append_page_text"]["risk"] == "write"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["notion_update_page_title"]["risk"] == "write"


def test_notion_mcp_executor_uses_runtime_tool_without_exposing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_notion_mcp.install_notion_mcp_tools()
    monkeypatch.setattr(plugin_mcp_gateway, "plugin_connection_status", lambda owner_id, plugin_id: {"connected": True})
    monkeypatch.setattr(plugin_mcp_gateway, "effective_agent_grant", lambda owner_id, employee, plugin_id: {"mode": "read"})
    seen: list[tuple[str, str]] = []

    def fake_run(owner_id: str, employee: dict[str, Any], plugin_id: str, tool_name: str, executor, **_kwargs: Any):
        seen.append((plugin_id, tool_name))
        return {
            "plugin_id": "notion",
            "query": "",
            "results": [{"object": "page", "id": PAGE_ID, "title": "A"}],
            "count": 1,
            "has_more": False,
            "next_cursor": "",
        }

    monkeypatch.setattr(plugin_mcp_gateway, "run_plugin_tool", fake_run)
    result = plugin_mcp_gateway._tool_call(
        {"owner_id": OWNER, "employee": {"id": 7, "name": "知识助手", "active": True}},
        "notion_search",
        {},
    )
    assert result["isError"] is False
    assert seen == [("notion", "notion.page.search")]
    assert PAGE_ID in result["content"][0]["text"]
    assert "access_token" not in result["content"][0]["text"]


def test_phase747_portal_lifecycle_export_and_import_order_are_wired() -> None:
    portal = (ROOT / "server/app/plugin_portal_routes.py").read_text(encoding="utf-8")
    page = (ROOT / "server/app/templates/user_plugins.html").read_text(encoding="utf-8")
    cleanup = (ROOT / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    export = (ROOT / "server/app/account_data_export.py").read_text(encoding="utf-8")
    installer = (ROOT / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")
    runtime = (ROOT / "server/app/plugin_notion_runtime.py").read_text(encoding="utf-8")

    assert "connect_notion" in portal
    assert "/account/plugins/notion/search.json" in page
    assert "Integration Token" in page
    assert "notion_search" in portal
    assert "notion_credential_store().delete_owner(clean)" in cleanup
    assert '"plugin_notion_connections"' in cleanup
    assert '"plugin_id": "notion"' in export
    assert '"notion_integration_token"' in export
    assert "install_notion_runtime()" in installer
    assert "install_notion_mcp_tools()" in installer
    assert "Phase 7.47" in installer
    assert 'sys.modules.get("app.plugin_mcp_gateway")' in runtime
    assert 'setattr(gateway, "plugin_connection_status", connection_status)' in runtime
