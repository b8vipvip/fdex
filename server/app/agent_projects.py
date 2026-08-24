from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings

_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")
_SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,180}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "agent-projects.db"
KEY_PATH = _DATA_DIR / "agent-projects.key"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_scope(value: str) -> str:
    clean = (value or "").strip()
    if not _SAFE_SCOPE.fullmatch(clean):
        raise ValueError("owner scope is invalid")
    return clean


def _safe_repo(value: str) -> str:
    clean = (value or "").strip().removesuffix(".git")
    if clean.startswith("https://github.com/"):
        clean = clean.removeprefix("https://github.com/")
    if not _SAFE_REPO.fullmatch(clean):
        raise ValueError("GitHub repository must use owner/name")
    return clean


def _safe_branch(value: str) -> str:
    clean = (value or "main").strip()
    if not _SAFE_BRANCH.fullmatch(clean) or ".." in clean or clean.startswith("/"):
        raise ValueError("base branch is invalid")
    return clean


class AgentProjectStore:
    def __init__(self, db_path: Path = DB_PATH, key_path: Path = KEY_PATH) -> None:
        self.db_path = db_path.resolve()
        self.key_path = key_path.resolve()
        self._fernet: Fernet | None = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self._cipher()
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS github_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    login TEXT NOT NULL DEFAULT '',
                    token_cipher TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_github_owner ON github_connections(owner_id,id);
                CREATE TABLE IF NOT EXISTS agent_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL,
                    base_branch TEXT NOT NULL DEFAULT 'main',
                    connection_id INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    allow_push INTEGER NOT NULL DEFAULT 0,
                    allow_pr INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, repo_full_name),
                    FOREIGN KEY(connection_id) REFERENCES github_connections(id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_project_owner ON agent_projects(owner_id,enabled,id);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _cipher(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.key_path.with_suffix(".tmp")
            tmp.write_bytes(key + b"\n")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.key_path)
        self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode()).decode() if value else ""

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("GitHub connector secret cannot be decrypted") from exc

    def list_connections(self, owner_id: str) -> list[dict[str, Any]]:
        self.init(); owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM github_connections WHERE owner_id=? ORDER BY id", (owner_id,)).fetchall()
        return [self._connection_row(row) for row in rows]

    def _connection_row(self, row: sqlite3.Row, *, secret: bool = False) -> dict[str, Any]:
        data = dict(row); cipher = data.pop("token_cipher")
        data["token_configured"] = bool(cipher)
        if secret:
            data["token"] = self.decrypt(cipher)
        return data

    def get_connection(self, owner_id: str, connection_id: int, *, secret: bool = False) -> dict[str, Any]:
        self.init(); owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM github_connections WHERE id=? AND owner_id=?", (connection_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("GitHub connection not found")
        return self._connection_row(row, secret=secret)

    def save_connection(self, owner_id: str, name: str, token: str, connection_id: int | None = None) -> dict[str, Any]:
        self.init(); owner_id = _safe_scope(owner_id); name = (name or "GitHub").strip()[:80]
        token = (token or "").strip()
        if connection_id:
            old = self.get_connection(owner_id, connection_id, secret=True)
            token = token or str(old.get("token") or "")
        if not token:
            raise ValueError("GitHub token is required")
        profile = self._github_json(token, "https://api.github.com/user")
        login = str(profile.get("login") or "").strip()
        if not login:
            raise ValueError("GitHub token validation failed")
        now = _now()
        with self.db() as conn:
            if connection_id:
                conn.execute("UPDATE github_connections SET name=?,login=?,token_cipher=?,updated_at=? WHERE id=? AND owner_id=?", (name, login, self.encrypt(token), now, connection_id, owner_id))
                cid = connection_id
            else:
                cur = conn.execute("INSERT INTO github_connections(owner_id,name,login,token_cipher,created_at,updated_at) VALUES(?,?,?,?,?,?)", (owner_id, name, login, self.encrypt(token), now, now))
                cid = int(cur.lastrowid)
        return self.get_connection(owner_id, cid)

    def delete_connection(self, owner_id: str, connection_id: int) -> None:
        self.get_connection(owner_id, connection_id)
        with self.db() as conn:
            used = conn.execute("SELECT COUNT(*) FROM agent_projects WHERE owner_id=? AND connection_id=?", (owner_id, connection_id)).fetchone()[0]
            if used:
                raise ValueError("GitHub connection is still used by a project")
            conn.execute("DELETE FROM github_connections WHERE id=? AND owner_id=?", (connection_id, owner_id))

    def list_projects(self, owner_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        self.init(); owner_id = _safe_scope(owner_id)
        sql = "SELECT * FROM agent_projects WHERE owner_id=?" + (" AND enabled=1" if enabled_only else "") + " ORDER BY name,id"
        with self.db() as conn:
            rows = conn.execute(sql, (owner_id,)).fetchall()
        return [self._project_row(row) for row in rows]

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("enabled", "allow_push", "allow_pr"):
            data[key] = bool(data[key])
        return data

    def get_project(self, owner_id: str, project_id: int) -> dict[str, Any]:
        self.init(); owner_id = _safe_scope(owner_id)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM agent_projects WHERE id=? AND owner_id=?", (project_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("Agent project not found")
        return self._project_row(row)

    def save_project(self, owner_id: str, *, name: str, repo_full_name: str, base_branch: str = "main", connection_id: int | None = None, allow_push: bool = False, allow_pr: bool = False, enabled: bool = True, project_id: int | None = None) -> dict[str, Any]:
        self.init(); owner_id = _safe_scope(owner_id); repo = _safe_repo(repo_full_name); branch = _safe_branch(base_branch)
        name = (name or repo.split("/")[-1]).strip()[:100]
        if connection_id is not None:
            self.get_connection(owner_id, connection_id)
        now = _now()
        with self.db() as conn:
            if project_id:
                conn.execute("UPDATE agent_projects SET name=?,repo_full_name=?,base_branch=?,connection_id=?,enabled=?,allow_push=?,allow_pr=?,updated_at=? WHERE id=? AND owner_id=?", (name, repo, branch, connection_id, int(enabled), int(allow_push), int(allow_pr), now, project_id, owner_id))
                pid = project_id
            else:
                cur = conn.execute("INSERT INTO agent_projects(owner_id,name,repo_full_name,base_branch,connection_id,enabled,allow_push,allow_pr,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (owner_id, name, repo, branch, connection_id, int(enabled), int(allow_push), int(allow_pr), now, now))
                pid = int(cur.lastrowid)
        return self.get_project(owner_id, pid)

    def delete_project(self, owner_id: str, project_id: int) -> None:
        self.get_project(owner_id, project_id)
        with self.db() as conn:
            conn.execute("DELETE FROM agent_projects WHERE id=? AND owner_id=?", (project_id, owner_id))

    def project_paths(self, owner_id: str, project_id: int) -> tuple[Path, Path]:
        owner_id = _safe_scope(owner_id)
        root = Path(fresh_settings().fdex_agent_sandbox_root).expanduser().resolve()
        project_root = root / "owners" / owner_id / "projects" / str(int(project_id))
        return (project_root / "repository").resolve(), (project_root / "worktrees").resolve()

    def prepare_repository(self, owner_id: str, project_id: int) -> tuple[dict[str, Any], Path, Path]:
        project = self.get_project(owner_id, project_id)
        if not project["enabled"]:
            raise ValueError("Agent project is disabled")
        repo_path, worktrees = self.project_paths(owner_id, project_id)
        worktrees.mkdir(parents=True, exist_ok=True)
        env = self._git_env(owner_id, project.get("connection_id"))
        clone_url = f"https://github.com/{project['repo_full_name']}.git"
        if not (repo_path / ".git").exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            self._git(("git", "clone", "--no-tags", clone_url, str(repo_path)), cwd=repo_path.parent, env=env, timeout=300)
        else:
            self._git(("git", "fetch", "origin", project["base_branch"], "--prune"), cwd=repo_path, env=env, timeout=180)
        return project, repo_path, worktrees

    def push_branch(self, owner_id: str, project_id: int, repo_path: Path, branch: str) -> str:
        project = self.get_project(owner_id, project_id)
        if not project["allow_push"]:
            raise ValueError("Git push is disabled for this project")
        env = self._git_env(owner_id, project.get("connection_id"), required=True)
        return self._git(("git", "push", "-u", "origin", branch), cwd=repo_path, env=env, timeout=180)

    def create_pr(self, owner_id: str, project_id: int, *, head: str, title: str, body: str = "") -> str:
        project = self.get_project(owner_id, project_id)
        if not project["allow_pr"]:
            raise ValueError("Pull request creation is disabled for this project")
        connection_id = project.get("connection_id")
        if not connection_id:
            raise ValueError("GitHub connection is required")
        token = str(self.get_connection(owner_id, int(connection_id), secret=True)["token"])
        payload = {"title": (title or "FDEX Agent changes")[:240], "head": head, "base": project["base_branch"], "body": body[:60000]}
        result = self._github_json(token, f"https://api.github.com/repos/{project['repo_full_name']}/pulls", method="POST", payload=payload)
        url = str(result.get("html_url") or "")
        if not url:
            raise RuntimeError("GitHub did not return a pull request URL")
        return url

    def _git_env(self, owner_id: str, connection_id: Any, *, required: bool = False) -> dict[str, str]:
        env = os.environ.copy()
        if connection_id:
            token = str(self.get_connection(owner_id, int(connection_id), secret=True)["token"])
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: bearer {token}"})
        elif required:
            raise ValueError("GitHub connection is required")
        return env

    @staticmethod
    def _git(args: tuple[str, ...], *, cwd: Path, env: dict[str, str], timeout: int) -> str:
        result = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False)
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise RuntimeError(output[-4000:] or f"git exited with {result.returncode}")
        return output[-20000:]

    @staticmethod
    def _github_json(token: str, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fdex-agent"}
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.request(method, url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise ValueError(f"GitHub API HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected GitHub API response")
        return data


@lru_cache(maxsize=1)
def agent_project_store() -> AgentProjectStore:
    store = AgentProjectStore(); store.init(); return store
