from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import codex_provider_compatibility as compatibility


def _provider() -> dict[str, object]:
    return {
        "id": 7,
        "name": "chat2api",
        "base_url": "https://chat2api.example/v1",
        "api_key": "secret-not-persisted",
        "enabled": True,
        "priority": 1,
        "protocol_order": ["responses", "chat"],
        "main_text_model": "gpt-5.6-sol",
        "backup_text_models": [],
        "timeout_seconds": 60,
    }


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(path="/opt/fdex/codex/codex", version="0.147.0", source="managed")


def _contract(runtime: str, bundle: str, close_revision: int) -> dict[str, object]:
    return {
        "object": "chat2api.version",
        "contract_version": 1,
        "server": {
            "runtime_version": runtime,
            "feature_revision": f"runtime-{runtime}-route-close-terminal-v{close_revision}",
        },
        "chrome_bridge": {
            "bundle_version": bundle,
            "build_revision": f"bundle-{bundle}",
            "route_window_authority_revision": 30,
            "route_close_terminal_revision": close_revision,
        },
    }


def test_chat2api_contract_identity_rotates_on_runtime_or_worker_contract() -> None:
    old = compatibility.chat2api_contract_identity(_contract("0.22.74", "0.8.30", 0))
    new_runtime = compatibility.chat2api_contract_identity(_contract("0.22.75", "0.8.30", 0))
    new_worker = compatibility.chat2api_contract_identity(_contract("0.22.75", "0.8.31", 91))
    assert old and new_runtime and new_worker
    assert old["identity"] != new_runtime["identity"]
    assert new_runtime["identity"] != new_worker["identity"]
    assert new_worker["runtime_version"] == "0.22.75"
    assert new_worker["bundle_version"] == "0.8.31"
    assert compatibility.chat2api_contract_identity({"object": "other.version"}) is None


def test_upstream_chat2api_change_invalidates_fresh_full_smoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import codex_subagent_governance as governance

    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("governance=v1",))
    provider = _provider()
    runtime = _runtime()
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")

    v74 = compatibility.chat2api_contract_identity(_contract("0.22.74", "0.8.30", 0))
    assert v74 is not None
    store.record_upstream_contract(7, v74)
    fingerprint = compatibility.provider_runtime_fingerprint(
        provider,
        runtime,
        upstream_contract_identity=v74["identity"],
    )
    store.record(
        7,
        fingerprint=fingerprint,
        level="full",
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model=str(provider["main_text_model"]),
        base_url=str(provider["base_url"]),
        latency_ms=100,
        evidence={"wire": True, "tools": True, "mcp": True, "subagent": True},
    )
    assert store.evaluate(provider, runtime)["valid"] is True

    v75 = compatibility.chat2api_contract_identity(_contract("0.22.75", "0.8.31", 91))
    assert v75 is not None
    store.record_upstream_contract(7, v75)
    invalid = store.evaluate(provider, runtime)
    assert invalid["valid"] is False
    assert invalid["upstream_contract"]["runtime_version"] == "0.22.75"
    assert "上游运行合同已变化" in invalid["reason"]


def test_health_refreshes_upstream_contract_before_compatibility_snapshot() -> None:
    root = Path(__file__).parents[1] / "app"
    health = (root / "codex_agent_health.py").read_text(encoding="utf-8")
    check = health[health.index("async def run_codex_agent_health_check"):]
    assert "probe_chat2api_contract" in health
    assert check.index("live_results = await asyncio.gather") < check.index(
        "selected, compatibility = await asyncio.to_thread(_compatibility_snapshot, runtime)"
    )

    smoke = (root / "codex_provider_smoke.py").read_text(encoding="utf-8")
    function = smoke[smoke.index("async def run_codex_provider_smoke"):]
    assert function.index("discovered_upstream = await probe_chat2api_contract(provider)") < function.index(
        "fingerprint = provider_runtime_fingerprint("
    )
    assert '"upstream_contract"' in function


def test_upstream_contract_store_never_persists_provider_secret(tmp_path: Path) -> None:
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    identity = compatibility.chat2api_contract_identity(_contract("0.22.75", "0.8.31", 91))
    assert identity is not None
    store.record_upstream_contract(7, identity)
    assert b"secret-not-persisted" not in store.path.read_bytes()
