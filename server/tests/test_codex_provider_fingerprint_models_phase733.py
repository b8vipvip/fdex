from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import codex_engine
from app import codex_provider_compatibility as compatibility


def _provider(model: str) -> dict[str, object]:
    return {
        "id": 733,
        "name": "Backup-only Responses",
        "base_url": "https://provider.example/v1",
        "api_key": "phase733-key",
        "enabled": True,
        "priority": 1,
        "protocol_order": ["responses"],
        "main_text_model": "",
        "backup_text_models": [model],
        "timeout_seconds": 60,
    }


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(path="/opt/fdex/codex", version="0.147.0", source="managed")


def test_backup_only_provider_is_a_valid_codex_candidate() -> None:
    selected = codex_engine.select_codex_provider_from([_provider("backup-model-a")])

    assert selected is not None
    assert selected.model == "backup-model-a"


def test_fingerprint_rotates_when_effective_backup_only_model_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import codex_subagent_governance as governance

    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("governance=v1",))
    first = compatibility.provider_runtime_fingerprint(_provider("backup-model-a"), _runtime())
    second = compatibility.provider_runtime_fingerprint(_provider("backup-model-b"), _runtime())

    assert first != second


def test_old_full_record_invalidates_after_backup_only_model_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import codex_subagent_governance as governance

    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("governance=v1",))
    runtime = _runtime()
    first_provider = _provider("backup-model-a")
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    fingerprint = compatibility.provider_runtime_fingerprint(first_provider, runtime)
    store.record(
        733,
        fingerprint=fingerprint,
        level="full",
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model="backup-model-a",
        base_url=str(first_provider["base_url"]),
        latency_ms=100,
        evidence={"wire": True, "tools": True, "mcp": True, "subagent": True, "reasoning": True},
    )

    assert store.evaluate(first_provider, runtime)["valid"] is True
    changed = store.evaluate(_provider("backup-model-b"), runtime)
    assert changed["valid"] is False
    assert "已变化" in str(changed["reason"])
