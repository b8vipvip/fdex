from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app import codex_runtime_manager as manager
from app.codex_runtime_fence import runtime_switch_fence


def upgrade_runtime_safely(tag: str | None = None) -> dict[str, Any]:
    """Download/verify outside the fence, then serialize the destructive activation boundary."""
    release = manager.fetch_release(tag)
    installed = manager.install_release(release)
    binary = Path(str(installed["path"]))
    current = {
        "tag": str(installed.get("tag") or release["tag"]),
        "version": str(installed["version"]),
        "path": str(binary),
        "binary_sha256": str(installed["binary_sha256"]),
    }
    with runtime_switch_fence():
        return manager._activate_pin(str(binary), current, action="upgrade")


def _fallback_validation() -> dict[str, str]:
    """Match codex_engine resolution precedence when the configured pin is empty."""
    system_codex = shutil.which("codex")
    if system_codex:
        return manager.validate_runtime_binary(Path(system_codex).resolve())
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError as exc:
        raise manager.CodexRuntimeManagerError(
            "cannot rollback to fallback: no system or bundled official Codex Runtime is available"
        ) from exc
    return manager.validate_runtime_binary(Path(bundled_codex_path()).resolve())


def rollback_runtime_safely() -> dict[str, Any]:
    """Serialize target validation + tree cleanup + pin restoration against new Host execs."""
    with runtime_switch_fence():
        state = manager._state()
        if "previous_pin" not in state:
            raise manager.CodexRuntimeManagerError(
                "no previous Codex Runtime pin is available for rollback"
            )
        previous = str(state.get("previous_pin") or "").strip()
        validation = manager._validate_pin(previous) if previous else _fallback_validation()
        values = manager.read_env()
        current_pin = str(values.get("FDEX_AGENT_CODEX_BIN") or "").strip()
        target = {
            "tag": "",
            "version": str(validation.get("version") or "fallback"),
            "path": previous,
            "binary_sha256": (
                manager._sha256_file(Path(str(validation["path"])))
                if validation.get("path")
                else ""
            ),
        }
        result = manager._activate_pin(previous, target, action="rollback")
        # Keep rollback reversible: _activate_pin records the old current pin, but preserve it
        # explicitly if a future state-format change stops doing so.
        after_state = manager._state()
        if str(after_state.get("previous_pin") or "") != current_pin:
            after_state["previous_pin"] = current_pin
            manager._atomic_json(manager._STATE, after_state)
        return result
