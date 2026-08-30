from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import codex_provider_compatibility as compatibility
from app import codex_provider_rollout as rollout
from app import codex_provider_smoke_mcp as smoke_mcp


def _provider(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 7,
        "name": "Primary Responses",
        "base_url": "https://provider.example/v1",
        "api_key": "fdex-phase733-secret-key",
        "enabled": True,
        "priority": 1,
        "protocol_order": ["responses", "chat"],
        "main_text_model": "gpt-5.6-sol",
        "backup_text_models": [],
        "timeout_seconds": 60,
    }
    value.update(overrides)
    return value


def _runtime(**overrides: object) -> SimpleNamespace:
    value = {
        "path": "/opt/fdex/codex/0.147.0/codex",
        "version": "0.147.0",
        "source": "managed",
    }
    value.update(overrides)
    return SimpleNamespace(**value)


def test_provider_fingerprint_rotates_on_secret_model_runtime_and_governance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import codex_subagent_governance as governance

    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("features.multi_agent_v2=true",))
    base = compatibility.provider_runtime_fingerprint(_provider(), _runtime())
    assert base != compatibility.provider_runtime_fingerprint(_provider(api_key="rotated-key"), _runtime())
    assert base != compatibility.provider_runtime_fingerprint(_provider(main_text_model="gpt-5.6-sol-2"), _runtime())
    assert base != compatibility.provider_runtime_fingerprint(_provider(base_url="https://other.example/v1"), _runtime())
    assert base != compatibility.provider_runtime_fingerprint(_provider(), _runtime(version="0.148.0"))
    assert base != compatibility.provider_runtime_fingerprint(_provider(), _runtime(path="/opt/fdex/codex/0.148.0/codex"))
    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("features.multi_agent_v2=false",))
    assert base != compatibility.provider_runtime_fingerprint(_provider(), _runtime())


def test_full_compatibility_requires_matching_fingerprint_freshness_and_no_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import codex_subagent_governance as governance

    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("governance=v1",))
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    provider = _provider()
    runtime = _runtime()
    fingerprint = compatibility.provider_runtime_fingerprint(provider, runtime)
    store.record(
        7,
        fingerprint=fingerprint,
        level="full",
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model=str(provider["main_text_model"]),
        base_url=str(provider["base_url"]),
        latency_ms=321,
        evidence={"wire": True, "tools": True, "mcp": True, "subagent": True, "reasoning": True},
    )
    assert store.evaluate(provider, runtime)["valid"] is True

    changed = dict(provider)
    changed["api_key"] = "rotated-key"
    invalid = store.evaluate(changed, runtime)
    assert invalid["valid"] is False
    assert "已变化" in invalid["reason"]

    with store.db() as conn:
        old = (datetime.now(UTC) - timedelta(hours=200)).isoformat(timespec="seconds")
        conn.execute("UPDATE compatibility SET checked_at=? WHERE provider_id=7", (old,))
    expired = store.evaluate(provider, runtime)
    assert expired["valid"] is False
    assert "超过" in expired["reason"]

    store.record(
        7,
        fingerprint=fingerprint,
        level="tools",
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model=str(provider["main_text_model"]),
        base_url=str(provider["base_url"]),
        latency_ms=1,
        evidence={"wire": True, "tools": True},
    )
    partial = store.evaluate(provider, runtime)
    assert partial["valid"] is False
    assert partial["level"] == "tools"

    store.record(
        7,
        fingerprint=fingerprint,
        level="full",
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model=str(provider["main_text_model"]),
        base_url=str(provider["base_url"]),
        latency_ms=1,
        evidence={},
        error="upstream failed after sub-agent stage",
    )
    failed = store.evaluate(provider, runtime)
    assert failed["valid"] is False
    assert "未完整通过" in failed["reason"]


def test_compatibility_database_never_stores_plain_provider_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import codex_subagent_governance as governance

    monkeypatch.setattr(governance, "codex_subagent_cli_overrides", lambda: ("governance=v1",))
    provider = _provider()
    runtime = _runtime()
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    store.record(
        7,
        fingerprint=compatibility.provider_runtime_fingerprint(provider, runtime),
        level="wire",
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model=str(provider["main_text_model"]),
        base_url=str(provider["base_url"]),
        latency_ms=10,
        evidence={"wire": True},
    )
    assert str(provider["api_key"]).encode("utf-8") not in store.path.read_bytes()


