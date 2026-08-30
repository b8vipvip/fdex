from __future__ import annotations

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


def rollback_runtime_safely() -> dict[str, Any]:
    """Serialize validation + tree cleanup + pin restoration against new Host execs."""
    with runtime_switch_fence():
        return manager.rollback_runtime()
