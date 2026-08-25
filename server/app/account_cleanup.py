from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app.agent_projects import agent_project_store
from app.agent_tasks import agent_task_store
from app.config import fresh_settings
from app.memory_erasure import erase_account_memory


def _safe_owner_path(root: Path, user_id: str) -> Path:
    owners = (root / "owners").resolve()
    target = (owners / user_id).resolve()
    if owners not in target.parents or target == owners:
        raise ValueError("invalid FDEX owner path")
    return target


def _validate_user_id(user_id: str) -> str:
    clean = (user_id or "").strip()
    if not clean.startswith("usr_") or len(clean) < 12:
        raise ValueError("invalid FDEX user id")
    return clean


def _purge_agent_resources_only(user_id: str) -> dict[str, int]:
    clean = _validate_user_id(user_id)
    store = agent_project_store()
    store.init()
    with store.db() as conn:
        project_count = int(conn.execute("SELECT COUNT(*) FROM agent_projects WHERE owner_id=?", (clean,)).fetchone()[0])
        connection_count = int(conn.execute("SELECT COUNT(*) FROM github_connections WHERE owner_id=?", (clean,)).fetchone()[0])
        conn.execute("DELETE FROM agent_projects WHERE owner_id=?", (clean,))
        conn.execute("DELETE FROM github_connections WHERE owner_id=?", (clean,))

    task_count = agent_task_store().delete_owner(clean)
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
        "agent_tasks": task_count,
        "owner_directories": removed_dirs,
    }


def purge_owned_agent_resources(user_id: str) -> dict[str, object]:
    """Delete every server-owned resource for one FDEX user before identity removal.

    An account cannot be deleted while a queued/running Coding Agent request can still write
    task/worktree state. This guard runs before remote-memory erasure so a rejected deletion
    attempt does not partially erase the user's data. Once no task is active, memory erasure
    remains fail-closed and durable Agent task/event rows are removed with the other resources.
    """
    clean = _validate_user_id(user_id)
    if agent_task_store().active_count(clean):
        raise ValueError("请先停止当前账号正在等待或执行中的 Coding Agent 任务，再注销账号")
    memory_cleanup = asyncio.run(erase_account_memory(clean))
    agent_cleanup = _purge_agent_resources_only(clean)
    return {
        **agent_cleanup,
        "memory": memory_cleanup,
    }