def test_loopback_smoke_capability_is_hashed_scoped_and_records_exact_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = compatibility.CodexProviderCompatibilityStore(tmp_path / "compat.db")
    monkeypatch.setattr(smoke_mcp, "codex_provider_compatibility_store", lambda: store)
    token = store.issue_smoke_capability("MARKER-733")
    assert token.encode("utf-8") not in store.path.read_bytes()

    initialized = smoke_mcp._handle_one(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized and initialized["result"]["serverInfo"]["name"] == "fdex-codex-provider-smoke"
    tools = smoke_mcp._handle_one(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools and tools["result"]["tools"][0]["name"] == "fdex_smoke_echo"

    wrong = smoke_mcp._handle_one(
        token,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "fdex_smoke_echo", "arguments": {"marker": "WRONG"}},
        },
    )
    assert wrong and wrong["result"]["isError"] is True
    assert store.smoke_capability(token)["call_count"] == 0

    correct = smoke_mcp._handle_one(
        token,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "fdex_smoke_echo", "arguments": {"marker": "MARKER-733"}},
        },
    )
    assert correct and correct["result"]["isError"] is False
    state = store.smoke_capability(token)
    assert state and state["call_count"] == 1 and state["last_argument"] == "MARKER-733"
    store.revoke_smoke_capability(token)
    assert store.smoke_capability(token) is None


def test_rollout_skips_unverified_high_priority_provider_before_host_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = [
        _provider(id=1, name="High stale", priority=1),
        _provider(id=2, name="Lower full", priority=2, api_key="second-key"),
    ]

    class FakeProviderStore:
        def list(self, *, enabled_only=False, include_secret=False):
            assert enabled_only is True and include_secret is True
            return providers

    class FakeCompatibility:
        def evaluate(self, provider, runtime, *, required_level, max_age_hours):
            assert required_level == "full"
            if int(provider["id"]) == 1:
                return {"valid": False, "level": "tools", "reason": "stale", "age_hours": 200.0}
            return {"valid": True, "level": "full", "reason": "verified", "age_hours": 1.0}

    monkeypatch.setattr(rollout, "provider_store", lambda: FakeProviderStore())
    monkeypatch.setattr(rollout, "codex_provider_compatibility_store", lambda: FakeCompatibility())
    result = rollout.rollout_selection(_runtime())
    selected = result["provider"]
    assert selected is not None and selected.provider_id == 2
    assert [item["provider_id"] for item in result["diagnostics"]] == [1, 2]
    assert result["diagnostics"][0]["eligible"] is False
    assert result["diagnostics"][1]["eligible"] is True


def test_no_full_provider_means_codex_not_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProviderStore:
        def list(self, *, enabled_only=False, include_secret=False):
            return [_provider()]

    class FakeCompatibility:
        def evaluate(self, *args, **kwargs):
            return {"valid": False, "level": "wire", "reason": "only wire"}

    monkeypatch.setattr(rollout, "provider_store", lambda: FakeProviderStore())
    monkeypatch.setattr(rollout, "codex_provider_compatibility_store", lambda: FakeCompatibility())
    assert rollout.rollout_selection(_runtime())["provider"] is None


def test_phase733_smoke_and_failover_are_evidence_based_and_pre_start_only() -> None:
    root = Path(__file__).parents[1] / "app"
    smoke = (root / "codex_provider_smoke.py").read_text(encoding="utf-8")
    rollout_source = (root / "codex_provider_rollout.py").read_text(encoding="utf-8")
    agent_loop = (root / "agent_loop.py").read_text(encoding="utf-8")

    for item_type in ("reasoning", "commandExecution", "fileChange", "mcpToolCall", "collabAgentToolCall"):
        assert item_type in smoke
    assert '"spawnAgent"' in smoke
    assert "smoke_capability" in smoke and "call_count" in smoke and "last_argument" in smoke
    assert "fdex_codex_provider_smoke.txt" in smoke
    assert "never commits, pushes, creates a PR" in smoke

    assert "before a user Host starts" in rollout_source
    assert "never switches Providers inside" in rollout_source
    assert "fresh full" in rollout_source

    # auto may fall back only before run_codex_task is entered. There is no catch around a started
    # Codex Host that resumes the legacy loop on the same worktree.
    helper = agent_loop.split("async def _maybe_run_official_codex", 1)[1].split("class FdexAgentLoop", 1)[0]
    assert "if mode == \"auto\"" in helper
    assert "await run_codex_task(runtime, task_id)" in helper
    assert "except" not in helper


def test_phase733_admin_and_main_wiring_never_render_plain_api_key() -> None:
    root = Path(__file__).parents[1] / "app"
    main = (root / "main.py").read_text(encoding="utf-8")
    routes = (root / "codex_provider_admin_routes.py").read_text(encoding="utf-8")
    template = (root / "templates" / "codex_provider_rollout.html").read_text(encoding="utf-8")
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")

    assert "install_codex_provider_rollout_runtime()" in main
    assert "codex_provider_smoke_mcp_router" in main
    assert "codex_provider_admin_router" in main
    assert "127.0.0.1" in (root / "codex_provider_smoke_mcp.py").read_text(encoding="utf-8")
    assert "codex_process_isolation_status" in routes
    assert "可能产生模型费用" in template
    assert "api_key_masked" in template
    assert "provider.api_key" not in template
    assert "/admin/agent/codex-providers" in base
