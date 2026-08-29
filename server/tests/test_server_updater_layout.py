from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_canonical_updater_uses_current_server_layout() -> None:
    updater = _text("scripts/update_server.sh")
    assert "${APP_DIR}/server/.venv" in updater
    assert "${APP_DIR}/server/requirements.txt" in updater
    assert "ai-business-assistant" not in updater


def test_legacy_deploy_entry_is_repository_owned_compat_wrapper() -> None:
    wrapper = _text("scripts/deploy_fdex_compat.sh")
    service = _text("deploy/systemd/fdex.service")

    assert 'CANONICAL_UPDATER="${APP_DIR}/scripts/update_server.sh"' in wrapper
    assert 'exec /bin/bash "${CANONICAL_UPDATER}" "$@"' in wrapper
    assert "ExecStartPre=/usr/bin/install -m 0755 /opt/fdex/scripts/deploy_fdex_compat.sh /opt/deploy_fdex.sh" in service
    assert "ai-business-assistant" not in wrapper


def test_memory_proxy_does_not_install_full_fdex_or_codex_dependencies() -> None:
    compose = _text("docker-compose.memory.yml")
    dockerfile = _text("server/Dockerfile.memory-proxy")
    requirements = _text("server/requirements-memory-proxy.txt").lower()

    assert "dockerfile: Dockerfile.memory-proxy" in compose
    assert "COPY requirements-memory-proxy.txt ." in dockerfile
    assert "requirements.txt" not in dockerfile.replace("requirements-memory-proxy.txt", "")
    assert "openai-codex" not in requirements
    assert "codex" not in requirements
    assert "letta-client" not in requirements
    for package in ("fastapi", "uvicorn", "pydantic-settings", "httpx", "cryptography"):
        assert package in requirements


def test_memory_docker_context_excludes_runtime_state() -> None:
    ignored = _text("server/.dockerignore")
    assert ".venv/" in ignored
    assert "data/" in ignored
    assert ".env" in ignored


def test_memory_setup_is_bounded_and_shell_scripts_parse() -> None:
    setup = _text("scripts/setup_memory_stack.sh")
    assert "FDEX_MEMORY_SETUP_TIMEOUT_SECONDS" in setup
    assert "timeout --signal=TERM --kill-after=30s" in setup
    assert "SETUP_TIMEOUT:-900" in setup

    for relative in ("scripts/setup_memory_stack.sh", "scripts/deploy_fdex_compat.sh", "scripts/update_server.sh"):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / relative)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
