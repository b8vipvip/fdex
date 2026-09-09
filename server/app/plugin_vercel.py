from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_API_ROOT = "https://api.vercel.com"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,240}$")
_TEAM_RE = re.compile(r"^team_[A-Za-z0-9_-]{6,220}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-vercel.db"
KEY_PATH = _DATA_DIR / "plugin-vercel.key"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class VercelPluginError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _owner(value: str) -> str:
    clean = str(value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _resource(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not _RESOURCE_RE.fullmatch(clean):
        raise ValueError(f"{label} 格式无效")
    return clean


def _team_id(value: str) -> str:
    clean = str(value or "").strip()
    if clean and not _TEAM_RE.fullmatch(clean):
        raise ValueError("Vercel Team ID 格式无效，应类似 team_xxx")
    return clean


def _limit(value: int, maximum: int = 100) -> int:
    if isinstance(value, bool):
        raise ValueError("limit 必须是整数")
    try:
        clean = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit 必须是整数") from exc
    if clean < 1 or clean > maximum:
        raise ValueError(f"limit 必须在 1-{maximum} 之间")
    return clean


def _load_or_create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if path.exists():
        key = path.read_bytes().strip()
        Fernet(key)
        return key
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key + b"\n")
    finally:
        os.close(fd)
    return key


def _provider_error(response: httpx.Response, token: str = "") -> str:
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or "")
            elif error:
                message = str(error)
            if not message:
                message = str(payload.get("message") or "")
    except (ValueError, json.JSONDecodeError):
        message = ""
    if token and message:
        message = message.replace(token, "[redacted]")
    return message[:700]


def _request_with_token(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str,
) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        response = httpx.request(
            method,
            f"{_API_ROOT}{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise VercelPluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise VercelPluginError(f"{context}响应过大，已拒绝处理")
    if response.status_code < 200 or response.status_code >= 300:
        detail = _provider_error(response, token)
        suffix = f"：{detail}" if detail else ""
        raise VercelPluginError(f"{context}失败（HTTP {response.status_code}）{suffix}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise VercelPluginError(f"{context}返回了无效 JSON") from exc


class VercelCredentialStore:
    def __init__(self, path: Path = DB_PATH, key_path: Path = KEY_PATH) -> None:
        self.path = path.resolve()
        self.key_path = key_path.resolve()
        self._initialized = False
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_load_or_create_key(self.key_path))
        return self._fernet

    def init(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS vercel_connections (
                       owner_id TEXT PRIMARY KEY,
                       token_cipher TEXT NOT NULL,
                       user_id TEXT NOT NULL DEFAULT '',
                       username TEXT NOT NULL DEFAULT '',
                       email TEXT NOT NULL DEFAULT '',
                       team_id TEXT NOT NULL DEFAULT '',
                       team_slug TEXT NOT NULL DEFAULT '',
                       team_name TEXT NOT NULL DEFAULT '',
                       project_count INTEGER NOT NULL DEFAULT 0,
                       last_checked_at TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._initialized = True

    def put(self, owner_id: str, token: str, metadata: dict[str, Any]) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        raw = str(token or "").strip()
        if len(raw) < 8 or len(raw) > 4096 or "\x00" in raw:
            raise ValueError("Vercel Access Token 格式无效")
        cipher = self.fernet.encrypt(raw.encode("utf-8")).decode("ascii")
        now = _now()
        with sqlite3.connect(self.path) as conn:
            existing = conn.execute("SELECT created_at FROM vercel_connections WHERE owner_id=?", (clean_owner,)).fetchone()
            created_at = str(existing[0]) if existing else now
            conn.execute(
                """INSERT INTO vercel_connections(
                       owner_id,token_cipher,user_id,username,email,team_id,team_slug,team_name,
                       project_count,last_checked_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                       token_cipher=excluded.token_cipher,user_id=excluded.user_id,username=excluded.username,
                       email=excluded.email,team_id=excluded.team_id,team_slug=excluded.team_slug,
                       team_name=excluded.team_name,project_count=excluded.project_count,
                       last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at""",
                (
                    clean_owner,
                    cipher,
                    str(metadata.get("user_id") or "")[:240],
                    str(metadata.get("username") or "")[:240],
                    str(metadata.get("email") or "")[:320],
                    str(metadata.get("team_id") or "")[:240],
                    str(metadata.get("team_slug") or "")[:240],
                    str(metadata.get("team_name") or "")[:240],
                    int(metadata.get("project_count") or 0),
                    now,
                    created_at,
                    now,
                ),
            )
        return self.get(clean_owner) or {}

    def get(self, owner_id: str) -> dict[str, Any] | None:
        self.init()
        clean_owner = _owner(owner_id)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM vercel_connections WHERE owner_id=?", (clean_owner,)).fetchone()
        if row is None:
            return None
        return {
            "owner_id": clean_owner,
            "user_id": str(row["user_id"]),
            "username": str(row["username"]),
            "email": str(row["email"]),
            "team_id": str(row["team_id"]),
            "team_slug": str(row["team_slug"]),
            "team_name": str(row["team_name"]),
            "project_count": int(row["project_count"] or 0),
            "last_checked_at": str(row["last_checked_at"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "token_configured": bool(row["token_cipher"]),
        }

    def token(self, owner_id: str) -> str:
        self.init()
        clean_owner = _owner(owner_id)
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT token_cipher FROM vercel_connections WHERE owner_id=?", (clean_owner,)).fetchone()
        if row is None:
            raise VercelPluginError("Vercel 尚未连接")
        try:
            return self.fernet.decrypt(str(row[0]).encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise VercelPluginError("Vercel 凭据无法解密，请重新连接") from exc

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        clean_owner = _owner(owner_id)
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute("DELETE FROM vercel_connections WHERE owner_id=?", (clean_owner,))
        return max(0, int(cur.rowcount or 0))


@lru_cache(maxsize=1)
def vercel_credential_store() -> VercelCredentialStore:
    store = VercelCredentialStore()
    store.init()
    return store


def _connection(owner_id: str) -> tuple[dict[str, Any], str]:
    item = vercel_credential_store().get(owner_id)
    if item is None:
        raise VercelPluginError("Vercel 尚未连接")
    return item, vercel_credential_store().token(owner_id)


def _scope_params(connection: dict[str, Any]) -> dict[str, str]:
    team = str(connection.get("team_id") or "").strip()
    return {"teamId": team} if team else {}


def _request(
    owner_id: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    context: str,
) -> Any:
    connection, token = _connection(owner_id)
    merged = {**_scope_params(connection), **(params or {})}
    return _request_with_token(token, method, path, params=merged, json_body=json_body, context=context)


def _user_summary(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict) and isinstance(payload.get("user"), dict):
        payload = payload["user"]
    item = payload if isinstance(payload, dict) else {}
    return {
        "user_id": str(item.get("uid") or item.get("id") or "")[:240],
        "username": str(item.get("username") or item.get("slug") or item.get("name") or "")[:240],
        "email": str(item.get("email") or "")[:320],
    }


def _project_summary(item: dict[str, Any]) -> dict[str, Any]:
    link = item.get("link") if isinstance(item.get("link"), dict) else {}
    return {
        "id": str(item.get("id") or "")[:240],
        "name": str(item.get("name") or "")[:240],
        "framework": str(item.get("framework") or "")[:120],
        "created_at": item.get("createdAt"),
        "updated_at": item.get("updatedAt"),
        "git": {
            "type": str(link.get("type") or "")[:80],
            "org": str(link.get("org") or "")[:240],
            "repo": str(link.get("repo") or "")[:240],
        },
    }


def _deployment_summary(item: dict[str, Any]) -> dict[str, Any]:
    git_source = item.get("gitSource") if isinstance(item.get("gitSource"), dict) else {}
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    return {
        "id": str(item.get("id") or item.get("uid") or "")[:240],
        "name": str(item.get("name") or "")[:240],
        "project_id": str(item.get("projectId") or "")[:240],
        "url": str(item.get("url") or "")[:1200],
        "inspector_url": str(item.get("inspectorUrl") or "")[:1200],
        "state": str(item.get("readyState") or item.get("state") or "")[:80],
        "target": str(item.get("target") or "preview")[:80],
        "ready_substate": str(item.get("readySubstate") or "")[:80],
        "checks_state": str(item.get("checksState") or "")[:80],
        "checks_conclusion": str(item.get("checksConclusion") or "")[:80],
        "source": str(item.get("source") or "")[:80],
        "created_at": item.get("createdAt") or item.get("created"),
        "building_at": item.get("buildingAt"),
        "ready_at": item.get("ready") or item.get("readyAt"),
        "branch": str(git_source.get("ref") or meta.get("githubCommitRef") or meta.get("gitlabCommitRef") or "")[:300],
        "sha": str(git_source.get("sha") or meta.get("githubCommitSha") or meta.get("gitlabCommitSha") or "")[:160],
        "error_code": str(item.get("errorCode") or "")[:160],
        "error_message": str(item.get("errorMessage") or "")[:1000],
    }


def _projects_from_payload(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], {}
    if isinstance(payload, dict):
        raw = payload.get("projects")
        if not isinstance(raw, list):
            raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        return [item for item in raw if isinstance(item, dict)], pagination
    raise VercelPluginError("Vercel 项目列表返回格式无效")


def connect_vercel(owner_id: str, access_token: str, *, team_id: str = "") -> dict[str, Any]:
    clean_owner = _owner(owner_id)
    token = str(access_token or "").strip()
    if len(token) < 8 or len(token) > 4096 or "\x00" in token:
        raise ValueError("Vercel Access Token 格式无效")
    team = _team_id(team_id)
    user = _user_summary(_request_with_token(token, "GET", "/v2/user", context="Vercel 身份验证"))
    team_meta: dict[str, str] = {"team_id": team, "team_slug": "", "team_name": ""}
    if team:
        payload = _request_with_token(token, "GET", f"/v2/teams/{quote(team, safe='')}", context="Vercel Team 验证")
        if not isinstance(payload, dict):
            raise VercelPluginError("Vercel Team 验证返回格式无效")
        team_meta["team_slug"] = str(payload.get("slug") or "")[:240]
        team_meta["team_name"] = str(payload.get("name") or "")[:240]
    params = {"limit": 100, **({"teamId": team} if team else {})}
    projects_payload = _request_with_token(token, "GET", "/v10/projects", params=params, context="Vercel 项目验证")
    projects, _ = _projects_from_payload(projects_payload)
    return vercel_credential_store().put(
        clean_owner,
        token,
        {**user, **team_meta, "project_count": len(projects)},
    )


def disconnect_vercel(owner_id: str) -> bool:
    return bool(vercel_credential_store().delete_owner(owner_id))


def vercel_connection_status(owner_id: str) -> dict[str, Any]:
    try:
        item = vercel_credential_store().get(owner_id)
    except (OSError, RuntimeError, ValueError):
        item = None
    if item is None:
        return {"connected": False, "connection_count": 0, "account_label": "", "connectable": True, "state": "available"}
    label = str(item.get("team_name") or item.get("team_slug") or item.get("username") or item.get("email") or "Vercel")
    return {
        "connected": True,
        "connection_count": 1,
        "account_label": label[:240],
        "project_count": int(item.get("project_count") or 0),
        "team_id": str(item.get("team_id") or ""),
        "connectable": True,
        "state": "connected",
    }


def list_vercel_projects(owner_id: str, search: str = "", *, limit: int = 50, cursor: str = "") -> dict[str, Any]:
    maximum = _limit(limit)
    clean_search = str(search or "").strip()[:240]
    clean_cursor = str(cursor or "").strip()[:240]
    params: dict[str, Any] = {"limit": maximum}
    if clean_search:
        params["search"] = clean_search
    if clean_cursor:
        params["from"] = clean_cursor
    payload = _request(owner_id, "GET", "/v10/projects", params=params, context="Vercel 项目列表")
    projects, pagination = _projects_from_payload(payload)
    return {
        "plugin_id": "vercel",
        "projects": [_project_summary(item) for item in projects],
        "count": len(projects),
        "pagination": {key: pagination.get(key) for key in ("count", "next", "prev") if key in pagination},
    }


def list_vercel_deployments(
    owner_id: str,
    *,
    project_id: str = "",
    limit: int = 20,
    state: str = "",
    target: str = "",
    branch: str = "",
) -> dict[str, Any]:
    maximum = _limit(limit, 100)
    params: dict[str, Any] = {"limit": maximum}
    if project_id:
        params["projectId"] = _resource(project_id, "project_id")
    clean_state = str(state or "").strip().upper()
    if clean_state:
        allowed_states = {"BUILDING", "ERROR", "INITIALIZING", "QUEUED", "READY", "CANCELED", "BLOCKED"}
        if clean_state not in allowed_states:
            raise ValueError("Vercel deployment state 无效")
        params["state"] = clean_state
    clean_target = str(target or "").strip().lower()
    if clean_target:
        if clean_target not in {"preview", "production", "staging"}:
            raise ValueError("Vercel deployment target 无效")
        params["target"] = clean_target
    if branch:
        params["branch"] = str(branch).strip()[:300]
    payload = _request(owner_id, "GET", "/v7/deployments", params=params, context="Vercel 部署列表")
    if not isinstance(payload, dict):
        raise VercelPluginError("Vercel 部署列表返回格式无效")
    rows = payload.get("deployments") if isinstance(payload.get("deployments"), list) else []
    pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
    return {
        "plugin_id": "vercel",
        "deployments": [_deployment_summary(item) for item in rows if isinstance(item, dict)],
        "count": len(rows),
        "pagination": {key: pagination.get(key) for key in ("count", "next", "prev") if key in pagination},
    }


def read_vercel_deployment(owner_id: str, deployment_id: str) -> dict[str, Any]:
    deployment = _resource(deployment_id, "deployment_id")
    payload = _request(
        owner_id,
        "GET",
        f"/v13/deployments/{quote(deployment, safe='')}",
        params={"withGitRepoInfo": "true"},
        context="Vercel 部署读取",
    )
    if not isinstance(payload, dict):
        raise VercelPluginError("Vercel 部署读取返回格式无效")
    return {"plugin_id": "vercel", "deployment": _deployment_summary(payload)}


def redeploy_vercel_preview(owner_id: str, project_id: str, deployment_id: str) -> dict[str, Any]:
    project = _resource(project_id, "project_id")
    deployment = _resource(deployment_id, "deployment_id")
    source_payload = _request(
        owner_id,
        "GET",
        f"/v13/deployments/{quote(deployment, safe='')}",
        context="Vercel 源部署验证",
    )
    if not isinstance(source_payload, dict):
        raise VercelPluginError("Vercel 源部署返回格式无效")
    source_project = str(source_payload.get("projectId") or "")
    if source_project != project:
        raise ValueError("源 deployment 不属于指定 project，已拒绝重新部署")
    name = str(source_payload.get("name") or "").strip()
    if not name:
        raise VercelPluginError("源 deployment 缺少项目名称，无法安全重新部署")
    payload = _request(
        owner_id,
        "POST",
        "/v13/deployments",
        json_body={"name": name[:240], "project": project, "deploymentId": deployment},
        context="Vercel Preview 重新部署",
    )
    if not isinstance(payload, dict):
        raise VercelPluginError("Vercel Preview 重新部署返回格式无效")
    result = _deployment_summary(payload)
    # The REST API defines omitted target as preview for this form. Fail closed if the provider
    # nevertheless reports that production traffic was targeted.
    if str(result.get("target") or "preview").lower() == "production":
        raise VercelPluginError("Vercel 返回了意外的 production target；请立即在 Vercel 控制台核查部署")
    return {"plugin_id": "vercel", "action": "preview_redeployment_created", "deployment": result}


def promote_vercel_production(owner_id: str, project_id: str, deployment_id: str) -> dict[str, Any]:
    project = _resource(project_id, "project_id")
    deployment = _resource(deployment_id, "deployment_id")
    source = read_vercel_deployment(owner_id, deployment)["deployment"]
    if str(source.get("project_id") or "") != project:
        raise ValueError("deployment 不属于指定 project，已拒绝生产提升")
    if str(source.get("state") or "").upper() != "READY":
        raise ValueError("只有 READY 的 Vercel deployment 才允许提升到生产")
    _request(
        owner_id,
        "POST",
        f"/v10/projects/{quote(project, safe='')}/promote/{quote(deployment, safe='')}",
        context="Vercel 生产提升",
    )
    return {"plugin_id": "vercel", "action": "production_promote_requested", "project_id": project, "deployment_id": deployment}


def rollback_vercel_production(owner_id: str, project_id: str, deployment_id: str) -> dict[str, Any]:
    project = _resource(project_id, "project_id")
    deployment = _resource(deployment_id, "deployment_id")
    source = read_vercel_deployment(owner_id, deployment)["deployment"]
    if str(source.get("project_id") or "") != project:
        raise ValueError("deployment 不属于指定 project，已拒绝生产回滚")
    _request(
        owner_id,
        "POST",
        f"/v1/projects/{quote(project, safe='')}/rollback/{quote(deployment, safe='')}",
        context="Vercel 生产回滚",
    )
    return {"plugin_id": "vercel", "action": "production_rollback_requested", "project_id": project, "deployment_id": deployment}
