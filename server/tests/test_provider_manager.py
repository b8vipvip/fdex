from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.provider_manager import ProviderStore, auto_test_due, model_candidates, normalize_base_url


def test_provider_store_encrypts_and_masks_key(tmp_path: Path) -> None:
    store = ProviderStore(tmp_path / "providers.db", tmp_path / "providers.key")
    item = store.create(
        name="chat2api",
        base_url="https://relay.example/v1/chat/completions",
        api_key="sk-super-secret-123456",
        priority=1,
        main_text_model="gpt-5.6-sol",
        backup_text_models=["gpt-5.5", "gpt-5.5"],
        protocol_order=["chat", "responses"],
        auto_test_enabled=True,
    )

    assert item["base_url"] == "https://relay.example/v1"
    assert item["api_key_configured"] is True
    assert "super-secret" not in item["api_key_masked"]
    assert item["backup_text_models"] == ["gpt-5.5"]
    assert model_candidates(item) == ["gpt-5.6-sol", "gpt-5.5"]
    assert store.get(item["id"], include_secret=True)["api_key"] == "sk-super-secret-123456"

    updated = store.update(item["id"], name="primary", api_key="", priority=2)
    assert updated["name"] == "primary"
    assert store.get(item["id"], include_secret=True)["api_key"] == "sk-super-secret-123456"

    store.clear_key(item["id"])
    assert store.get(item["id"], include_secret=True)["api_key"] == ""
    assert (tmp_path / "providers.key").exists()


def test_auto_test_due() -> None:
    now = datetime.now(timezone.utc)
    provider = {
        "enabled": True,
        "auto_test_enabled": True,
        "auto_test_interval_hours": 12,
        "last_test_at": (now - timedelta(hours=13)).isoformat(),
    }
    assert auto_test_due(provider, now) is True
    provider["last_test_at"] = (now - timedelta(hours=2)).isoformat()
    assert auto_test_due(provider, now) is False
    provider["enabled"] = False
    assert auto_test_due(provider, now) is False


def test_normalize_base_url() -> None:
    assert normalize_base_url("https://relay.example/v1/chat/completions") == "https://relay.example/v1"
    assert normalize_base_url("relay.example/v1") == "https://relay.example/v1"
