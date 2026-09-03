from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app import codex_agent_health as health


def test_phase737_store_persists_snapshot_history_and_failure_counter(tmp_path: Path) -> None:
    store = health.CodexAgentHealthStore(tmp_path / "health.db")
    snapshot = {
        "state": "READY",
        "code": "READY",
        "reason": "healthy",
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "providers": [],
    }
    store.save(snapshot)

    assert store.latest() == snapshot
    rows = store.history(10)
    assert rows and rows[0]["state"] == "READY"

    assert store.record_provider_live(7, state="unreachable", status_code=None, latency_ms=11, healthy=False) == 1
    assert store.record_provider_live(7, state="upstream_error", status_code=503, latency_ms=22, healthy=False) == 2
    assert store.record_provider_live(7, state="ok", status_code=200, latency_ms=33, healthy=True) == 0


def test_phase737_multi_worker_monitor_lease_is_single_leader(tmp_path: Path) -> None:
    store = health.CodexAgentHealthStore(tmp_path / "health.db")
    assert store.try_acquire_lease("worker-a", ttl_seconds=90) is True
    assert store.try_acquire_lease("worker-b", ttl_seconds=90) is False
    assert store.try_acquire_lease("worker-a", ttl_seconds=90) is True
    store.release_lease("worker-a")
    assert store.try_acquire_lease("worker-b", ttl_seconds=90) is True


def test_phase737_compatibility_codes_are_structured_not_error_string_retry_policy() -> None:
    current = "current-fingerprint"
    missing = {"valid": False, "record": None, "fingerprint_current": current, "level": "none", "required_level": "full"}
    mismatch = {
        "valid": False,
        "record": {"fingerprint": "old", "error": ""},
        "fingerprint_current": current,
        "level": "full",
        "required_level": "full",
        "age_hours": 1,
    }
    expired = {
        "valid": False,
        "record": {"fingerprint": current, "error": ""},
        "fingerprint_current": current,
        "level": "full",
        "required_level": "full",
        "age_hours": health.COMPATIBILITY_MAX_AGE_HOURS + 1,
    }
    insufficient = {
        "valid": False,
        "record": {"fingerprint": current, "error": ""},
        "fingerprint_current": current,
        "level": "tools",
        "required_level": "full",
        "age_hours": 1,
    }

    assert health._compatibility_code(missing) == "SMOKE_MISSING"
    assert health._compatibility_code(mismatch) == "FINGERPRINT_MISMATCH"
    assert health._compatibility_code(expired) == "SMOKE_EXPIRED"
    assert health._compatibility_code(insufficient) == "COMPATIBILITY_INSUFFICIENT"
    assert health._compatibility_code({"valid": True}) == "READY"


def test_phase737_host_probe_refreshes_when_runtime_provider_or_age_changes() -> None:
    now = datetime.now(UTC)
    runtime = SimpleNamespace(version="0.147.0")
    provider = SimpleNamespace(provider_id=7)
    recent = {
        "host": {
            "checked_at": now.isoformat(timespec="seconds"),
            "runtime_version": "0.147.0",
            "provider_id": 7,
        }
    }
    old = {
        "host": {
            "checked_at": (now - timedelta(seconds=health.HOST_HANDSHAKE_INTERVAL_SECONDS + 5)).isoformat(timespec="seconds"),
            "runtime_version": "0.147.0",
            "provider_id": 7,
        }
    }

    assert health._host_probe_due(recent, runtime, provider) is False
    assert health._host_probe_due(old, runtime, provider) is True
    assert health._host_probe_due(recent, SimpleNamespace(version="0.148.0"), provider) is True
    assert health._host_probe_due(recent, runtime, SimpleNamespace(provider_id=9)) is True


def test_phase737_runtime_failure_is_blocked_without_generic_agent_fallback(monkeypatch, tmp_path: Path) -> None:
    store = health.CodexAgentHealthStore(tmp_path / "health.db")

    def no_runtime():
        raise RuntimeError("codex binary unavailable")

    class EmptyProviders:
        def list(self, *args, **kwargs):
            return []

    monkeypatch.setattr(health, "codex_agent_health_store", lambda: store)
    monkeypatch.setattr(health, "resolve_codex_runtime", no_runtime)
    monkeypatch.setattr(
        health,
        "codex_process_isolation_status",
        lambda: {"enforced": True, "required": True, "reason": "", "controllers": ["cpu", "memory", "pids"]},
    )
    monkeypatch.setattr(health, "provider_store", lambda: EmptyProviders())
    monkeypatch.setattr(health, "fresh_settings", lambda: SimpleNamespace(fdex_agent_enabled=True))

    result = asyncio.run(health.run_codex_agent_health_check(force_host=True))

    assert result["state"] == "BLOCKED"
    assert result["code"] == "RUNTIME_UNAVAILABLE"
    assert "codex binary unavailable" in result["reason"]
    assert result["selected_provider"] is None


