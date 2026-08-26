from __future__ import annotations

import base64
import json
import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agent_projects import (
    DB_PATH,
    KEY_PATH,
    AgentProjectStore,
    _safe_branch,
    _safe_repo,
    _safe_scope,
)
from app.github_app import GitHubAppClient, GitHubAppError


class GitHubAppAgentProjectStore(AgentProjectStore):
    """Agent project store with GitHub App installations as the preferred connection type."""

    def init(self) -> None:
        super().init()
        with self.db() as conn:
            existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(github_connections)").fetchall()}
            additions = {
                "github_app_installation_id": "TEXT NOT NULL DEFAULT ''",
                "github_app_account_id": "TEXT NOT NULL DEFAULT ''",
                "github_app_account_type": "TEXT NOT NULL DEFAULT ''",
                "github_app_repository_selection": "TEXT NOT NULL DEFAULT ''",
                "github_app_permissions": "TEXT NOT NULL DEFAULT '{}'",
                "github_app_slug": "TEXT NOT NULL DEFAULT ''",
            }
            for name, ddl in additions.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE github_connections ADD COLUMN {name} {ddl}")
            # One app installation is app-level authority rather than a human-scoped credential.
            # Until FDEX has a shared workspace owner model, keep that authority bound to one
            # canonical FDEX user so it cannot bridge isolated accounts.
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_github_app_installation
                   ON github_connections(github_app_installation_id)
                   WHERE github_app_installation_id<>''"""
            )

    def _connection_row(self, row: sqlite3.Row, *, secret: bool = False) -> dict[str, Any]:
        data = super()._connection_row(row, secret=secret)
        raw_permissions = str(data.pop("github_app_permissions", "{}") or "{}")
        try:
            parsed = json.loads(raw_permissions)
        except ValueError:
            parsed = {}
        data["app_permissions"] = parsed if isinstance(parsed, dict) else {}
        data["is_github_app"] = str(data.get("auth_type") or "") == "github_app"
        if data["is_github_app"]:
            # Installation tokens are intentionally never persisted. token_configured=false is
            # expected and must not be treated as a reconnect failure.
            data["needs_reconnect"] = False
        return data

    def save_github_app_connection(
        self,
        owner_id: str,
        *,
        installer_user_id: str,
        installation: dict[str, Any],
    ) -> dict[str, Any]:
        self.init()
        owner_id = _safe_scope(owner_id)
        installation_id = str(installation.get("id") or "").strip()
        if not installation_id.isdigit():
            raise ValueError("GitHub App installation id is invalid")
        account = installation.get("account") if isinstance(installation.get("account"), dict) else {}
        login = str(account.get("login") or "").strip()
        account_id = str(account.get("id") or "").strip()
        account_type = str(account.get("type") or "").strip()[:40]
        if not login or not account_id:
            raise ValueError("GitHub App installation account is invalid")
        if installation.get("suspended_at"):
            raise ValueError("GitHub App installation is suspended")
        permissions = installation.get("permissions") if isinstance(installation.get("permissions"), dict) else {}
        repository_selection = str(installation.get("repository_selection") or "all").strip()[:20]
        slug = str(installation.get("app_slug") or "").strip()[:100]
        now = self._now()
        with self.owner_db(owner_id, "github_app_installation_write") as conn:
            collision = conn.execute(
                """SELECT id,owner_id FROM github_connections
                   WHERE github_app_installation_id=? ORDER BY id LIMIT 1""",
                (installation_id,),
            ).fetchone()
            if collision is not None and str(collision["owner_id"]) != owner_id:
                raise ValueError("该 GitHub App 安装已经绑定到另一个 FDEX 账号")
            existing = conn.execute(
                """SELECT id FROM github_connections
                   WHERE owner_id=? AND github_app_installation_id=? ORDER BY id LIMIT 1""",
                (owner_id, installation_id),
            ).fetchone()
            values = (
                f"GitHub App · {login}"[:80],
                login,
                (installer_user_id or "").strip(),
                installation_id,
                account_id,
                account_type,
                repository_selection,
                json.dumps(permissions, ensure_ascii=False, separators=(",", ":")),
                slug,
                now,
            )
            if existing is not None:
                cid = int(existing["id"])
                conn.execute(
                    """UPDATE github_connections
                       SET name=?,login=?,github_user_id=?,auth_type='github_app',token_cipher='',
                           refresh_token_cipher='',token_expires_at='',refresh_expires_at='',scope='',
                           oauth_client_id='',needs_reconnect=0,github_app_installation_id=?,
                           github_app_account_id=?,github_app_account_type=?,github_app_repository_selection=?,
                           github_app_permissions=?,github_app_slug=?,updated_at=?
                       WHERE id=? AND owner_id=?""",
                    (*values, cid, owner_id),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO github_connections(
                           owner_id,name,login,github_user_id,auth_type,token_cipher,refresh_token_cipher,
                           token_expires_at,refresh_expires_at,scope,oauth_client_id,needs_reconnect,
                           github_app_installation_id,github_app_account_id,github_app_account_type,
                           github_app_repository_selection,github_app_permissions,github_app_slug,
                           created_at,updated_at
                       ) VALUES(?,?,?,?,'github_app','','','','','','',0,?,?,?,?,?,?,?,?)""",
                    (
                        owner_id,
                        values[0],
                        values[1],
                        values[2],
                        values[3],
                        values[4],
                        values[5],
                        values[6],
                        values[7],
                        values[8],
                        now,
                        now,
                    ),
                )
                cid = int(cur.lastrowid)
        return self.get_connection(owner_id, cid)

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
        if connection_id is not None:
            connection = self.get_connection(owner_id, int(connection_id))
            if str(connection.get("auth_type") or "") == "github_app":
                permissions = connection.get("app_permissions") if isinstance(connection.get("app_permissions"), dict) else {}
                if (allow_push or allow_pr) and str(permissions.get("contents") or "") != "write":
                    raise ValueError("GitHub App 未授予 Contents 写权限，不能启用 Push/PR")
                if allow_pr and str(permissions.get("pull_requests") or "") != "write":
                    raise ValueError("GitHub App 未授予 Pull requests 写权限，不能启用 PR")
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

    def find_github_app_connection(self, owner_id: str, installation_id: int) -> dict[str, Any] | None:
        owner_id = _safe_scope(owner_id)
        needle = str(int(installation_id))
        return next(
            (
                connection
                for connection in self.list_connections(owner_id)
                if str(connection.get("github_app_installation_id") or "") == needle
                and str(connection.get("auth_type") or "") == "github_app"
            ),
            None,
        )

    def connection_token(
        self,
        owner_id: str,
        connection_id: int,
        *,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> str:
        owner_id = _safe_scope(owner_id)
        connection = self.get_connection(owner_id, connection_id)
        if str(connection.get("auth_type") or "") != "github_app":
            return super().connection_token(owner_id, connection_id)
        installation_id = str(connection.get("github_app_installation_id") or "")
        if not installation_id.isdigit():
            raise ValueError("GitHub App installation is invalid; reconnect GitHub")
        try:
            return GitHubAppClient().installation_token(
                int(installation_id),
                repository=repository,
                permissions=permissions,
            )
        except GitHubAppError as exc:
            raise ValueError(str(exc)) from exc

    def list_repositories(
        self,
        owner_id: str,
        connection_id: int,
        *,
        page: int = 1,
        per_page: int = 100,
        query: str = "",
    ) -> list[dict[str, Any]]:
        owner_id = _safe_scope(owner_id)
        connection = self.get_connection(owner_id, connection_id)
        if str(connection.get("auth_type") or "") != "github_app":
            repositories = super().list_repositories(
                owner_id,
                connection_id,
                page=page,
                per_page=per_page,
                query=query,
            )
            for repository in repositories:
                repository["can_pr"] = bool(repository.get("can_push"))
            return repositories

        installation_id = str(connection.get("github_app_installation_id") or "")
        if not installation_id.isdigit():
            raise ValueError("GitHub App installation is invalid")
        page = max(1, min(int(page), 1000))
        per_page = max(1, min(int(per_page), 100))
        try:
            raw_repositories = GitHubAppClient().installation_repositories(
                int(installation_id), page=page, per_page=per_page
            )
        except GitHubAppError as exc:
            raise ValueError(str(exc)) from exc
        needle = (query or "").strip().casefold()[:100]
        app_permissions = connection.get("app_permissions") if isinstance(connection.get("app_permissions"), dict) else {}
        can_write_contents = str(app_permissions.get("contents") or "") == "write"
        can_write_pr = str(app_permissions.get("pull_requests") or "") == "write"
        repositories: list[dict[str, Any]] = []
        for raw in raw_repositories:
            full_name = str(raw.get("full_name") or "").strip()
            try:
                full_name = _safe_repo(full_name)
            except ValueError:
                continue
            description = str(raw.get("description") or "").strip()[:500]
            if needle and needle not in full_name.casefold() and needle not in description.casefold():
                continue
            archived = bool(raw.get("archived"))
            repositories.append(
                {
                    "id": int(raw.get("id") or 0),
                    "name": str(raw.get("name") or full_name.rsplit("/", 1)[-1])[:100],
                    "full_name": full_name,
                    "private": bool(raw.get("private")),
                    "default_branch": _safe_branch(str(raw.get("default_branch") or "main")),
                    "can_push": can_write_contents and not archived,
                    "can_pr": can_write_contents and can_write_pr and not archived,
                    "archived": archived,
                    "description": description,
                    "updated_at": str(raw.get("updated_at") or "")[:40],
                }
            )
        return repositories

    def prepare_repository(self, owner_id: str, project_id: int) -> tuple[dict[str, Any], Path, Path]:
        project = self.get_project(owner_id, project_id)
        if not project["enabled"]:
            raise ValueError("Agent project is disabled")
        repo_path, worktrees = self.project_paths(owner_id, project_id)
        worktrees.mkdir(parents=True, exist_ok=True)
        env = self._git_env(
            owner_id,
            project.get("connection_id"),
            repository=str(project["repo_full_name"]),
            permissions={"contents": "read"},
        )
        clone_url = f"https://github.com/{project['repo_full_name']}.git"
        if not (repo_path / ".git").exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            self._git(("git", "clone", "--no-tags", clone_url, str(repo_path)), cwd=repo_path.parent, env=env, timeout=300)
        else:
            self._git(("git", "fetch", "origin", "--prune"), cwd=repo_path, env=env, timeout=180)
        self._git(("git", "rev-parse", "--verify", f"origin/{project['base_branch']}"), cwd=repo_path, env=env, timeout=30)
        return project, repo_path, worktrees

    def push_branch(self, owner_id: str, project_id: int, repo_path: Path, branch: str) -> str:
        project = self.get_project(owner_id, project_id)
        if not project["allow_push"]:
            raise ValueError("Git push is disabled for this project")
        if not branch.startswith("fdex-agent/"):
            raise ValueError("only generated fdex-agent branches may be pushed")
        env = self._git_env(
            owner_id,
            project.get("connection_id"),
            required=True,
            repository=str(project["repo_full_name"]),
            permissions={"contents": "write"},
        )
        return self._git(("git", "push", "-u", "origin", branch), cwd=repo_path, env=env, timeout=180)

    def create_pr(self, owner_id: str, project_id: int, *, head: str, title: str, body: str = "") -> str:
        project = self.get_project(owner_id, project_id)
        if not project["allow_pr"]:
            raise ValueError("Pull request creation is disabled for this project")
        if not head.startswith("fdex-agent/"):
            raise ValueError("only generated fdex-agent branches may create pull requests")
        connection_id = project.get("connection_id")
        if not connection_id:
            raise ValueError("GitHub connection is required")
        token = self.connection_token(
            owner_id,
            int(connection_id),
            repository=str(project["repo_full_name"]),
            permissions={"pull_requests": "write"},
        )
        payload = {
            "title": (title or "FDEX Agent changes")[:240],
            "head": head,
            "base": project["base_branch"],
            "body": body[:60000],
        }
        result = self._github_json(
            token,
            f"https://api.github.com/repos/{project['repo_full_name']}/pulls",
            method="POST",
            payload=payload,
        )
        url = str(result.get("html_url") or "")
        if not url:
            raise RuntimeError("GitHub did not return a pull request URL")
        return url

    def _git_env(
        self,
        owner_id: str,
        connection_id: Any,
        *,
        required: bool = False,
        repository: str = "",
        permissions: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if connection_id:
            token = self.connection_token(
                owner_id,
                int(connection_id),
                repository=repository,
                permissions=permissions,
            )
            basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
                }
            )
        elif required:
            raise ValueError("GitHub connection is required")
        return env

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def agent_project_store() -> GitHubAppAgentProjectStore:
    store = GitHubAppAgentProjectStore(DB_PATH, KEY_PATH)
    store.init()
    return store
