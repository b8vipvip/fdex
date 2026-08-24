from __future__ import annotations

import shutil
from pathlib import Path

from app.agent_projects import agent_project_store
from app.config import fresh_settings


def _safe_owner_path(root: Path, user_id: str) -> Path:
    owners = (root / "owners").resolve()
    target = (owners / user_id).resolve()
    if owners not in target.parents or target == owners:
        raise ValueError("invalid FDEX owner path")
    return target


def purge_owned_agent_resources(user_id: str) -> dict[str, int]:
    """Delete server-owned GitHub/project metadata and filesystem sandboxes for one FDEX user.

    This intentionally runs before the center account row is removed. Failure leaves the
    account intact so the user can retry rather than creating a half-deleted identity.
    """
    clean = (user_id or "").strip()
    if not clean.startswith("usr_") or len(clean) < 12:
        raise ValueError("invalid FDEX user id")

    store = agent_project_store()
    store.init()
    with store.db() as conn:
        project_count = int(conn.execute("SELECT COUNT(*) FROM agent_projects WHERE owner_id=?", (clean,)).fetchone()[0])
        connection_count = int(conn.execute("SELECT COUNT(*) FROM github_connections WHERE owner_id=?", (clean,)).fetchone()[0])
        conn.execute("DELETE FROM agent_projects WHERE owner_id=?", (clean,))
        conn.execute("DELETE FROM github_connections WHERE owner_id=?", (clean,))

    settings = fresh_settings()
    removed_dirs = 0
    for configured_root in (settings.fdex_agent_sandbox_root, settings.fdex_agent_worktree_root):
        root = Path(configured_root).expanduser().resolve()
        target = _safe_owner_path(root, clean)
        if target.exists():
            shutil.rmtree(target)
            removed_dirs += 1

    return {
        "projects": project_count,
        "github_connections": connection_count,
        "owner_directories": removed_dirs,
    }
