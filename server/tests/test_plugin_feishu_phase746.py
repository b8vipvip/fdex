from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app import plugin_feishu, plugin_feishu_mcp, plugin_feishu_runtime, plugin_mcp_gateway, plugin_runtime


OWNER = "usr_plugin_feishu_746"
ROOT = Path(__file__).resolve().parents[2]


def test_feishu_credentials_are_verified_and_encrypted_at_rest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            calls.append({"client": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            calls.append({"url": url, **kwargs})
            return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-secret-runtime-token", "expire": 7200})

    monkeypatch.setattr(plugin_feishu.httpx, "Client", FakeClient)
    verified = plugin_feishu.verify_feishu_credentials("cli_test746", "app-secret-746")
    assert verified["tenant_access_token"] == "t-secret-runtime-token"
    assert calls[0]["client"]["follow_redirects"] is False
    assert calls[0]["client"]["trust_env"] is False
    assert calls[1]["url"] == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    assert calls[1]["json"] == {"app_id": "cli_test746", "app_secret": "app-secret-746"}

    store = plugin_feishu.FeishuCredentialStore(tmp_path / "feishu.db", tmp_path / "feishu.key")
    saved = store.save_verified(
        OWNER,
        app_id="cli_test746",
        app_secret="app-secret-746",
        tenant_access_token="t-secret-runtime-token",
        expires_in=7200,
    )
    assert saved["app_id"] == "cli_test746"
    raw = (tmp_path / "feishu.db").read_bytes()
    assert b"app-secret-746" not in raw
    assert b"t-secret-runtime-token" not in raw
    secret = store.get(OWNER, secret=True)
    assert secret is not None
    assert secret["app_secret"] == "app-secret-746"
    assert secret["tenant_access_token"] == "t-secret-runtime-token"


def test_feishu_read_surfaces_are_bounded_and_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(owner_id: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert owner_id == OWNER
        if path == "/im/v1/chats":
            assert kwargs["params"]["page_size"] == 2
            return {
                "code": 0,
                "data": {
                    "items": [
                        {"chat_id": "oc_a", "name": "A", "description": "one", "owner_id": "ou_1", "external": False},
                        {"chat_id": "oc_b", "name": "B", "description": "two", "owner_id": "ou_2", "external": True},
                    ],
                    "has_more": True,
                    "page_token": "next-chat",
                },
            }
        if path == "/im/v1/messages":
            params = kwargs["params"]
            assert params["container_id_type"] == "chat"
            assert params["container_id"] == "oc_a"
            assert params["sort_type"] == "ByCreateTimeDesc"
            assert params["card_msg_content_type"] == "user_card_content"
            return {
                "code": 0,
                "data": {
                    "items": [{
                        "message_id": "om_1",
                        "msg_type": "text",
                        "create_time": "123",
                        "sender": {"id": "ou_1", "sender_type": "user"},
                        "body": {"content": '{"text":"hello"}'},
                    }],
                    "has_more": False,
                    "page_token": "",
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(plugin_feishu, "_request", fake_request)
    chats = plugin_feishu.list_feishu_chats(OWNER, page_size=2)
    assert chats["count"] == 2
    assert chats["items"][1]["external"] is True
    assert chats["page_token"] == "next-chat"

    messages = plugin_feishu.list_feishu_messages(OWNER, "oc_a", page_size=1)
    assert messages["count"] == 1
    assert messages["items"][0]["message_id"] == "om_1"
    assert messages["items"][0]["content"] == '{"text":"hello"}'


def test_feishu_message_and_document_writes_use_real_openapi_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(owner_id: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"owner_id": owner_id, "method": method, "path": path, **kwargs})
        if path == "/im/v1/messages":
            return {"code": 0, "data": {"message": {"message_id": "om_sent", "chat_id": "oc_a", "msg_type": "text"}}}
        if path == "/docx/v1/documents":
            return {"code": 0, "data": {"document": {"document_id": "doxc746", "revision_id": 1, "title": "Runbook"}}}
        if path.endswith("/raw_content"):
            return {"code": 0, "data": {"content": "hello doc"}}
        if path.endswith("/children"):
            return {"code": 0, "data": {"children": [{"block_id": "blk_1"}]}}
        raise AssertionError(path)

    monkeypatch.setattr(plugin_feishu, "_request", fake_request)

    sent = plugin_feishu.send_feishu_text(OWNER, "oc_a", "hello", receive_id_type="chat_id")
    assert sent["message_id"] == "om_sent"
    assert calls[-1]["params"] == {"receive_id_type": "chat_id"}
    assert calls[-1]["json_body"]["msg_type"] == "text"
    assert calls[-1]["json_body"]["content"] == '{"text": "hello"}'

    created = plugin_feishu.create_feishu_document(OWNER, "Runbook", folder_token="fld746")
    assert created["document_id"] == "doxc746"
    assert calls[-1]["json_body"] == {"title": "Runbook", "folder_token": "fld746"}

    read = plugin_feishu.read_feishu_document(OWNER, "doxc746")
    assert read["content"] == "hello doc"

    appended = plugin_feishu.append_feishu_document_text(OWNER, "doxc746", "line one\nline two")
    assert appended["created_blocks"] == ["blk_1"]
    assert calls[-1]["path"] == "/docx/v1/documents/doxc746/blocks/doxc746/children"
    children = calls[-1]["json_body"]["children"]
    assert children[0]["block_type"] == 2
    assert children[0]["text"]["elements"][0]["text_run"]["content"] == "line one\nline two"


def test_phase746_promotes_feishu_and_installs_mcp_tools() -> None:
    plugin_feishu_runtime.install_feishu_runtime()
    plugin_feishu_mcp.install_feishu_mcp_tools()
    definition = plugin_runtime.plugin_definition("feishu")
    assert definition.implementation == "native"
    assert definition.connect_path == "/account/plugins#plugin-feishu"
    assert {tool.name for tool in definition.tools} >= {
        "feishu.chats.list",
        "feishu.message.read",
        "feishu.document.read",
        "feishu.message.send",
        "feishu.document.write",
    }
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["feishu_list_chats"]["risk"] == "read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["feishu_list_messages"]["runtime_tool"] == "feishu.message.read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["feishu_send_text_message"]["risk"] == "write"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["feishu_create_document"]["risk"] == "write"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["feishu_append_document_text"]["risk"] == "write"


def test_phase746_portal_and_account_erasure_are_wired() -> None:
    portal = (ROOT / "server/app/plugin_portal_routes.py").read_text(encoding="utf-8")
    page = (ROOT / "server/app/templates/user_plugins.html").read_text(encoding="utf-8")
    cleanup = (ROOT / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    installer = (ROOT / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")

    assert "connect_feishu" in portal
    assert 'name="app_id"' in page
    assert 'name="app_secret"' in page
    assert "/account/plugins/feishu/chats.json" in page
    assert "feishu_list_chats" in portal
    assert "feishu_credential_store().delete_owner(clean)" in cleanup
    assert '"plugin_feishu_connections"' in cleanup
    assert "install_feishu_mcp_tools()" in installer
    assert "Phase 7.46" in installer
