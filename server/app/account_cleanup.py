from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

from app.agent_projects import agent_project_store
from app.agent_tasks import agent_task_store
from app.codex_host_store import codex_host_store
from app.codex_interaction_store import codex_interaction_store
from app.codex_item_store import codex_item_store
from app.codex_retry_data_lifecycle import delete_owner_retry_task_graph
from app.codex_task_inputs import codex_task_input_store
from app.config import fresh_settings
from app.github_app import GitHubAppClient, GitHubAppError
from app.github_app_flow import GitHubAppInstallationFlowStore
from app.github_web_oauth import GitHubWebOAuthStore
from app.memory_erasure import erase_account_memory
from app.remote_mcp_credentials import remote_mcp_credential_store
from app.remote_mcp_gateway import remote_mcp_lease_store
from app.remote_mcp_registry import remote_mcp_registry
from app.web_workspace import web_workspace_store


def _safe_owner_path(root: Path, user_id: str) -> Path:
    owners = (root / "owners").resolve()
    target = (owners / user_id).resolve()
    if owners not in target.parents or target == owners:
        raise ValueError("invalid FDEX owner path")
    return target


def _safe_direct_owner_path(root: Path, user_id: str) -> Path:
    """Resolve an owner directory stored directly below a configured root."""
    base = root.resolve()
    target = (base / user_id).resolve()
    if base not in target.parents or target == base:
        raise ValueError("invalid FDEX direct owner path")
    return target


def _validate_user_id(user_id: str) -> str:
    clean = (user_id or "").strip()
    if not clean.startswith("usr_") or len(clean) < 12:
        raise ValueError("invalid FDEX user id")
    return clean


def _purge_web_workspace(user_id: str) -> dict[str, int]:
    clean = _validate_user_id(user_id)
    settings = fresh_settings()
    removed_records = web_workspace_store().clear_owner(clean)
    owner_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]
    assets_root = (Path(settings.app_dir).expanduser().resolve() / "server" / "data" / "web-assets").resolve()
    target = (assets_root / owner_hash).resolve()
    if assets_root not in target.parents or target == assets_root:
        raise ValueError("invalid FDEX Web asset owner path")
    removed_asset_dir = 0
    if target.exists():
        shutil.rmtree(target)
        removed_asset_dir = 1
    return {"records": removed_records, "asset_directories": removed_asset_dir}


