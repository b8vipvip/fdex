from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from app import plugin_google_drive, plugin_google_drive_mcp, plugin_google_drive_runtime, plugin_mcp_gateway, plugin_runtime


OWNER = "usr_plugin_drive_748"
ROOT = Path(__file__).resolve().parents[2]


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        google_drive_oauth_ready=True,
        public_base_url="https://fdex.example",
        fdex_google_oauth_flow_minutes=10,
        fdex_google_oauth_client_id="google-client-748.apps.googleusercontent.com",
        fdex_google_oauth_client_secret="google-secret-748",
        fdex_google_oauth_scope="https://www.googleapis.com/auth/drive",
    )


def test_google_drive_oauth_is_pkce_owner_bound_and_credentials_are_encrypted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = plugin_google_drive.GoogleDriveStore(tmp_path / "drive.db", tmp_path / "drive.key")
    monkeypatch.setattr(plugin_google_drive, "fresh_settings", _settings)
    flow = store.start_flow(OWNER)
    query = parse_qs(urlparse(flow["authorize_url"]).query)
    assert query["client_id"] == ["google-client-748.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["https://fdex.example/account/plugins/google-drive/oauth/callback"]
    assert query["access_type"] == ["offline"]
    assert "consent" in query["prompt"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["https://www.googleapis.com/auth/drive"]

    monkeypatch.setattr(
        plugin_google_drive,
        "_token_request",
        lambda form, *, context: {
            "access_token": "access-token-748-secret-value",
            "refresh_token": "refresh-token-748-secret-value",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/drive",
        },
    )
    monkeypatch.setattr(
        plugin_google_drive,
        "verify_google_drive_access",
        lambda token: {
            "user": {
                "emailAddress": "drive@example.com",
                "displayName": "Drive User",
                "permissionId": "perm748",
            }
        },
    )
    saved = store.complete_flow(OWNER, state=query["state"][0], code="oauth-code-748")
    assert saved["account_email"] == "drive@example.com"
    assert saved["refresh_token_configured"] is True
    raw = (tmp_path / "drive.db").read_bytes()
    assert b"access-token-748-secret-value" not in raw
    assert b"refresh-token-748-secret-value" not in raw
    secret = store.get(OWNER, secret=True)
    assert secret is not None
    assert secret["access_token"] == "access-token-748-secret-value"
    assert secret["refresh_token"] == "refresh-token-748-secret-value"


def test_drive_search_is_bounded_and_escapes_user_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_api(owner_id: str, method: str, root: str, path: str, **kwargs: Any) -> dict[str, Any]:
        seen.update({"owner_id": owner_id, "method": method, "root": root, "path": path, **kwargs})
        return {
            "nextPageToken": "next748",
            "files": [{
                "id": "file_12345678",
                "name": "Spec",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-09-09T00:00:00Z",
                "capabilities": {"canEdit": True, "canDownload": True, "canCopy": True},
                "owners": [{"emailAddress": "drive@example.com"}],
            }],
        }

    monkeypatch.setattr(plugin_google_drive, "_api_request", fake_api)
    result = plugin_google_drive.search_google_drive_files(OWNER, "O'Reilly", page_size=5)
    assert result["count"] == 1
    assert result["next_page_token"] == "next748"
    assert seen["path"] == "/files"
    assert seen["params"]["pageSize"] == 5
    assert seen["params"]["includeItemsFromAllDrives"] == "true"
    assert "O\\'Reilly" in seen["params"]["q"]
    assert result["files"][0]["can_edit"] is True


def test_drive_reads_docs_sheets_and_slides_with_hard_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_api(owner_id: str, method: str, root: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((root, path, kwargs))
        if root == plugin_google_drive._DOCS_ROOT:
            return {
                "tabs": [{
                    "tabProperties": {"title": "Main"},
                    "documentTab": {"body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "hello doc\n"}}]}}]}},
                    "childTabs": [],
                }]
            }
        if root == plugin_google_drive._SHEETS_ROOT and path.endswith("/spreadsheets/sheet_12345678"):
            return {"sheets": [{"properties": {"title": "Data", "index": 0}}]}
        if root == plugin_google_drive._SHEETS_ROOT and "/values/" in path:
            return {"values": [["A", "B"], [1, 2]]}
        if root == plugin_google_drive._SLIDES_ROOT:
            return {
                "slides": [{
                    "pageElements": [{"shape": {"text": {"textElements": [{"textRun": {"content": "slide text"}}]}}}]
                }]
            }
        raise AssertionError((root, path))

    monkeypatch.setattr(plugin_google_drive, "_api_request", fake_api)
    doc, doc_truncated = plugin_google_drive._read_google_doc(OWNER, "doc_12345678")
    assert "hello doc" in doc
    assert doc_truncated is False

    sheet, sheet_truncated = plugin_google_drive._read_google_sheet(OWNER, "sheet_12345678")
    assert "## Data" in sheet
    assert "A\tB" in sheet
    assert sheet_truncated is False
    sheet_value_call = next(item for item in calls if "/values/" in item[1])
    assert "A1%3AZ500" in sheet_value_call[1]

    slides, slides_truncated = plugin_google_drive._read_google_slides(OWNER, "slides_12345678")
    assert "Slide 1" in slides
    assert "slide text" in slides
    assert slides_truncated is False


def test_drive_document_writes_use_docs_and_drive_api_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api(owner_id: str, method: str, root: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, "root": root, "path": path, **kwargs})
        if path == "/documents":
            return {"documentId": "doc_12345678", "title": "Runbook"}
        if path.endswith(":batchUpdate"):
            return {"replies": [{}]}
        if root == plugin_google_drive._DRIVE_ROOT and method == "PATCH":
            return {"id": "doc_12345678", "name": "Renamed", "mimeType": plugin_google_drive._GOOGLE_DOC}
        raise AssertionError((method, root, path))

    monkeypatch.setattr(plugin_google_drive, "_api_request", fake_api)
    monkeypatch.setattr(
        plugin_google_drive,
        "_file_metadata",
        lambda owner_id, file_id: {"id": file_id, "name": "Runbook", "mime_type": plugin_google_drive._GOOGLE_DOC},
    )
    monkeypatch.setattr(plugin_google_drive, "_document_end_index", lambda owner_id, file_id: 9)

    created = plugin_google_drive.create_google_document(OWNER, "Runbook")
    assert created["file_id"] == "doc_12345678"
    assert calls[-1]["json_body"] == {"title": "Runbook"}

    appended = plugin_google_drive.append_google_document_text(OWNER, "doc_12345678", "new line")
    assert appended["insert_index"] == 9
    request = calls[-1]["json_body"]["requests"][0]["insertText"]
    assert request == {"location": {"index": 9}, "text": "new line"}

    renamed = plugin_google_drive.rename_google_drive_file(OWNER, "doc_12345678", "Renamed")
    assert renamed["name"] == "Renamed"
    assert calls[-1]["method"] == "PATCH"
    assert calls[-1]["json_body"] == {"name": "Renamed"}


def test_phase748_promotes_drive_and_installs_mcp_tools() -> None:
    plugin_google_drive_runtime.install_google_drive_runtime()
    plugin_google_drive_mcp.install_google_drive_mcp_tools()
    definition = plugin_runtime.plugin_definition("google-drive")
    assert definition.implementation == "native"
    assert definition.connect_path == "/account/plugins#plugin-google-drive"
    assert {tool.name for tool in definition.tools} >= {"drive.file.search", "drive.file.read", "drive.file.write"}
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["drive_search_files"]["risk"] == "read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["drive_read_file"]["runtime_tool"] == "drive.file.read"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["drive_create_document"]["risk"] == "write"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["drive_append_document_text"]["risk"] == "write"
    assert plugin_mcp_gateway._TOOL_DEFINITIONS["drive_rename_file"]["risk"] == "write"


def test_phase748_portal_config_lifecycle_and_export_are_wired() -> None:
    portal = (ROOT / "server/app/plugin_portal_routes.py").read_text(encoding="utf-8")
    page = (ROOT / "server/app/templates/user_plugins.html").read_text(encoding="utf-8")
    config = (ROOT / "server/app/config.py").read_text(encoding="utf-8")
    cleanup = (ROOT / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    export = (ROOT / "server/app/account_data_export.py").read_text(encoding="utf-8")
    installer = (ROOT / "server/app/codex_remote_mcp_install.py").read_text(encoding="utf-8")

    assert "/google-drive/oauth/start" in portal
    assert "/google-drive/oauth/callback" in portal
    assert "search_google_drive_files" in portal
    assert "drive_search_files" in portal
    assert "使用 Google 授权连接" in page
    assert "FDEX_GOOGLE_OAUTH_CLIENT_ID" in page
    assert "fdex_google_oauth_client_id" in config
    assert "google_drive_oauth_ready" in config
    assert "google_drive_store().delete_owner(clean)" in cleanup
    assert '"plugin_google_drive_connections"' in cleanup
    assert '"plugin_id": "google-drive"' in export
    assert '"google_drive_access_token"' in export
    assert '"google_drive_refresh_token"' in export
    assert "install_google_drive_runtime()" in installer
    assert "install_google_drive_mcp_tools()" in installer
    assert "Phase 7.48" in installer
