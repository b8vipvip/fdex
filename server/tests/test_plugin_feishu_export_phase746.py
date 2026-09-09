from __future__ import annotations

import pytest

from app import account_data_export


OWNER = "usr_plugin_feishu_export_746"


def test_native_plugin_export_includes_feishu_metadata_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    class CodeHosts:
        def get(self, owner_id: str, plugin_id: str):
            assert owner_id == OWNER
            assert plugin_id in {"gitlab", "gitee"}
            return None

    class Feishu:
        def get(self, owner_id: str):
            assert owner_id == OWNER
            return {
                "app_id": "cli_export746",
                "last_checked_at": "2026-09-09T02:00:00+00:00",
                "created_at": "2026-09-09T01:00:00+00:00",
                "updated_at": "2026-09-09T02:00:00+00:00",
                "secret_configured": True,
                "app_secret": "must-not-export",
                "tenant_access_token": "must-not-export-token",
            }

    monkeypatch.setattr(account_data_export, "code_host_credential_store", lambda: CodeHosts())
    monkeypatch.setattr(account_data_export, "feishu_credential_store", lambda: Feishu())
    rows = account_data_export._native_plugin_connections(OWNER)
    assert rows == [{
        "plugin_id": "feishu",
        "app_id": "cli_export746",
        "last_checked_at": "2026-09-09T02:00:00+00:00",
        "created_at": "2026-09-09T01:00:00+00:00",
        "updated_at": "2026-09-09T02:00:00+00:00",
        "secret_configured": True,
    }]
    serialized = repr(rows)
    assert "must-not-export" not in serialized
    assert "tenant_access_token" not in serialized
