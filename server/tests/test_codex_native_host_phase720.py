from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.codex_app_server import CodexAppServerClient
from app import codex_engine


def test_native_runtime_resolution_uses_official_bundled_fallback(monkeypatch) -> None:
    monkeypatch.delenv("FDEX_AGENT_CODEX_BIN", raising=False)
    monkeypatch.setattr(codex_engine.shutil, "which", lambda _name: None)

    runtime = codex_engine.resolve_codex_runtime()

    assert Path(runtime.path).is_file()
    assert runtime.source == "bundled"
    assert runtime.version


def test_native_runtime_operator_pin_wins(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\necho 'codex-cli 9.8.7'\n", encoding="utf-8")
    fake.chmod(0o700)
    monkeypatch.setenv("FDEX_AGENT_CODEX_BIN", str(fake))

    runtime = codex_engine.resolve_codex_runtime()

    assert runtime.path == str(fake.resolve())
    assert runtime.source == "configured"
    assert runtime.version == "9.8.7"


def test_official_bundled_app_server_native_jsonrpc_handshake(tmp_path: Path) -> None:
    """Exercise the real OpenAI binary without making any model/API request."""
    from codex_cli_bin import bundled_codex_path

    binary = Path(bundled_codex_path()).resolve()
    codex_home = (tmp_path / "codex-home").resolve()
    codex_home.mkdir(parents=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(codex_home),
        "CODEX_HOME": str(codex_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CI": "true",
    }

    async def exercise() -> None:
        client = CodexAppServerClient(
            (str(binary), "app-server", "--listen", "stdio://"),
            env=env,
            cwd=tmp_path,
            client_version="phase7.20-test",
            request_timeout=15.0,
            experimental_api=True,
        )
        async with client:
            assert isinstance(client.initialize_result, dict)
            # A transport-level API that does not start a model turn. This proves FDEX can
            # issue native methods after the mandatory initialize/initialized handshake.
            loaded = await client.request("thread/loaded/list", {}, timeout=10.0)
            assert loaded is not None

    asyncio.run(exercise())


def test_native_thread_payload_uses_public_app_server_field_names(tmp_path: Path) -> None:
    config = codex_engine._codex_thread_config(tmp_path, allow_network=False)
    payload = {
        "model": "gpt-test",
        "modelProvider": "fdex",
        "cwd": str(tmp_path),
        "approvalPolicy": "never",
        "sandbox": "workspace-write",
        "config": config,
        "developerInstructions": "test",
        "ephemeral": False,
    }

    assert payload["modelProvider"] == "fdex"
    assert payload["approvalPolicy"] == "never"
    assert payload["sandbox"] == "workspace-write"
    assert payload["ephemeral"] is False
    assert config["sandbox_workspace_write"] == {"network_access": False}


def test_native_codex_home_is_owner_scoped(monkeypatch, tmp_path: Path) -> None:
    class Settings:
        fdex_agent_codex_home_root = str(tmp_path / "homes")

    monkeypatch.setattr(codex_engine, "fresh_settings", lambda: Settings())

    first = codex_engine._codex_home("user-A")
    second = codex_engine._codex_home("user-A")
    other = codex_engine._codex_home("user-B")

    assert first == second
    assert first != other
    assert first.parent == other.parent
    assert first.stat().st_mode & 0o077 == 0