def test_phase737_models_auth_probe_is_advisory_and_never_overrides_full_gate(monkeypatch, tmp_path: Path) -> None:
    store = health.CodexAgentHealthStore(tmp_path / "health.db")
    runtime = SimpleNamespace(version="0.147.0", source="bundled", path="/codex")
    selected = SimpleNamespace(provider_id=7, name="Gateway", model="gpt-test")
    provider = {"id": 7, "name": "Gateway", "api_key": "secret"}

    class Providers:
        def list(self, *args, **kwargs):
            return [provider]

    def compatibility_snapshot(_runtime):
        return selected, [
            {
                "provider_id": 7,
                "provider_name": "Gateway",
                "model": "gpt-test",
                "eligible": True,
                "selected": True,
                "level": "full",
                "code": "READY",
                "reason": "fresh full Codex compatibility smoke verified",
                "age_hours": 12.0,
                "remaining_hours": 156.0,
            }
        ]

    async def live_probe(_provider, _store):
        return {
            "provider_id": 7,
            "provider_name": "Gateway",
            "model": "gpt-test",
            "state": "auth_error",
            "status_code": 403,
            "latency_ms": 21,
            "consecutive_failures": 1,
            "error": "HTTP 403",
        }

    async def host_probe(_runtime, _provider):
        return {
            "state": "ok",
            "code": "READY",
            "reason": "handshake completed",
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "latency_ms": 8,
            "runtime_version": "0.147.0",
            "provider_id": 7,
            "process_unit": "fdex-codex-test.service",
            "server_info_present": True,
        }

    monkeypatch.setattr(health, "codex_agent_health_store", lambda: store)
    monkeypatch.setattr(health, "resolve_codex_runtime", lambda: runtime)
    monkeypatch.setattr(
        health,
        "codex_process_isolation_status",
        lambda: {
            "enforced": True,
            "required": True,
            "reason": "",
            "controllers": ["cpu", "memory", "pids"],
            "parent_unit": "fdex.service",
            "memory_mb": 2048,
            "cpu_percent": 150,
            "pids_max": 512,
        },
    )
    monkeypatch.setattr(health, "_compatibility_snapshot", compatibility_snapshot)
    monkeypatch.setattr(health, "provider_store", lambda: Providers())
    monkeypatch.setattr(health, "_probe_provider_live", live_probe)
    monkeypatch.setattr(health, "_probe_host", host_probe)
    monkeypatch.setattr(health, "fresh_settings", lambda: SimpleNamespace(fdex_agent_enabled=True))

    result = asyncio.run(health.run_codex_agent_health_check(force_host=True))

    assert result["state"] == "DEGRADED"
    assert result["code"] == "PROVIDER_AUTH_PROBE_FAILED"
    assert result["selected_provider"]["provider_id"] == 7
    assert result["compatibility"][0]["eligible"] is True


def test_phase737_admin_console_wiring_and_polling_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    main = (root / "server/app/main.py").read_text(encoding="utf-8")
    template = (root / "server/app/templates/agent_settings.html").read_text(encoding="utf-8")
    script = (root / "server/app/static/agent_health.js").read_text(encoding="utf-8")
    routes = (root / "server/app/codex_agent_health_admin_routes.py").read_text(encoding="utf-8")

    assert "start_codex_agent_health_monitor" in main
    assert "stop_codex_agent_health_monitor" in main
    assert "codex_agent_health_admin_router" in main
    assert "Codex Agent 链路监控" in template
    assert "立即检测链路" in template
    assert "/admin/agent/codex-providers" in template
    assert "/static/agent_health.js" in template
    assert "window.setInterval(refresh, pollMs)" in script
    assert "const pollMs = 5000" in script
    assert "'/admin/agent/health.json'" in script
    assert "'/admin/agent/health/check'" in script
    assert '@router.get("/health.json"' in routes
    assert '@router.post("/health/check"' in routes
    assert "error_type=type(exc).__name__" in routes
    assert '"error": str(exc)' not in routes


def test_phase737_monitor_source_never_serializes_provider_secrets() -> None:
    source = Path(health.__file__).read_text(encoding="utf-8")
    assert '"api_key": spec.api_key' not in source
    assert '"api_key": provider' not in source
    assert "provider.api_key" not in source.split("snapshot =", 1)[-1]
