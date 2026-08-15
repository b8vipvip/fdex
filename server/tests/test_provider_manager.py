from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.provider_manager import (
    ProviderStore,
    audio_model_candidates,
    auto_test_due,
    image_model_candidates,
    model_candidates,
    normalize_base_url,
    text_model_candidates,
)


def test_provider_store_encrypts_masks_and_routes_capability_models(tmp_path: Path) -> None:
    store = ProviderStore(tmp_path / "providers.db", tmp_path / "providers.key")
    item = store.create(
        name="chat2api",
        base_url="https://relay.example/v1/chat/completions",
        api_key="sk-super-secret-123456",
        priority=1,
        main_text_model="gpt-5.6-sol",
        backup_text_models=["gpt-5.5", "gpt-5.5"],
        main_image_model="gpt-image-1",
        backup_image_models=["image-backup"],
        main_audio_model="gpt-audio",
        backup_audio_models=["gpt-audio-mini"],
        audio_protocol="chat_audio",
        audio_voice="marin",
        audio_format="wav",
        protocol_order=["chat", "responses"],
        auto_test_enabled=True,
    )

    assert item["base_url"] == "https://relay.example/v1"
    assert item["api_key_configured"] is True
    assert "super-secret" not in item["api_key_masked"]
    assert item["backup_text_models"] == ["gpt-5.5"]
    assert model_candidates(item) == ["gpt-5.6-sol", "gpt-5.5"]
    assert text_model_candidates(item, vision=True) == ["gpt-5.6-sol", "gpt-5.5"]
    assert image_model_candidates(item) == ["gpt-image-1", "image-backup"]
    assert audio_model_candidates(item) == ["gpt-audio", "gpt-audio-mini"]
    assert item["audio_protocol"] == "chat_audio"
    assert item["audio_voice"] == "marin"
    assert store.get(item["id"], include_secret=True)["api_key"] == "sk-super-secret-123456"

    updated = store.update(
        item["id"],
        name="primary",
        api_key="",
        priority=2,
        main_vision_model="vision-special",
        backup_vision_models=["vision-backup"],
        audio_protocol="realtime",
    )
    assert updated["name"] == "primary"
    assert text_model_candidates(updated, vision=True) == ["vision-special", "vision-backup"]
    assert updated["audio_protocol"] == "realtime"
    assert store.get(item["id"], include_secret=True)["api_key"] == "sk-super-secret-123456"

    store.clear_key(item["id"])
    assert store.get(item["id"], include_secret=True)["api_key"] == ""
    assert (tmp_path / "providers.key").exists()


def test_existing_provider_database_is_migrated_in_place(tmp_path: Path) -> None:
    db_path = tmp_path / "providers.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key_cipher TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            main_text_model TEXT NOT NULL DEFAULT '',
            backup_text_models_json TEXT NOT NULL DEFAULT '[]',
            main_vision_model TEXT NOT NULL DEFAULT '',
            backup_vision_models_json TEXT NOT NULL DEFAULT '[]',
            protocol_order_json TEXT NOT NULL DEFAULT '[\"chat\"]',
            model_capabilities_json TEXT NOT NULL DEFAULT '{}',
            timeout_seconds INTEGER NOT NULL DEFAULT 60,
            auto_test_enabled INTEGER NOT NULL DEFAULT 0,
            auto_test_interval_hours INTEGER NOT NULL DEFAULT 12,
            last_test_at TEXT,
            last_status TEXT NOT NULL DEFAULT '未测试',
            last_latency_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()

    store = ProviderStore(db_path, tmp_path / "providers.key")
    store.init()
    with store.db() as migrated:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(providers)").fetchall()}
    assert {
        "main_image_model",
        "backup_image_models_json",
        "main_audio_model",
        "backup_audio_models_json",
        "audio_protocol",
        "audio_voice",
        "audio_format",
    }.issubset(columns)


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
    assert normalize_base_url("https://relay.example/v1/images/generations") == "https://relay.example/v1"
    assert normalize_base_url("https://relay.example/v1/audio/speech") == "https://relay.example/v1"
    assert normalize_base_url("relay.example/v1") == "https://relay.example/v1"