def _purge_agent_resources_only(user_id: str) -> dict[str, object]:
    clean = _validate_user_id(user_id)
    store = agent_project_store()
    store.init()

    # A GitHub App installation remains repository authority even when FDEX forgets its id.
    # Revoke that remote authority before deleting the local installation metadata. This is
    # fail-closed: a non-404 GitHub failure keeps the FDEX account/connection record so cleanup
    # can be retried instead of silently orphaning repository access.
    with store.db() as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(github_connections)").fetchall()}
        installation_ids: list[int] = []
        if "github_app_installation_id" in columns:
            rows = conn.execute(
                """SELECT github_app_installation_id FROM github_connections
                   WHERE owner_id=? AND auth_type='github_app' AND github_app_installation_id<>''""",
                (clean,),
            ).fetchall()
            installation_ids = sorted({int(str(row[0])) for row in rows if str(row[0]).isdigit()})
    if installation_ids:
        client = GitHubAppClient()
        for installation_id in installation_ids:
            try:
                client.delete_installation(installation_id)
            except GitHubAppError as exc:
                raise RuntimeError(f"GitHub App 安装撤销失败：{exc}") from exc

    with store.db() as conn:
        project_count = int(conn.execute("SELECT COUNT(*) FROM agent_projects WHERE owner_id=?", (clean,)).fetchone()[0])
        connection_count = int(conn.execute("SELECT COUNT(*) FROM github_connections WHERE owner_id=?", (clean,)).fetchone()[0])
        device_flow_count = int(conn.execute("SELECT COUNT(*) FROM github_device_flows WHERE owner_id=?", (clean,)).fetchone()[0])
        policy_count = 0
        sync_state_count = 0
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "agent_account_policies" in tables:
            policy_count = int(conn.execute("SELECT COUNT(*) FROM agent_account_policies WHERE owner_id=?", (clean,)).fetchone()[0])
            conn.execute("DELETE FROM agent_account_policies WHERE owner_id=?", (clean,))
        if "github_installation_project_sync" in tables:
            sync_state_count = int(conn.execute("SELECT COUNT(*) FROM github_installation_project_sync WHERE owner_id=?", (clean,)).fetchone()[0])
            conn.execute("DELETE FROM github_installation_project_sync WHERE owner_id=?", (clean,))
        conn.execute("DELETE FROM agent_projects WHERE owner_id=?", (clean,))
        conn.execute("DELETE FROM github_device_flows WHERE owner_id=?", (clean,))
        conn.execute("DELETE FROM github_connections WHERE owner_id=?", (clean,))

    web_oauth_flow_count = GitHubWebOAuthStore(project_store=store).delete_owner(clean)
    github_app_flow_count = GitHubAppInstallationFlowStore(project_store=store).delete_owner(clean)
    # Invalidate localhost capabilities first, then destroy encrypted Remote MCP secrets, then
    # remove the public registry. This ordering guarantees no still-valid lease can outlive the
    # credential it was authorized against and no orphan secret survives registry deletion.
    remote_mcp_lease_count = remote_mcp_lease_store().delete_owner(clean)
    remote_mcp_credential_count = remote_mcp_credential_store().delete_owner(clean)
    remote_mcp_count = remote_mcp_registry().delete_owner(clean)
    # Interactive answers may contain secrets. Remove their encrypted short-lived bridge rows
    # before Item/Thread metadata so no orphaned approval or requestUserInput material survives
    # account deletion. Item/Event rows then erase transcript/command-output projections.
    codex_interaction_cleanup = codex_interaction_store().delete_owner(clean)
    codex_item_cleanup = codex_item_store().delete_owner(clean)
    codex_cleanup = codex_host_store().delete_owner(clean)
    # Phase 7.29 media lives outside task worktrees. Erase its metadata and generated owner-scoped
    # files before deleting the durable task records so no attachment can become an identity orphan.
    codex_input_cleanup = codex_task_input_store().delete_owner(clean)
    # Phase 7.41 retry-transition rows share the Agent task SQLite database. Erase transition,
    # attempt and task projections in one BEGIN IMMEDIATE transaction so account deletion cannot
    # leave retry policy/error/provider metadata behind after the owner task rows disappear.
    retry_task_cleanup = delete_owner_retry_task_graph(clean)
    settings = fresh_settings()
    removed_dirs = 0
    for configured_root in (settings.fdex_agent_sandbox_root, settings.fdex_agent_worktree_root):
        root = Path(configured_root).expanduser().resolve()
        target = _safe_owner_path(root, clean)
        if target.exists():
            shutil.rmtree(target)
            removed_dirs += 1

    # CODEX_HOME may contain official Runtime rollout/state files for this owner. Account erasure
    # removes that filesystem state after all live task/Host guards have already passed.
    codex_home_root = Path(settings.fdex_agent_codex_home_root).expanduser().resolve()
    codex_home_target = _safe_direct_owner_path(codex_home_root, clean)
    codex_home_removed = 0
    if codex_home_target.exists():
        shutil.rmtree(codex_home_target)
        codex_home_removed = 1

    return {
        "projects": project_count,
        "github_connections": connection_count,
        "github_device_flows": device_flow_count,
        "github_web_oauth_flows": web_oauth_flow_count,
        "github_app_flows": github_app_flow_count,
        "github_app_installations_revoked": len(installation_ids),
        "agent_account_policies": policy_count,
        "github_installation_sync_states": sync_state_count,
        "remote_mcp_leases": remote_mcp_lease_count,
        "remote_mcp_credentials": remote_mcp_credential_count,
        "remote_mcp_servers": remote_mcp_count,
        "agent_tasks": retry_task_cleanup["agent_tasks"],
        "codex_retry_attempts": retry_task_cleanup["codex_retry_attempts"],
        "codex_retry_transitions": retry_task_cleanup["codex_retry_transitions"],
        "codex_interactions": codex_interaction_cleanup,
        "codex_items": codex_item_cleanup,
        "codex_host": codex_cleanup,
        "codex_task_inputs": codex_input_cleanup,
        "owner_directories": removed_dirs,
        "codex_home_directories": codex_home_removed,
    }


def purge_owned_agent_resources(user_id: str) -> dict[str, object]:
    """Delete every server-owned resource for one FDEX user before identity removal."""
    clean = _validate_user_id(user_id)
    if agent_task_store().active_count(clean):
        raise ValueError("请先停止当前账号正在等待或执行中的 Coding Agent 任务，再注销账号")
    if codex_host_store().active_count(clean):
        raise ValueError("请先等待 Codex Host 的 Turn/Compact/控制操作结束，再注销账号")
    interaction_store = codex_interaction_store()
    interaction_store.interrupt_orphans(clean)
    if interaction_store.active_count(clean):
        raise ValueError("请先完成或取消当前 Codex 审批/提问，再注销账号")
    memory_cleanup = asyncio.run(erase_account_memory(clean))
    agent_cleanup = _purge_agent_resources_only(clean)
    web_cleanup = _purge_web_workspace(clean)
    return {
        **agent_cleanup,
        "memory": memory_cleanup,
        "web_workspace": web_cleanup,
    }
