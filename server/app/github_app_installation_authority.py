from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from app.agent_projects import DB_PATH, KEY_PATH, _safe_scope
from app.config import fresh_settings
from app.github_app import GitHubAppClient, GitHubAppError
from app.github_app_agent_projects import GitHubAppAgentProjectStore


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    # Microseconds are intentional: consecutive permission syncs can happen within one second.
    # A second-resolution marker could leave a just-removed repository falsely enabled.
    return _now_dt().isoformat(timespec="microseconds")


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class InstallationAuthorityProjectStore(GitHubAppAgentProjectStore):
    """Treat GitHub App Installation as the only repository/GitHub permission authority.

    `agent_projects` remains only a stable internal workspace/task-history index. Repository scope
    comes from the GitHub installation. Push/PR rights come from the GitHub App permissions.
    Network/memory/CPU are FDEX account-level runtime policy. Actual GitHub operations still mint
    short-lived tokens restricted to the current repository and requested operation.
    """

    SYNC_TTL_SECONDS = 90
    MAX_SYNC_PAGES = 100

    def init(self) -> None:
        super().init()
        with self.db() as conn:
            existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(agent_projects)").fetchall()}
            additions = {
                "managed_by_installation": "INTEGER NOT NULL DEFAULT 0",
                "source_installation_id": "TEXT NOT NULL DEFAULT ''",
                "last_seen_installation_at": "TEXT NOT NULL DEFAULT ''",
            }
            for name, ddl in additions.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE agent_projects ADD COLUMN {name} {ddl}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_account_policies (
                    owner_id TEXT PRIMARY KEY,
                    allow_network INTEGER NOT NULL DEFAULT 0,
                    sandbox_memory_mb INTEGER NOT NULL DEFAULT 2048,
                    sandbox_cpu_percent INTEGER NOT NULL DEFAULT 150,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS github_installation_project_sync (
                    owner_id TEXT PRIMARY KEY,
                    last_synced_at TEXT NOT NULL DEFAULT '',
                    repository_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            # Migrate Phase 7.7 GitHub-App-backed manual projects into internal workspace rows.
            conn.execute(
                """UPDATE agent_projects
                   SET managed_by_installation=1,
                       source_installation_id=COALESCE((
                           SELECT github_app_installation_id FROM github_connections c
                           WHERE c.id=agent_projects.connection_id
                             AND c.owner_id=agent_projects.owner_id
                       ), source_installation_id)
                   WHERE connection_id IN (
                       SELECT id FROM github_connections WHERE auth_type='github_app'
                   )"""
            )

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, Any]:
        data = GitHubAppAgentProjectStore._project_row(row)
        data["managed_by_installation"] = bool(data.get("managed_by_installation", 0))
        return data

    def account_policy(self, owner_id: str) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        settings = fresh_settings()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM agent_account_policies WHERE owner_id=?", (owner_id,)).fetchone()
            if row is None:
                now = _now()
                conn.execute(
                    """INSERT INTO agent_account_policies(
                           owner_id,allow_network,sandbox_memory_mb,sandbox_cpu_percent,updated_at
                       ) VALUES(?,?,?,?,?)""",
                    (
                        owner_id,
                        0,
                        int(settings.fdex_agent_sandbox_memory_mb),
                        int(settings.fdex_agent_sandbox_cpu_percent),
                        now,
                    ),
                )
                row = conn.execute("SELECT * FROM agent_account_policies WHERE owner_id=?", (owner_id,)).fetchone()
        assert row is not None
        data = dict(row)
        data["allow_network"] = bool(data.get("allow_network", 0))
        return data

    def save_account_policy(
        self,
        owner_id: str,
        *,
        allow_network: bool,
        sandbox_memory_mb: int,
        sandbox_cpu_percent: int,
    ) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        memory_mb = max(128, min(16384, int(sandbox_memory_mb or 2048)))
        cpu_percent = max(10, min(800, int(sandbox_cpu_percent or 150)))
        now = _now()
        with self.owner_db(owner_id, "agent_account_policy_write") as conn:
            conn.execute(
                """INSERT INTO agent_account_policies(
                       owner_id,allow_network,sandbox_memory_mb,sandbox_cpu_percent,updated_at
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       allow_network=excluded.allow_network,
                       sandbox_memory_mb=excluded.sandbox_memory_mb,
                       sandbox_cpu_percent=excluded.sandbox_cpu_percent,
                       updated_at=excluded.updated_at""",
                (owner_id, int(bool(allow_network)), memory_mb, cpu_percent, now),
            )
            conn.execute(
                """UPDATE agent_projects
                   SET allow_network=?,sandbox_memory_mb=?,sandbox_cpu_percent=?,updated_at=?
                   WHERE owner_id=? AND managed_by_installation=1""",
                (int(bool(allow_network)), memory_mb, cpu_percent, now, owner_id),
            )
        return self.account_policy(owner_id)

    def sync_status(self, owner_id: str) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM github_installation_project_sync WHERE owner_id=?", (owner_id,)
            ).fetchone()
        if row is None:
            return {"last_synced_at": "", "repository_count": 0, "last_error": ""}
        return dict(row)

    def sync_owner_installations(
        self,
        owner_id: str,
        *,
        force: bool = False,
        strict: bool = False,
    ) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        status = self.sync_status(owner_id)
        last = _parse_time(str(status.get("last_synced_at") or ""))
        if not force and last and last > _now_dt() - timedelta(seconds=self.SYNC_TTL_SECONDS):
            return status

        connections = [
            item
            for item in self.list_connections(owner_id)
            if str(item.get("auth_type") or "") == "github_app" and not bool(item.get("needs_reconnect"))
        ]
        if not connections:
            self._save_sync_status(owner_id, repository_count=0, error="")
            return self.sync_status(owner_id)

        total = 0
        errors: list[str] = []
        for connection in connections:
            try:
                total += self.sync_connection(owner_id, int(connection["id"]), update_owner_status=False)
            except (KeyError, ValueError, RuntimeError) as exc:
                errors.append(f"{connection.get('login') or connection.get('name')}: {exc}")
                if strict:
                    self._save_sync_status(owner_id, repository_count=total, error="; ".join(errors)[:1000])
                    raise
        self._save_sync_status(owner_id, repository_count=total, error="; ".join(errors)[:1000])
        return self.sync_status(owner_id)

    def sync_connection(self, owner_id: str, connection_id: int, *, update_owner_status: bool = True) -> int:
        self.init()
        owner_id = _safe_scope(owner_id)
        connection = self.get_connection(owner_id, int(connection_id))
        if str(connection.get("auth_type") or "") != "github_app":
            return 0
        installation_id = str(connection.get("github_app_installation_id") or "").strip()
        if not installation_id.isdigit():
            raise ValueError("GitHub App installation is invalid")

        policy = self.account_policy(owner_id)
        seen_at = _now()
        repositories: list[dict[str, Any]] = []
        for page in range(1, self.MAX_SYNC_PAGES + 1):
            batch = self.list_repositories(owner_id, int(connection_id), page=page, per_page=100, query="")
            repositories.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError("GitHub App 仓库数量超过同步上限，请联系平台管理员")

        with self.owner_db(owner_id, "github_installation_project_sync") as conn:
            for repository in repositories:
                full_name = str(repository.get("full_name") or "").strip()
                if not full_name:
                    continue
                archived = bool(repository.get("archived"))
                can_push = bool(repository.get("can_push")) and not archived
                can_pr = bool(repository.get("can_pr")) and can_push
                name = str(repository.get("name") or full_name.rsplit("/", 1)[-1]).strip()[:100]
                base_branch = str(repository.get("default_branch") or "main").strip()
                conn.execute(
                    """INSERT INTO agent_projects(
                           owner_id,name,repo_full_name,base_branch,connection_id,enabled,
                           allow_push,allow_pr,allow_network,sandbox_memory_mb,sandbox_cpu_percent,
                           managed_by_installation,source_installation_id,last_seen_installation_at,
                           created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(owner_id,repo_full_name) DO UPDATE SET
                           name=excluded.name,
                           base_branch=excluded.base_branch,
                           connection_id=excluded.connection_id,
                           enabled=excluded.enabled,
                           allow_push=excluded.allow_push,
                           allow_pr=excluded.allow_pr,
                           allow_network=excluded.allow_network,
                           sandbox_memory_mb=excluded.sandbox_memory_mb,
                           sandbox_cpu_percent=excluded.sandbox_cpu_percent,
                           managed_by_installation=1,
                           source_installation_id=excluded.source_installation_id,
                           last_seen_installation_at=excluded.last_seen_installation_at,
                           updated_at=excluded.updated_at""",
                    (
                        owner_id,
                        name,
                        full_name,
                        base_branch,
                        int(connection_id),
                        int(not archived),
                        int(can_push),
                        int(can_pr),
                        int(bool(policy["allow_network"])),
                        int(policy["sandbox_memory_mb"]),
                        int(policy["sandbox_cpu_percent"]),
                        1,
                        installation_id,
                        seen_at,
                        seen_at,
                        seen_at,
                    ),
                )
            conn.execute(
                """UPDATE agent_projects
                   SET enabled=0,allow_push=0,allow_pr=0,updated_at=?
                   WHERE owner_id=? AND connection_id=? AND managed_by_installation=1
                     AND last_seen_installation_at<>?""",
                (seen_at, owner_id, int(connection_id), seen_at),
            )
        if update_owner_status:
            self._save_sync_status(owner_id, repository_count=len(repositories), error="")
        return len(repositories)

    def _save_sync_status(self, owner_id: str, *, repository_count: int, error: str) -> None:
        with self.db() as conn:
            conn.execute(
                """INSERT INTO github_installation_project_sync(owner_id,last_synced_at,repository_count,last_error)
                   VALUES(?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       last_synced_at=excluded.last_synced_at,
                       repository_count=excluded.repository_count,
                       last_error=excluded.last_error""",
                (owner_id, _now(), max(0, int(repository_count)), (error or "")[:1000]),
            )

    def list_projects(self, owner_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        self.init()
        owner_id = _safe_scope(owner_id)
        try:
            self.sync_owner_installations(owner_id, force=False, strict=False)
        except (KeyError, ValueError, RuntimeError):
            # Keep the last known safe local cache during transient GitHub transport failure.
            pass
        sql = (
            "SELECT * FROM agent_projects WHERE owner_id=?"
            + (" AND enabled=1" if enabled_only else "")
            + " ORDER BY name,id"
        )
        with self.db() as conn:
            rows = conn.execute(sql, (owner_id,)).fetchall()
        return [self._project_row(row) for row in rows]

    def save_project(
        self,
        owner_id: str,
        *,
        name: str,
        repo_full_name: str,
        base_branch: str = "main",
        connection_id: int | None = None,
        allow_push: bool = False,
        allow_pr: bool = False,
        allow_network: bool = False,
        sandbox_memory_mb: int = 2048,
        sandbox_cpu_percent: int = 150,
        enabled: bool = True,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        if connection_id is None:
            return super().save_project(
                owner_id,
                name=name,
                repo_full_name=repo_full_name,
                base_branch=base_branch,
                connection_id=connection_id,
                allow_push=allow_push,
                allow_pr=allow_pr,
                allow_network=allow_network,
                sandbox_memory_mb=sandbox_memory_mb,
                sandbox_cpu_percent=sandbox_cpu_percent,
                enabled=enabled,
                project_id=project_id,
            )
        connection = self.get_connection(owner_id, int(connection_id))
        if str(connection.get("auth_type") or "") != "github_app":
            return super().save_project(
                owner_id,
                name=name,
                repo_full_name=repo_full_name,
                base_branch=base_branch,
                connection_id=connection_id,
                allow_push=allow_push,
                allow_pr=allow_pr,
                allow_network=allow_network,
                sandbox_memory_mb=sandbox_memory_mb,
                sandbox_cpu_percent=sandbox_cpu_percent,
                enabled=enabled,
                project_id=project_id,
            )

        # Compatibility API calls can still name a repository, but their per-repository flags are
        # intentionally ignored. Installation permissions + account runtime policy are authority.
        self.sync_connection(owner_id, int(connection_id))
        clean_repo = repo_full_name.strip().removesuffix(".git")
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM agent_projects WHERE owner_id=? AND repo_full_name=? AND connection_id=?",
                (_safe_scope(owner_id), clean_repo, int(connection_id)),
            ).fetchone()
        if row is None:
            raise ValueError("该仓库不在当前 GitHub App 安装授权范围内")
        return self._project_row(row)

    def delete_project(self, owner_id: str, project_id: int) -> None:
        project = self.get_project(owner_id, project_id)
        if bool(project.get("managed_by_installation")):
            raise ValueError("GitHub App 仓库由安装范围自动管理；请在 GitHub 修改 FDEX App 的仓库范围")
        super().delete_project(owner_id, project_id)

    def delete_connection(self, owner_id: str, connection_id: int) -> None:
        owner_id = _safe_scope(owner_id)
        connection = self.get_connection(owner_id, connection_id)
        if str(connection.get("auth_type") or "") != "github_app":
            return super().delete_connection(owner_id, connection_id)

        from app.agent_tasks import agent_task_store

        installation_id = str(connection.get("github_app_installation_id") or "")
        if not installation_id.isdigit():
            raise ValueError("GitHub App installation is invalid")

        project_ids: list[int] = []
        # Hold the owner mutation fence across active-task verification, remote revocation and the
        # local delete. A new task cannot start in the revoke/delete gap.
        with self.owner_db(owner_id, "github_app_disconnect") as conn:
            if agent_task_store().active_count(owner_id):
                raise ValueError("请先停止当前账号正在等待或执行中的 Coding Agent 任务，再卸载 GitHub App")
            project_rows = conn.execute(
                "SELECT id FROM agent_projects WHERE owner_id=? AND connection_id=?",
                (owner_id, int(connection_id)),
            ).fetchall()
            project_ids = [int(row[0]) for row in project_rows]
            try:
                GitHubAppClient().delete_installation(int(installation_id))
            except GitHubAppError as exc:
                raise ValueError(str(exc)) from exc
            conn.execute("DELETE FROM agent_projects WHERE owner_id=? AND connection_id=?", (owner_id, int(connection_id)))
            conn.execute(
                "UPDATE github_device_flows SET connection_id=NULL WHERE owner_id=? AND connection_id=?",
                (owner_id, int(connection_id)),
            )
            conn.execute("DELETE FROM github_connections WHERE id=? AND owner_id=?", (int(connection_id), owner_id))

        owner_root = self.owner_root(owner_id)
        for project_id in project_ids:
            target = (owner_root / "projects" / str(project_id)).resolve()
            if owner_root in target.parents and target.exists():
                shutil.rmtree(target, ignore_errors=True)
        self._save_sync_status(owner_id, repository_count=0, error="")


@lru_cache(maxsize=1)
def agent_project_store() -> InstallationAuthorityProjectStore:
    store = InstallationAuthorityProjectStore(DB_PATH, KEY_PATH)
    store.init()
    return store
