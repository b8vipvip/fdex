from __future__ import annotations

import base64
import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_ALLOWED_PLUGINS = {"gitlab", "gitee"}
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_MAX_CONTENT_BYTES = 2 * 1024 * 1024
_MAX_REPOSITORIES = 2000
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-code-hosts.db"
KEY_PATH = _DATA_DIR / "plugin-code-hosts.key"


class CodeHostPluginError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner(value: str) -> str:
    clean = (value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _plugin(value: str) -> str:
    clean = (value or "").strip().lower()
    if clean not in _ALLOWED_PLUGINS:
        raise ValueError("当前代码托管适配器仅支持 GitLab / Gitee")
    return clean


def _base_url(plugin_id: str, value: str = "") -> str:
    clean_plugin = _plugin(plugin_id)
    if clean_plugin == "gitee":
        return "https://gitee.com"
    clean = (value or "https://gitlab.com").strip().rstrip("/")
    # Phase 7.42 deliberately starts with GitLab.com only. Arbitrary self-managed base URLs would
    # make this server-side connector an SSRF primitive unless it is backed by a dedicated egress
    # allowlist / DNS-rebinding-safe transport. Add self-managed instances through that control
    # plane rather than weakening this invariant.
    if clean != "https://gitlab.com":
        raise ValueError("当前 GitLab 适配器仅开放 https://gitlab.com；自建 GitLab 将在安全出口白名单接入")
    return clean


def _repo(plugin_id: str, value: str) -> str:
    clean = (value or "").strip().strip("/")
    if not clean or len(clean) > 400 or "\x00" in clean:
        raise ValueError("仓库/项目路径无效")
    parts = clean.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("仓库/项目路径无效")
    if _plugin(plugin_id) == "gitee" and len(parts) != 2:
        raise ValueError("Gitee 仓库必须使用 owner/repo")
    if _plugin(plugin_id) == "gitlab" and len(parts) < 2:
        raise ValueError("GitLab 项目必须使用 namespace/project")
    return clean


def _branch(value: str) -> str:
    clean = (value or "main").strip()
    if not clean or len(clean) > 180 or "\x00" in clean or "\n" in clean or "\r" in clean:
        raise ValueError("分支名称无效")
    return clean


def _file_path(value: str) -> str:
    clean = (value or "").strip().lstrip("/")
    if not clean or len(clean) > 1000 or "\x00" in clean:
        raise ValueError("文件路径无效")
    if any(part in {"", ".", ".."} for part in clean.split("/")):
        raise ValueError("文件路径无效")
    return clean


def _commit_message(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise ValueError("提交说明不能为空")
    return clean[:500]


class CodeHostCredentialStore:
    def __init__(self, db_path: Path = DB_PATH, key_path: Path = KEY_PATH) -> None:
        self.db_path = db_path.resolve()
        self.key_path = key_path.resolve()
        self._fernet: Fernet | None = None

    def _cipher(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.key_path.parent, 0o700)
        except OSError:
            pass
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, key + b"\n")
            finally:
                os.close(fd)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = Fernet(key)
        return self._fernet

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS code_host_connections (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       owner_id TEXT NOT NULL,
                       plugin_id TEXT NOT NULL,
                       base_url TEXT NOT NULL,
                       account_id TEXT NOT NULL DEFAULT '',
                       account_login TEXT NOT NULL DEFAULT '',
                       account_name TEXT NOT NULL DEFAULT '',
                       token_cipher TEXT NOT NULL,
                       repository_count INTEGER NOT NULL DEFAULT 0,
                       last_checked_at TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL,
                       UNIQUE(owner_id,plugin_id)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_host_owner ON code_host_connections(owner_id,plugin_id)"
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise CodeHostPluginError("插件凭据解密失败，请重新连接") from exc

    @staticmethod
    def _row(row: sqlite3.Row, *, secret: bool = False, token: str = "") -> dict[str, Any]:
        data = {
            "id": int(row["id"]),
            "owner_id": str(row["owner_id"]),
            "plugin_id": str(row["plugin_id"]),
            "base_url": str(row["base_url"]),
            "account_id": str(row["account_id"]),
            "account_login": str(row["account_login"]),
            "account_name": str(row["account_name"]),
            "repository_count": int(row["repository_count"] or 0),
            "last_checked_at": str(row["last_checked_at"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "token_configured": bool(str(row["token_cipher"] or "")),
        }
        if secret:
            data["token"] = token
        return data

    def get(self, owner_id: str, plugin_id: str, *, secret: bool = False) -> dict[str, Any] | None:
        self.init()
        clean_owner = _owner(owner_id)
        clean_plugin = _plugin(plugin_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM code_host_connections WHERE owner_id=? AND plugin_id=? LIMIT 1",
                (clean_owner, clean_plugin),
            ).fetchone()
        if row is None:
            return None
        token = self._decrypt(str(row["token_cipher"])) if secret else ""
        return self._row(row, secret=secret, token=token)

    def save_verified(
        self,
        owner_id: str,
        plugin_id: str,
        *,
        base_url: str,
        token: str,
        profile: dict[str, Any],
        repository_count: int,
    ) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        clean_plugin = _plugin(plugin_id)
        clean_token = (token or "").strip()
        if len(clean_token) < 8 or len(clean_token) > 4096:
            raise ValueError("访问令牌格式无效")
        clean_base = _base_url(clean_plugin, base_url)
        login = str(profile.get("username") or profile.get("login") or "").strip()[:160]
        account_id = str(profile.get("id") or "").strip()[:120]
        account_name = str(profile.get("name") or login).strip()[:200]
        if not login or not account_id:
            raise CodeHostPluginError("远端账号信息不完整，未保存连接")
        now = _now()
        encrypted = self._encrypt(clean_token)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO code_host_connections(
                       owner_id,plugin_id,base_url,account_id,account_login,account_name,token_cipher,
                       repository_count,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id,plugin_id) DO UPDATE SET
                       base_url=excluded.base_url,
                       account_id=excluded.account_id,
                       account_login=excluded.account_login,
                       account_name=excluded.account_name,
                       token_cipher=excluded.token_cipher,
                       repository_count=excluded.repository_count,
                       last_checked_at=excluded.last_checked_at,
                       updated_at=excluded.updated_at""",
                (
                    clean_owner,
                    clean_plugin,
                    clean_base,
                    account_id,
                    login,
                    account_name,
                    encrypted,
                    max(0, int(repository_count)),
                    now,
                    now,
                    now,
                ),
            )
        saved = self.get(clean_owner, clean_plugin)
        if saved is None:
            raise CodeHostPluginError("插件连接保存失败")
        return saved

    def update_repository_count(self, owner_id: str, plugin_id: str, count: int) -> None:
        self.init()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE code_host_connections SET repository_count=?,last_checked_at=?,updated_at=? WHERE owner_id=? AND plugin_id=?",
                (max(0, int(count)), _now(), _now(), _owner(owner_id), _plugin(plugin_id)),
            )

    def delete(self, owner_id: str, plugin_id: str) -> int:
        self.init()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM code_host_connections WHERE owner_id=? AND plugin_id=?",
                (_owner(owner_id), _plugin(plugin_id)),
            )
        return max(0, int(cur.rowcount or 0))

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM code_host_connections WHERE owner_id=?", (_owner(owner_id),))
        return max(0, int(cur.rowcount or 0))


@lru_cache(maxsize=1)
def code_host_credential_store() -> CodeHostCredentialStore:
    store = CodeHostCredentialStore()
    store.init()
    return store


def _api_root(plugin_id: str, base_url: str) -> str:
    clean_plugin = _plugin(plugin_id)
    clean_base = _base_url(clean_plugin, base_url)
    return f"{clean_base}/api/v4" if clean_plugin == "gitlab" else "https://gitee.com/api/v5"


def _headers(plugin_id: str, token: str) -> dict[str, str]:
    clean = _plugin(plugin_id)
    headers = {"Accept": "application/json", "User-Agent": "FDEX-Plugin-Runtime/1"}
    if clean == "gitlab":
        headers["PRIVATE-TOKEN"] = token
    else:
        # Gitee API v5 is OAuth2-authenticated; using the bearer header avoids putting credentials
        # into URLs/query strings where reverse proxies and access logs commonly record them.
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()[:500]
    if isinstance(payload, dict):
        raw = payload.get("message") or payload.get("error_description") or payload.get("error")
        if isinstance(raw, dict):
            return "; ".join(f"{k}: {v}" for k, v in list(raw.items())[:6])[:500]
        if raw:
            return str(raw)[:500]
    return ""


def _request(
    plugin_id: str,
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    allow_404: bool = False,
) -> httpx.Response | None:
    url = f"{_api_root(plugin_id, base_url)}{path}"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.request(
                method.upper(),
                url,
                headers=_headers(plugin_id, token),
                params=params,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise CodeHostPluginError(f"{_plugin(plugin_id)} 网络请求失败：{type(exc).__name__}") from exc
    if allow_404 and response.status_code == 404:
        return None
    if response.is_redirect:
        raise CodeHostPluginError("代码托管 API 返回了未允许的重定向")
    if response.status_code < 200 or response.status_code >= 300:
        detail = _message(response)
        suffix = f"：{detail}" if detail else ""
        raise CodeHostPluginError(f"代码托管 API 请求失败（HTTP {response.status_code}）{suffix}")
    return response


def _json(response: httpx.Response | None) -> Any:
    if response is None:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise CodeHostPluginError("代码托管 API 返回了无效 JSON") from exc


def verify_code_host_token(plugin_id: str, token: str, *, base_url: str = "") -> dict[str, Any]:
    clean_plugin = _plugin(plugin_id)
    clean_base = _base_url(clean_plugin, base_url)
    clean_token = (token or "").strip()
    if len(clean_token) < 8 or len(clean_token) > 4096:
        raise ValueError("访问令牌格式无效")
    payload = _json(_request(clean_plugin, clean_base, clean_token, "GET", "/user"))
    if not isinstance(payload, dict):
        raise CodeHostPluginError("无法识别远端账号信息")
    login = str(payload.get("username") or payload.get("login") or "").strip()
    if not login or not payload.get("id"):
        raise CodeHostPluginError("访问令牌没有返回有效账号身份")
    return payload


def _connection(owner_id: str, plugin_id: str) -> dict[str, Any]:
    row = code_host_credential_store().get(owner_id, plugin_id, secret=True)
    if row is None:
        raise PermissionError(f"{_plugin(plugin_id)} 插件尚未连接")
    return row


def _list_with_connection(connection: dict[str, Any]) -> list[dict[str, Any]]:
    plugin_id = str(connection["plugin_id"])
    base_url = str(connection["base_url"])
    token = str(connection["token"])
    repositories: list[dict[str, Any]] = []
    for page in range(1, 21):
        if plugin_id == "gitlab":
            params = {
                "membership": "true",
                "per_page": 100,
                "page": page,
                "order_by": "last_activity_at",
                "sort": "desc",
            }
            payload = _json(_request(plugin_id, base_url, token, "GET", "/projects", params=params))
        else:
            params = {"per_page": 100, "page": page, "sort": "updated", "direction": "desc"}
            payload = _json(_request(plugin_id, base_url, token, "GET", "/user/repos", params=params))
        if not isinstance(payload, list):
            raise CodeHostPluginError("仓库列表返回格式无效")
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            if plugin_id == "gitlab":
                permissions = raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
                project_access = permissions.get("project_access") if isinstance(permissions.get("project_access"), dict) else {}
                group_access = permissions.get("group_access") if isinstance(permissions.get("group_access"), dict) else {}
                level = max(int(project_access.get("access_level") or 0), int(group_access.get("access_level") or 0))
                full_name = str(raw.get("path_with_namespace") or "")
                item = {
                    "id": str(raw.get("id") or ""),
                    "full_name": full_name,
                    "private": str(raw.get("visibility") or "private") != "public",
                    "default_branch": str(raw.get("default_branch") or "main")[:180],
                    "archived": bool(raw.get("archived")),
                    "updated_at": str(raw.get("last_activity_at") or "")[:80],
                    "web_url": str(raw.get("web_url") or "")[:500],
                    "can_push": level >= 30,
                    "can_pr": level >= 30,
                }
            else:
                perms = raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
                owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
                full_name = str(raw.get("full_name") or f"{owner.get('login') or owner.get('name') or ''}/{raw.get('path') or raw.get('name') or ''}").strip("/")
                item = {
                    "id": str(raw.get("id") or ""),
                    "full_name": full_name,
                    "private": bool(raw.get("private")),
                    "default_branch": str(raw.get("default_branch") or "master")[:180],
                    "archived": bool(raw.get("archived")),
                    "updated_at": str(raw.get("updated_at") or "")[:80],
                    "web_url": str(raw.get("html_url") or raw.get("human_name") or "")[:500],
                    "can_push": bool(perms.get("push") or perms.get("admin")),
                    "can_pr": bool(perms.get("push") or perms.get("admin")),
                }
            if item["full_name"]:
                repositories.append(item)
            if len(repositories) >= _MAX_REPOSITORIES:
                return repositories
        if len(payload) < 100:
            break
    return repositories


def connect_code_host(owner_id: str, plugin_id: str, token: str, *, base_url: str = "") -> dict[str, Any]:
    clean_plugin = _plugin(plugin_id)
    clean_base = _base_url(clean_plugin, base_url)
    profile = verify_code_host_token(clean_plugin, token, base_url=clean_base)
    transient = {
        "plugin_id": clean_plugin,
        "base_url": clean_base,
        "token": (token or "").strip(),
    }
    repositories = _list_with_connection(transient)
    return code_host_credential_store().save_verified(
        owner_id,
        clean_plugin,
        base_url=clean_base,
        token=token,
        profile=profile,
        repository_count=len(repositories),
    )


def disconnect_code_host(owner_id: str, plugin_id: str) -> int:
    return code_host_credential_store().delete(owner_id, plugin_id)


def code_host_connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
    clean = _plugin(plugin_id)
    row = code_host_credential_store().get(owner_id, clean)
    if row is None:
        return {
            "connected": False,
            "connection_count": 0,
            "account_label": "",
            "repository_count": 0,
            "connectable": True,
            "state": "available",
        }
    return {
        "connected": True,
        "connection_count": 1,
        "account_label": str(row.get("account_login") or row.get("account_name") or "")[:240],
        "repository_count": int(row.get("repository_count") or 0),
        "last_checked_at": str(row.get("last_checked_at") or ""),
        "connectable": True,
        "state": "connected",
    }


def list_code_host_repositories(owner_id: str, plugin_id: str) -> list[dict[str, Any]]:
    connection = _connection(owner_id, plugin_id)
    repositories = _list_with_connection(connection)
    code_host_credential_store().update_repository_count(owner_id, plugin_id, len(repositories))
    return repositories


def read_code_host_file(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    path: str,
    *,
    ref: str = "",
) -> dict[str, Any]:
    connection = _connection(owner_id, plugin_id)
    clean_plugin = _plugin(plugin_id)
    clean_repo = _repo(clean_plugin, repo_full_name)
    clean_path = _file_path(path)
    clean_ref = _branch(ref or "HEAD")
    if clean_plugin == "gitlab":
        endpoint = f"/projects/{quote(clean_repo, safe='')}/repository/files/{quote(clean_path, safe='')}"
        payload = _json(_request(clean_plugin, str(connection["base_url"]), str(connection["token"]), "GET", endpoint, params={"ref": clean_ref}))
        if not isinstance(payload, dict):
            raise CodeHostPluginError("GitLab 文件返回格式无效")
        encoded = str(payload.get("content") or "").replace("\n", "")
        sha = str(payload.get("last_commit_id") or payload.get("blob_id") or "")
    else:
        endpoint = f"/repos/{quote(clean_repo.split('/')[0], safe='')}/{quote(clean_repo.split('/')[1], safe='')}/contents/{quote(clean_path, safe='/')}"
        payload = _json(_request(clean_plugin, str(connection["base_url"]), str(connection["token"]), "GET", endpoint, params={"ref": clean_ref}))
        if not isinstance(payload, dict):
            raise CodeHostPluginError("Gitee 文件返回格式无效")
        encoded = str(payload.get("content") or "").replace("\n", "")
        sha = str(payload.get("sha") or "")
    try:
        content = base64.b64decode(encoded, validate=False)
    except ValueError as exc:
        raise CodeHostPluginError("远端文件内容不是有效 Base64") from exc
    if len(content) > _MAX_CONTENT_BYTES:
        raise CodeHostPluginError("文件超过 FDEX 插件单次读取上限 2 MiB")
    return {
        "plugin_id": clean_plugin,
        "repository": clean_repo,
        "path": clean_path,
        "ref": clean_ref,
        "sha": sha,
        "size": len(content),
        "content": content.decode("utf-8", errors="replace"),
    }


def write_code_host_file(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    path: str,
    content: str,
    *,
    branch: str,
    message: str,
) -> dict[str, Any]:
    connection = _connection(owner_id, plugin_id)
    clean_plugin = _plugin(plugin_id)
    clean_repo = _repo(clean_plugin, repo_full_name)
    clean_path = _file_path(path)
    clean_branch = _branch(branch)
    clean_message = _commit_message(message)
    raw_content = (content or "").encode("utf-8")
    if len(raw_content) > _MAX_CONTENT_BYTES:
        raise ValueError("单次写入内容不能超过 2 MiB")
    base_url = str(connection["base_url"])
    token = str(connection["token"])
    if clean_plugin == "gitlab":
        endpoint = f"/projects/{quote(clean_repo, safe='')}/repository/files/{quote(clean_path, safe='')}"
        existing = _request(clean_plugin, base_url, token, "GET", endpoint, params={"ref": clean_branch}, allow_404=True)
        body: dict[str, Any] = {
            "branch": clean_branch,
            "content": content or "",
            "commit_message": clean_message,
        }
        method = "POST"
        if existing is not None:
            current = _json(existing)
            if isinstance(current, dict) and current.get("last_commit_id"):
                body["last_commit_id"] = str(current["last_commit_id"])
            method = "PUT"
        payload = _json(_request(clean_plugin, base_url, token, method, endpoint, json_body=body))
    else:
        owner_name, repo_name = clean_repo.split("/", 1)
        endpoint = f"/repos/{quote(owner_name, safe='')}/{quote(repo_name, safe='')}/contents/{quote(clean_path, safe='/')}"
        existing = _request(clean_plugin, base_url, token, "GET", endpoint, params={"ref": clean_branch}, allow_404=True)
        body = {
            "branch": clean_branch,
            "content": base64.b64encode(raw_content).decode("ascii"),
            "message": clean_message,
        }
        method = "POST"
        if existing is not None:
            current = _json(existing)
            if not isinstance(current, dict) or not current.get("sha"):
                raise CodeHostPluginError("Gitee 更新文件前无法取得当前 SHA")
            body["sha"] = str(current["sha"])
            method = "PUT"
        payload = _json(_request(clean_plugin, base_url, token, method, endpoint, json_body=body))
    if not isinstance(payload, dict):
        raise CodeHostPluginError("文件写入返回格式无效")
    return {
        "plugin_id": clean_plugin,
        "repository": clean_repo,
        "path": clean_path,
        "branch": clean_branch,
        "result": payload,
    }


def create_code_host_pull_request(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str = "",
) -> dict[str, Any]:
    connection = _connection(owner_id, plugin_id)
    clean_plugin = _plugin(plugin_id)
    clean_repo = _repo(clean_plugin, repo_full_name)
    source = _branch(source_branch)
    target = _branch(target_branch)
    clean_title = (title or "").strip()[:300]
    if not clean_title:
        raise ValueError("PR/MR 标题不能为空")
    base_url = str(connection["base_url"])
    token = str(connection["token"])
    if clean_plugin == "gitlab":
        endpoint = f"/projects/{quote(clean_repo, safe='')}/merge_requests"
        body = {
            "source_branch": source,
            "target_branch": target,
            "title": clean_title,
            "description": (description or "")[:10000],
        }
    else:
        owner_name, repo_name = clean_repo.split("/", 1)
        endpoint = f"/repos/{quote(owner_name, safe='')}/{quote(repo_name, safe='')}/pulls"
        body = {
            "head": source,
            "base": target,
            "title": clean_title,
            "body": (description or "")[:10000],
        }
    payload = _json(_request(clean_plugin, base_url, token, "POST", endpoint, json_body=body))
    if not isinstance(payload, dict):
        raise CodeHostPluginError("PR/MR 创建返回格式无效")
    return {
        "plugin_id": clean_plugin,
        "repository": clean_repo,
        "source_branch": source,
        "target_branch": target,
        "url": str(payload.get("web_url") or payload.get("html_url") or "")[:800],
        "number": int(payload.get("iid") or payload.get("number") or 0),
        "result": payload,
    }
